"""The bounded run loop: what the time budget does, what a lost fence does, and how a run is opened once.

Pure-unit coverage with no database, on the same seam as `test_jobs_lease.py`: `RecordingSession`
answers each statement by the `-- <name>` marker it opens with.
"""

# ruff: noqa: PLR2004, ARG001

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import OperationalError

from agri_data_service.jobs.registry import (
    JobDefinitionSpec,
    JobHandlerOutcome,
    JobHandlerRegistry,
    JobSpecificationError,
    JobWorkItemSpec,
    RetryPolicy,
    UnknownJobHandlerError,
)
from agri_data_service.jobs.worker import (
    CANCELLED_RELEASE_REASON,
    JOB_EVENT_SERVICE,
    PREFLIGHT_MISSING_RELATIONS_STOP_REASON,
    SHUTDOWN_RELEASE_REASON,
    SLICE_FINISHED_EVENT_CODE,
    JobDefinitionNotFoundError,
    JobDefinitionRecord,
    ShutdownSignal,
    ensure_job_definition,
    open_job_run,
    preflight_required_relations,
    run_job_slice,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agri_data_service.jobs.registry import JobInvocation

_MARKER = re.compile(r"^--\s+(\w+)\s*$", re.MULTILINE)

DEFINITION_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
JOB_RUN_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
WORK_ITEM_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
ATTEMPT_ID = uuid.UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
LEASE_EXPIRES_AT = datetime(2026, 8, 7, 12, 5, tzinfo=UTC)

HANDLER_TOKEN = "agri.backfill.firms"
DEFINITION_NAME = "agri.backfill.firms"


class FakeResult:
    """The narrow slice of SQLAlchemy's Result the runtime actually uses."""

    def __init__(self, rows: Sequence[Mapping[str, object]]) -> None:
        self._rows = list(rows)

    def mappings(self) -> FakeResult:
        """Return self, because the fake already yields mappings."""
        return self

    def first(self) -> Mapping[str, object] | None:
        """The first row, or None when the statement matched nothing."""
        return self._rows[0] if self._rows else None

    def all(self) -> list[Mapping[str, object]]:
        """Every row the statement returned."""
        return list(self._rows)


class AbortedTransactionError(RuntimeError):
    """Stands in for asyncpg's InFailedSQLTransactionError: every statement raises until a rollback."""


class RecordingSession:
    """An AsyncSession stand-in that records every statement and answers it by its `-- marker` comment."""

    def __init__(self) -> None:
        """Start with no scripted answers; an unscripted statement returns no rows."""
        self.statements: list[tuple[str, dict[str, object]]] = []
        self.commits = 0
        self.rollbacks = 0
        # A handler shares this session, so it can leave the transaction aborted. Postgres then refuses
        # every subsequent statement until a rollback, which is what makes the runtime's ordering matter.
        self.aborted = False
        self._answers: dict[str, list[list[Mapping[str, object]]]] = {}

    def answer(self, marker: str, *responses: Sequence[Mapping[str, object]]) -> None:
        """Answer statements carrying `-- marker` with these row sets in order; the last one repeats."""
        self._answers[marker] = [list(response) for response in responses]

    async def execute(self, statement: object, parameters: Mapping[str, object] | None = None) -> FakeResult:
        """Record the statement and answer it from the script, refusing everything while aborted."""
        if self.aborted:
            raise AbortedTransactionError("current transaction is aborted, commands ignored until rollback")
        sql = str(statement)
        self.statements.append((sql, dict(parameters or {})))
        responses = self._answers.get(self.marker_of(sql) or "")
        if not responses:
            return FakeResult([])
        rows = responses[0] if len(responses) == 1 else responses.pop(0)
        return FakeResult(rows)

    async def commit(self) -> None:
        """Count a commit."""
        self.commits += 1

    async def rollback(self) -> None:
        """Count a rollback, which is the only thing that clears an aborted transaction."""
        self.rollbacks += 1
        self.aborted = False

    @staticmethod
    def marker_of(sql: str) -> str | None:
        """The `-- <name>` marker a statement opens with."""
        found = _MARKER.search(sql)
        return None if found is None else found.group(1)

    def markers(self) -> list[str]:
        """Every statement's marker, in the order they were executed."""
        return [marker for sql, _ in self.statements if (marker := self.marker_of(sql)) is not None]

    def emitted(self, marker: str) -> bool:
        """True when at least one statement carrying this marker was executed."""
        return marker in self.markers()

    def sql_for(self, marker: str) -> str:
        """The SQL text of the first statement carrying this marker."""
        for sql, _ in self.statements:
            if self.marker_of(sql) == marker:
                return sql
        raise AssertionError(f"no statement carrying marker {marker!r} was executed")

    def parameters_for(self, marker: str) -> dict[str, object]:
        """The bound parameters of the first statement carrying this marker."""
        for sql, parameters in self.statements:
            if self.marker_of(sql) == marker:
                return parameters
        raise AssertionError(f"no statement carrying marker {marker!r} was executed")


class ManualClock:
    """A monotonic clock a test advances by hand, so a time budget is exhausted deterministically."""

    def __init__(self) -> None:
        """Start at zero; nothing moves until a test says so."""
        self.value = 0.0

    def __call__(self) -> float:
        """Read the clock without advancing it."""
        return self.value

    def advance(self, seconds: float) -> None:
        """Move the clock forward."""
        self.value += seconds


DEFINITION = JobDefinitionRecord(
    id=DEFINITION_ID,
    name=DEFINITION_NAME,
    version="2026-08-07",
    handler=HANDLER_TOKEN,
    queue_name="default",
    concurrency_key=None,
    max_attempts=5,
    lease_seconds=300,
    time_budget_seconds=240,
    retry_policy=RetryPolicy(),
    parameters={"bbox": "-125,42,-111,49"},
)


def _definition_row(**overrides: object) -> dict[str, object]:
    """One row shaped like the definition SELECT's column list."""
    row: dict[str, object] = {
        "id": DEFINITION_ID,
        "name": DEFINITION_NAME,
        "version": "2026-08-07",
        "handler": HANDLER_TOKEN,
        "queue_name": "default",
        "concurrency_key": None,
        "max_attempts": 5,
        "lease_seconds": 300,
        "time_budget_seconds": 240,
        "retry_policy": {},
        "parameters": {"bbox": "-125,42,-111,49"},
    }
    row.update(overrides)
    return row


def _claim_row(**overrides: object) -> dict[str, object]:
    """One row shaped like the claim statement's RETURNING clause."""
    row: dict[str, object] = {
        "id": WORK_ITEM_ID,
        "job_run_id": JOB_RUN_ID,
        "shard_key": "firms:2003-04-01",
        "kind": "history_window",
        "payload": {"window_start": "2003-04-01"},
        "fencing_token": 7,
        "attempt_count": 2,
        "max_attempts": 5,
        "checkpoint_sequence": 3,
        "progress_fraction": 0.4,
        "lease_expires_at": LEASE_EXPIRES_AT,
    }
    row.update(overrides)
    return row


def _rollup_row(**overrides: object) -> dict[str, object]:
    """One row shaped like the rollup statement's RETURNING clause."""
    row: dict[str, object] = {
        "status": "running",
        "total_work_items": 4,
        "succeeded_work_items": 1,
        "failed_work_items": 0,
    }
    row.update(overrides)
    return row


def _slice_session(*, claims: int = 1, **claim_overrides: object) -> RecordingSession:
    """A session scripted for a slice that can claim `claims` shards and then runs dry."""
    session = RecordingSession()
    session.answer("load_job_definition", [_definition_row()])
    session.answer("select_open_job_run", [{"id": JOB_RUN_ID}])
    session.answer("claim_work_item", *([[_claim_row(**claim_overrides)]] * claims), [])
    session.answer("open_attempt", [{"id": ATTEMPT_ID}])
    session.answer("mark_work_item_running", [{"id": WORK_ITEM_ID}])
    session.answer("refresh_job_run_rollup", [_rollup_row()])
    return session


def _registry(handler: object) -> JobHandlerRegistry:
    """A registry holding exactly the one handler under test."""
    registry = JobHandlerRegistry()
    registry.register(HANDLER_TOKEN, handler)  # type: ignore[arg-type]
    return registry


async def test_a_slice_drives_a_claimed_shard_to_completion_and_reports_it() -> None:
    session = _slice_session()
    session.answer("close_attempt_succeeded", [{"id": ATTEMPT_ID}])
    session.answer("complete_work_item", [{"id": WORK_ITEM_ID}])
    session.answer("increment_run_succeeded", [{"succeeded_work_items": 1}])

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        return JobHandlerOutcome.completed()

    summary = await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    assert (summary.claimed, summary.succeeded) == (1, 1)
    assert summary.stop_reason == "no_claimable_work"
    assert summary.run_status == "running"
    assert session.emitted("complete_work_item") is True


async def test_a_handler_is_handed_the_shards_payload_the_definitions_parameters_and_a_resume_cursor() -> None:
    session = _slice_session()
    session.answer("latest_checkpoint_cursor", [{"cursor": {"day": "2003-04-11"}}])
    session.answer("close_attempt_succeeded", [{"id": ATTEMPT_ID}])
    session.answer("complete_work_item", [{"id": WORK_ITEM_ID}])
    session.answer("increment_run_succeeded", [{"succeeded_work_items": 1}])
    seen: list[JobInvocation] = []

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        seen.append(invocation)
        return JobHandlerOutcome.completed()

    await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    assert len(seen) == 1
    assert seen[0].shard_key == "firms:2003-04-01"
    assert seen[0].payload == {"window_start": "2003-04-01"}
    assert seen[0].parameters == {"bbox": "-125,42,-111,49"}
    assert seen[0].cursor == {"day": "2003-04-11"}


async def test_the_budget_is_checked_before_claiming_so_an_exhausted_slice_claims_nothing_at_all() -> None:
    session = _slice_session()

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        raise AssertionError("the handler must never run when the budget is already spent")

    summary = await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        budget_seconds=0.0,
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    assert summary.claimed == 0
    assert summary.stop_reason == "time_budget_exhausted"
    assert session.emitted("claim_work_item") is False


async def test_a_progressed_outcome_checkpoints_and_keeps_the_same_shard_while_budget_remains() -> None:
    session = _slice_session()
    session.answer("advance_checkpoint_sequence", [{"checkpoint_sequence": 4}], [{"checkpoint_sequence": 5}])
    session.answer("append_checkpoint", [{"id": uuid.uuid4()}])
    session.answer("close_attempt_succeeded", [{"id": ATTEMPT_ID}])
    session.answer("complete_work_item", [{"id": WORK_ITEM_ID}])
    session.answer("increment_run_succeeded", [{"succeeded_work_items": 1}])
    cursors: list[object] = []

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        cursors.append(invocation.cursor)
        if len(cursors) == 1:
            return JobHandlerOutcome.progressed({"day": "2003-04-06"}, progress_fraction=0.5)
        return JobHandlerOutcome.completed()

    summary = await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    assert summary.claimed == 1
    assert summary.succeeded == 1
    # The second call into the same handler resumes from the cursor the first one checkpointed.
    assert cursors == [None, {"day": "2003-04-06"}]
    assert session.markers().count("append_checkpoint") == 1


async def test_a_budget_that_runs_out_mid_shard_parks_the_shard_rather_than_leaving_its_lease_to_rot() -> None:
    session = _slice_session()
    session.answer("advance_checkpoint_sequence", [{"checkpoint_sequence": 4}])
    session.answer("append_checkpoint", [{"id": uuid.uuid4()}])
    session.answer("close_attempt_deferred", [{"id": ATTEMPT_ID}])
    session.answer("defer_work_item", [{"id": WORK_ITEM_ID}])
    clock = ManualClock()

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        clock.advance(500.0)
        return JobHandlerOutcome.progressed({"day": "2003-04-06"}, progress_fraction=0.5)

    summary = await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        budget_seconds=100.0,
        registry=_registry(handler),
        monotonic=clock,
    )
    assert summary.yielded == 1
    assert summary.stop_reason == "time_budget_exhausted"
    # The cursor was checkpointed BEFORE the shard was parked, so the next tick resumes from it.
    assert session.markers().index("append_checkpoint") < session.markers().index("defer_work_item")
    assert session.parameters_for("defer_work_item")["resume_at"] is None


async def test_a_handler_that_raises_fails_only_its_own_shard_and_the_slice_keeps_going() -> None:
    session = _slice_session(claims=2)
    session.answer("close_attempt_failed", [{"id": ATTEMPT_ID}])
    session.answer("retry_work_item", [{"id": WORK_ITEM_ID}])
    session.answer("close_attempt_succeeded", [{"id": ATTEMPT_ID}])
    session.answer("complete_work_item", [{"id": WORK_ITEM_ID}])
    session.answer("increment_run_succeeded", [{"succeeded_work_items": 1}])
    calls: list[int] = []

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("upstream returned 503 for https://firms.example/api?MAP_KEY=abc123")
        return JobHandlerOutcome.completed()

    summary = await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    assert (summary.claimed, summary.retried, summary.succeeded) == (2, 1, 1)
    recorded = session.parameters_for("close_attempt_failed")
    assert recorded["failure_class"] == "RuntimeError"
    assert "MAP_KEY" not in str(recorded["error_summary"])


async def test_the_last_attempt_dead_letters_the_shard_instead_of_reporting_it_done() -> None:
    session = _slice_session(attempt_count=5)
    session.answer("close_attempt_failed", [{"id": ATTEMPT_ID}])
    session.answer("dead_letter_work_item", [{"id": WORK_ITEM_ID}])
    session.answer("increment_run_failed", [{"failed_work_items": 1}])

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        return JobHandlerOutcome.failed("UpstreamHttpError", "upstream returned 503")

    summary = await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    assert summary.dead_lettered == 1
    assert summary.succeeded == 0
    assert session.emitted("retry_work_item") is False
    assert session.emitted("increment_run_succeeded") is False


async def test_a_deferred_outcome_parks_the_shard_and_is_counted_apart_from_a_failure() -> None:
    session = _slice_session()
    session.answer("close_attempt_deferred", [{"id": ATTEMPT_ID}])
    session.answer("defer_work_item", [{"id": WORK_ITEM_ID}])
    resume_at = datetime(2026, 8, 13, 14, 0, tzinfo=UTC)

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        return JobHandlerOutcome.deferred(resume_at, "USDM publishes on Thursdays")

    summary = await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    assert (summary.deferred, summary.retried, summary.dead_lettered) == (1, 0, 0)
    assert session.parameters_for("defer_work_item")["resume_at"] == resume_at


async def test_a_heartbeat_that_lost_the_fence_abandons_the_shard_without_marking_it_failed() -> None:
    session = _slice_session()
    session.answer("close_attempt_lost", [{"id": ATTEMPT_ID}])
    heartbeats: list[bool] = []

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        heartbeats.append(await invocation.heartbeat())
        return JobHandlerOutcome.completed()

    summary = await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    assert heartbeats == [False]
    assert summary.abandoned == 1
    assert (summary.succeeded, summary.retried, summary.dead_lettered) == (0, 0, 0)
    assert session.emitted("complete_work_item") is False
    assert session.emitted("close_attempt_failed") is False
    assert session.emitted("close_attempt_lost") is True


async def test_a_checkpoint_that_lost_the_fence_abandons_the_shard_rather_than_failing_it() -> None:
    session = _slice_session()
    session.answer("close_attempt_lost", [{"id": ATTEMPT_ID}])

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        return JobHandlerOutcome.progressed({"day": "2003-04-06"}, progress_fraction=0.5)

    summary = await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    assert summary.abandoned == 1
    assert session.emitted("append_checkpoint") is False
    assert session.emitted("close_attempt_failed") is False


async def test_a_handler_that_left_the_shared_transaction_aborted_still_fails_only_its_own_shard() -> None:
    session = _slice_session(claims=2)
    session.answer("close_attempt_failed", [{"id": ATTEMPT_ID}])
    session.answer("retry_work_item", [{"id": WORK_ITEM_ID}])
    session.answer("close_attempt_succeeded", [{"id": ATTEMPT_ID}])
    session.answer("complete_work_item", [{"id": WORK_ITEM_ID}])
    session.answer("increment_run_succeeded", [{"succeeded_work_items": 1}])
    calls: list[int] = []

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        calls.append(1)
        if len(calls) == 1:
            # writer.py resolves a layer id outside its own rollback, so a statement timeout there
            # leaves the session this handler shares with the runtime in InFailedSQLTransaction.
            session.aborted = True
            raise OperationalError("SELECT id FROM geo.layers WHERE slug = ...", {}, Exception("timeout"))
        return JobHandlerOutcome.completed()

    summary = await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    # Without a rollback before the failure write, close_attempt_failed raises a SECOND time, that
    # exception escapes run_job_slice, and the shard is stranded 'running' behind a live lease.
    assert (summary.claimed, summary.retried, summary.succeeded) == (2, 1, 1)
    assert session.emitted("close_attempt_failed") is True
    assert session.emitted("refresh_job_run_rollup") is True


async def test_a_failure_written_after_an_aborted_transaction_names_the_error_class_not_its_statement() -> None:
    session = _slice_session()
    session.answer("close_attempt_failed", [{"id": ATTEMPT_ID}])
    session.answer("retry_work_item", [{"id": WORK_ITEM_ID}])

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        session.aborted = True
        raise OperationalError("INSERT INTO geo.features ...", {"payload": "secret"}, Exception("boom"))

    await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    recorded = session.parameters_for("close_attempt_failed")
    assert recorded["failure_class"] == "OperationalError"
    # `failure_condition_name` unwraps one `.orig` level past the dialect wrapper (see
    # jobs/AGENTS.md "failure_summary names the real condition now, not the dialect's tautology"):
    # this OperationalError's `orig` is the bare `Exception("boom")` the test raises it with, and
    # that has no `__cause__` of its own, so the pair reports the dialect wrapper alongside `orig`'s
    # own class rather than the class-name tautology the old, unwrap-free assertion expected.
    assert recorded["error_summary"] == "job step failed (OperationalError: Exception)"


async def test_the_statement_timeout_is_re_pinned_after_a_handler_commits_the_shared_session() -> None:
    session = _slice_session()
    session.answer("close_attempt_succeeded", [{"id": ATTEMPT_ID}])
    session.answer("complete_work_item", [{"id": WORK_ITEM_ID}])
    session.answer("increment_run_succeeded", [{"succeeded_work_items": 1}])
    after_commit: list[int] = []

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        # ingest/writer.py commits this session once per batch, and SET LOCAL dies with its transaction.
        await session.commit()
        after_commit.append(len(session.statements))
        return JobHandlerOutcome.completed()

    await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    assert session.markers()[after_commit[0]] == "statement_timeout"


async def test_a_heartbeat_re_pins_the_statement_timeout_before_it_touches_the_ledger() -> None:
    session = _slice_session()
    session.answer("extend_work_item_lease", [{"id": WORK_ITEM_ID}])
    session.answer("extend_attempt_heartbeat", [{"id": ATTEMPT_ID}])
    session.answer("close_attempt_succeeded", [{"id": ATTEMPT_ID}])
    session.answer("complete_work_item", [{"id": WORK_ITEM_ID}])
    session.answer("increment_run_succeeded", [{"succeeded_work_items": 1}])

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        await session.commit()
        assert await invocation.heartbeat() is True
        return JobHandlerOutcome.completed()

    await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    markers = session.markers()
    assert markers[markers.index("extend_work_item_lease") - 1] == "statement_timeout"


async def test_a_deferred_outcome_checkpoints_the_chunks_it_did_walk_before_parking_the_shard() -> None:
    session = _slice_session()
    session.answer("advance_checkpoint_sequence", [{"checkpoint_sequence": 4}])
    session.answer("append_checkpoint", [{"id": uuid.uuid4()}])
    session.answer("close_attempt_deferred", [{"id": ATTEMPT_ID}])
    session.answer("defer_work_item", [{"id": WORK_ITEM_ID}])
    resume_at = datetime(2026, 8, 8, 6, 0, tzinfo=UTC)

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        return JobHandlerOutcome.deferred(
            resume_at,
            "upstream said come back at 06:00",
            cursor={"chunk": 5},
            progress_fraction=0.8,
        )

    summary = await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    assert summary.deferred == 1
    # Without this the four chunks it walked before upstream said "later" are thrown away and re-walked
    # on every single deferral, forever.
    markers = session.markers()
    assert markers.index("append_checkpoint") < markers.index("defer_work_item")
    assert session.parameters_for("append_checkpoint")["cursor"] == '{"chunk":5}'


async def test_a_handler_that_yields_for_budget_parks_the_shard_without_charging_it_an_attempt() -> None:
    session = _slice_session(claims=2)
    session.answer("advance_checkpoint_sequence", [{"checkpoint_sequence": 4}])
    session.answer("append_checkpoint", [{"id": uuid.uuid4()}])
    session.answer("close_attempt_deferred", [{"id": ATTEMPT_ID}])
    session.answer("defer_work_item", [{"id": WORK_ITEM_ID}])

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        if invocation.has_budget_for(10_000.0):
            raise AssertionError("a step larger than the whole budget must never look affordable")
        return JobHandlerOutcome.yielded(cursor={"chunk": 3}, progress_fraction=0.6)

    summary = await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    assert summary.yielded == 1
    assert (summary.retried, summary.dead_lettered, summary.deferred) == (0, 0, 0)
    # Stopping for the clock is not failing: the park raises the ceiling rather than spending it, up to
    # the consecutive-park bound past which a shard that can only ever park stops being protected.
    assert "max_attempts = item.max_attempts + CASE" in session.sql_for("defer_work_item")
    assert session.emitted("close_attempt_failed") is False
    assert session.parameters_for("defer_work_item")["resume_at"] is None


async def test_a_handler_yield_ends_the_slice_rather_than_claiming_a_shard_it_also_cannot_finish() -> None:
    session = _slice_session(claims=2)
    session.answer("advance_checkpoint_sequence", [{"checkpoint_sequence": 4}])
    session.answer("append_checkpoint", [{"id": uuid.uuid4()}])
    session.answer("close_attempt_deferred", [{"id": ATTEMPT_ID}])
    session.answer("defer_work_item", [{"id": WORK_ITEM_ID}])

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        return JobHandlerOutcome.yielded(cursor={"chunk": 3}, progress_fraction=0.6)

    summary = await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    assert summary.claimed == 1
    assert summary.stop_reason == "time_budget_exhausted"


async def test_a_handler_is_told_the_budget_left_in_this_slice_and_is_asked_again_on_every_call() -> None:
    session = _slice_session()
    session.answer("advance_checkpoint_sequence", [{"checkpoint_sequence": 4}])
    session.answer("append_checkpoint", [{"id": uuid.uuid4()}])
    session.answer("close_attempt_succeeded", [{"id": ATTEMPT_ID}])
    session.answer("complete_work_item", [{"id": WORK_ITEM_ID}])
    session.answer("increment_run_succeeded", [{"succeeded_work_items": 1}])
    clock = ManualClock()
    remaining: list[float] = []

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        remaining.append(invocation.seconds_remaining)
        clock.advance(40.0)
        if len(remaining) == 1:
            return JobHandlerOutcome.progressed({"day": "2003-04-06"}, progress_fraction=0.5)
        return JobHandlerOutcome.completed()

    await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        budget_seconds=100.0,
        registry=_registry(handler),
        monotonic=clock,
    )
    # The figure is a fresh snapshot per call, not a number that goes stale for the whole shard.
    assert remaining == [100.0, 60.0]


async def test_a_slice_budget_the_lease_cannot_outlive_is_refused_as_a_self_inflicted_fence_loss() -> None:
    session = _slice_session()

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        raise AssertionError("no shard may be claimed under a budget the lease cannot outlive")

    # The definition declares lease_seconds=300; budget_seconds bypasses JobDefinitionSpec's own check.
    with pytest.raises(JobSpecificationError, match="lease_seconds"):
        await run_job_slice(
            session,
            definition_name=DEFINITION_NAME,
            worker_id="agri-worker-1",
            budget_seconds=300.0,
            registry=_registry(handler),
            monotonic=ManualClock(),
        )


async def test_the_metrics_of_every_step_reach_the_attempt_even_when_the_shard_finally_fails() -> None:
    session = _slice_session()
    session.answer("advance_checkpoint_sequence", [{"checkpoint_sequence": 4}])
    session.answer("append_checkpoint", [{"id": uuid.uuid4()}])
    session.answer("close_attempt_failed", [{"id": ATTEMPT_ID}])
    session.answer("retry_work_item", [{"id": WORK_ITEM_ID}])
    calls: list[int] = []

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        calls.append(1)
        if len(calls) == 1:
            return JobHandlerOutcome.progressed(
                {"chunk": 2},
                progress_fraction=0.4,
                metrics={"window_records_seen": 900, "chunks_walked": 1},
            )
        return JobHandlerOutcome.failed("UpstreamHttpError", "upstream returned 503", metrics={"chunks_walked": 2})

    summary = await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    assert summary.retried == 1
    # What a failed window did manage is exactly what an operator needs to triage it, and a progressed
    # step's counters would otherwise be dropped on the floor.
    written = json.loads(str(session.parameters_for("close_attempt_failed")["metrics"]))
    assert written == {"window_records_seen": 900, "chunks_walked": 2}


async def test_a_handler_supplied_failure_reason_is_redacted_even_though_no_exception_ever_saw_it() -> None:
    session = _slice_session()
    session.answer("close_attempt_failed", [{"id": ATTEMPT_ID}])
    session.answer("retry_work_item", [{"id": WORK_ITEM_ID}])

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        # A reason a handler composes itself never passes through failure_summary, so the chokepoint
        # has to be fail_work_item rather than the exception path.
        return JobHandlerOutcome.failed(
            "UpstreamHttpError",
            "GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/abc123KEY/VIIRS returned 503",
        )

    await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    assert "abc123KEY" not in str(session.parameters_for("close_attempt_failed")["error_summary"])
    assert "abc123KEY" not in str(session.parameters_for("retry_work_item")["error_summary"])


async def test_a_shard_whose_claim_could_not_be_marked_running_is_abandoned_not_failed() -> None:
    session = _slice_session()
    session.answer("mark_work_item_running", [])
    session.answer("close_attempt_lost", [{"id": ATTEMPT_ID}])

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        raise AssertionError("the handler must never run once the fence is already lost")

    summary = await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    assert summary.abandoned == 1


async def test_a_slice_reclaims_expired_leases_before_it_claims_anything_of_its_own() -> None:
    session = _slice_session(claims=0)
    session.answer(
        "reclaim_expired_leases",
        [{"id": WORK_ITEM_ID, "job_run_id": JOB_RUN_ID, "status": "retry_wait"}],
    )
    session.answer("close_lost_attempts", [{"id": ATTEMPT_ID}])

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        return JobHandlerOutcome.completed()

    summary = await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    assert summary.reclaimed == 1
    markers = session.markers()
    assert markers.index("reclaim_expired_leases") < markers.index("claim_work_item")
    # Scoped to the DEFINITION and not to the one run this tick drives: a lane mints a second run every
    # time its floor is lowered, and a shard stranded behind a dead lease in that sibling run could
    # otherwise never be reaped by any tick at all, because every tick reaps only the run it drives.
    reclaim_parameters = session.parameters_for("reclaim_expired_leases")
    assert reclaim_parameters["job_definition_id"] == DEFINITION_ID
    assert reclaim_parameters["job_run_id"] is None


async def test_every_tick_writes_one_durable_heartbeat_so_an_idle_lane_reads_differently_from_a_dead_one() -> None:
    session = _slice_session(claims=0)

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        return JobHandlerOutcome.completed()

    await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    # Exactly one, because a tick that claims nothing is precisely the tick that must still leave a row:
    # without it a finished lane, a lane whose cron was never created and a container that crashes on
    # startup are the same silence. More than one would make the heartbeat chatty rather than useful.
    assert session.markers().count("write_slice_event") == 1
    parameters = session.parameters_for("write_slice_event")
    assert parameters["event_code"] == SLICE_FINISHED_EVENT_CODE
    assert parameters["service"] == JOB_EVENT_SERVICE
    assert parameters["job_run_id"] == JOB_RUN_ID
    assert json.loads(str(parameters["progress"]))["stop_reason"] == "no_claimable_work"
    # The queue depth is computed inside the INSERT, so the heartbeat stays a single statement.
    event_sql = session.sql_for("write_slice_event")
    assert "jsonb_build_object(" in event_sql
    assert "outstanding_work_items" in event_sql
    markers = session.markers()
    assert markers.index("refresh_job_run_rollup") < markers.index("write_slice_event")


async def test_a_tick_with_no_open_run_still_writes_its_heartbeat_because_that_is_the_case_that_needs_one() -> None:
    session = RecordingSession()
    session.answer("load_job_definition", [_definition_row()])

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        return JobHandlerOutcome.completed()

    summary = await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    assert summary.stop_reason == "no_open_run"
    assert session.markers().count("write_slice_event") == 1
    # A NULL run id still writes its row: an aggregate with no GROUP BY returns one row over no rows.
    assert session.parameters_for("write_slice_event")["job_run_id"] is None
    assert session.commits == 1


async def test_a_stop_signal_releases_the_shard_in_hand_rather_than_stranding_it_behind_a_live_lease() -> None:
    session = _slice_session(claims=2)
    session.answer("advance_checkpoint_sequence", [{"checkpoint_sequence": 4}])
    session.answer("append_checkpoint", [{"id": uuid.uuid4()}])
    session.answer("close_attempt_deferred", [{"id": ATTEMPT_ID}])
    session.answer("defer_work_item", [{"id": WORK_ITEM_ID}])
    stop = ShutdownSignal()

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        stop.request(SHUTDOWN_RELEASE_REASON)
        return JobHandlerOutcome.progressed({"chunk": 3}, progress_fraction=0.6)

    summary = await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
        stop=stop,
    )
    assert (summary.released, summary.claimed) == (1, 1)
    assert summary.stop_reason == "shutdown_requested"
    # Parked to now() and fenced, so the NEXT container claims it immediately instead of waiting out a
    # lease no living process owns -- and it is not a failure, so no attempt budget is spent on it.
    assert session.parameters_for("defer_work_item")["resume_at"] is None
    assert SHUTDOWN_RELEASE_REASON in str(session.parameters_for("close_attempt_deferred")["metrics"])
    assert session.emitted("close_attempt_failed") is False


async def test_a_stop_signal_between_shards_claims_nothing_more_and_names_why_the_tick_ended() -> None:
    session = _slice_session(claims=1)
    stop = ShutdownSignal()
    stop.request(SHUTDOWN_RELEASE_REASON)

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        raise AssertionError("no shard may be claimed once the container has been told to stop")

    summary = await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
        stop=stop,
    )
    assert summary.claimed == 0
    assert summary.stop_reason == "shutdown_requested"
    assert session.emitted("claim_work_item") is False
    # The rollup and the heartbeat still run: a tick that stopped early is still a tick that happened.
    assert session.emitted("write_slice_event") is True


async def test_a_cancelled_handler_releases_its_shard_before_the_cancellation_finishes_unwinding() -> None:
    session = _slice_session()
    session.answer("close_attempt_deferred", [{"id": ATTEMPT_ID}])
    session.answer("defer_work_item", [{"id": WORK_ITEM_ID}])

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        raise asyncio.CancelledError

    # CancelledError derives from BaseException, so `except Exception` never saw it and the slice used to
    # unwind with the shard still 'running' behind a live lease.
    with pytest.raises(asyncio.CancelledError):
        await run_job_slice(
            session,
            definition_name=DEFINITION_NAME,
            worker_id="agri-worker-1",
            registry=_registry(handler),
            monotonic=ManualClock(),
        )
    assert session.emitted("defer_work_item") is True
    assert session.parameters_for("defer_work_item")["resume_at"] is None
    assert CANCELLED_RELEASE_REASON in str(session.parameters_for("close_attempt_deferred")["metrics"])
    # Released, never failed -- and never swallowed: the cancellation still propagates.
    assert session.emitted("close_attempt_failed") is False


async def test_a_definition_whose_handler_token_resolves_to_nothing_fails_loudly() -> None:
    session = _slice_session()
    with pytest.raises(UnknownJobHandlerError, match=re.escape(HANDLER_TOKEN)):
        await run_job_slice(
            session,
            definition_name=DEFINITION_NAME,
            worker_id="agri-worker-1",
            registry=JobHandlerRegistry(),
            monotonic=ManualClock(),
        )


async def test_an_unknown_definition_name_is_refused_by_name_rather_than_doing_nothing_quietly() -> None:
    session = RecordingSession()

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        return JobHandlerOutcome.completed()

    with pytest.raises(JobDefinitionNotFoundError, match=re.escape(DEFINITION_NAME)):
        await run_job_slice(
            session,
            definition_name=DEFINITION_NAME,
            worker_id="agri-worker-1",
            registry=_registry(handler),
            monotonic=ManualClock(),
        )


async def test_a_definition_with_no_open_run_reports_that_rather_than_inventing_one() -> None:
    session = RecordingSession()
    session.answer("load_job_definition", [_definition_row()])

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        return JobHandlerOutcome.completed()

    summary = await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    assert summary.stop_reason == "no_open_run"
    assert summary.job_run_id is None
    assert session.emitted("claim_work_item") is False


async def test_a_preflight_refusal_names_what_is_missing_and_opens_no_run() -> None:
    session = RecordingSession()
    session.answer(
        "check_relations_exist",
        [{"qualified_name": "agri.matview_refresh_state", "relation_exists": False}],
    )

    summary = await preflight_required_relations(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        required_relations=["agri.matview_refresh_state"],
    )

    assert summary is not None
    assert summary.stop_reason == PREFLIGHT_MISSING_RELATIONS_STOP_REASON
    assert summary.job_run_id is None
    assert summary.preflight_missing_relations == ("agri.matview_refresh_state",)
    # No run opened, no work item minted, no attempt claimed -- there is nothing here to retry or
    # dead-letter, which is the whole point.
    assert session.emitted("insert_job_run") is False
    assert session.emitted("claim_work_item") is False
    # The refusal is still durable: one heartbeat row, committed, so a lane that refuses on every
    # tick still reads as ticking rather than looking identical to a container that never started.
    assert session.markers().count("write_slice_event") == 1
    assert session.commits == 1
    written = json.loads(session.parameters_for("write_slice_event")["progress"])
    assert written["stop_reason"] == PREFLIGHT_MISSING_RELATIONS_STOP_REASON
    assert written["preflight_missing_relations"] == ["agri.matview_refresh_state"]
    assert session.parameters_for("write_slice_event")["severity"] == "error"


async def test_a_preflight_pass_returns_none_and_touches_nothing_durable() -> None:
    session = RecordingSession()
    session.answer(
        "check_relations_exist",
        [{"qualified_name": "agri.matview_refresh_state", "relation_exists": True}],
    )

    summary = await preflight_required_relations(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        required_relations=["agri.matview_refresh_state"],
    )

    assert summary is None
    assert session.emitted("write_slice_event") is False
    assert session.commits == 0


async def test_a_preflight_refusal_is_distinguishable_from_a_healthy_idle_tick() -> None:
    """Per docs/layer-lane-standard.md §0: a refusal must never render like `no_claimable_work`."""
    session = RecordingSession()
    session.answer(
        "check_relations_exist",
        [{"qualified_name": "agri.matview_refresh_state", "relation_exists": False}],
    )
    refusal = await preflight_required_relations(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        required_relations=["agri.matview_refresh_state"],
    )
    assert refusal is not None
    assert refusal.stop_reason not in {"no_open_run", "no_claimable_work"}
    rendered = json.loads(json.dumps(refusal.to_summary(), sort_keys=True))
    assert rendered["preflight_missing_relations"] == ["agri.matview_refresh_state"]


async def test_a_slice_summary_renders_as_one_json_line_a_cron_log_can_carry() -> None:
    session = _slice_session(claims=0)

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        return JobHandlerOutcome.completed()

    summary = await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    rendered = json.loads(json.dumps(summary.to_summary(), sort_keys=True))
    assert rendered["definition"] == DEFINITION_NAME
    assert rendered["job_run_id"] == str(JOB_RUN_ID)
    assert rendered["stop_reason"] == "no_claimable_work"


async def test_the_run_rollup_assigns_every_counter_in_one_statement_so_the_sum_check_never_sees_a_half_update() -> (
    None
):
    session = _slice_session(claims=0)

    async def handler(invocation: JobInvocation) -> JobHandlerOutcome:
        return JobHandlerOutcome.completed()

    await run_job_slice(
        session,
        definition_name=DEFINITION_NAME,
        worker_id="agri-worker-1",
        registry=_registry(handler),
        monotonic=ManualClock(),
    )
    rollup_sql = session.sql_for("refresh_job_run_rollup")
    for column in ("total_work_items = tally.total", "succeeded_work_items = tally.succeeded"):
        assert column in rollup_sql
    # A terminal status and its completion time must be assigned by the same statement, because
    # ck_job_run_terminal_run_has_completion_time is IMMEDIATE.
    assert "completed_at = CASE" in rollup_sql


async def test_opening_a_run_twice_reuses_the_existing_row_rather_than_minting_a_duplicate() -> None:
    session = RecordingSession()
    session.answer("insert_job_run", [])
    session.answer("select_job_run", [{"id": JOB_RUN_ID, "status": "running", "total_work_items": 2}])
    session.answer("insert_job_work_items", [])
    session.answer("refresh_job_run_rollup", [_rollup_row(total_work_items=2)])
    opened = await open_job_run(
        session,
        DEFINITION,
        logical_run_key="agri.backfill.firms:2003",
        work_items=[JobWorkItemSpec(shard_key="firms:2003-04-01", kind="history_window")],
    )
    assert opened.created is False
    assert opened.added_work_items == 0
    assert opened.job_run_id == JOB_RUN_ID
    assert "ON CONFLICT (logical_run_key) DO NOTHING" in session.sql_for("insert_job_run")


async def test_opening_a_new_run_fans_its_shards_out_idempotently_on_the_run_and_shard_key() -> None:
    session = RecordingSession()
    session.answer("insert_job_run", [{"id": JOB_RUN_ID}])
    session.answer("insert_job_work_items", [{"id": WORK_ITEM_ID}, {"id": uuid.uuid4()}])
    session.answer("refresh_job_run_rollup", [_rollup_row(total_work_items=2)])
    opened = await open_job_run(
        session,
        DEFINITION,
        logical_run_key="agri.backfill.firms:2003",
        work_items=[
            JobWorkItemSpec(shard_key="firms:2003-04-01", kind="history_window", payload={"days": 5}),
            JobWorkItemSpec(shard_key="firms:2003-04-06", kind="history_window", payload={"days": 5}, priority=2),
        ],
    )
    assert opened.created is True
    assert opened.added_work_items == 2
    assert opened.total_work_items == 2
    insert_sql = session.sql_for("insert_job_work_items")
    assert "ON CONFLICT (job_run_id, shard_key) DO NOTHING" in insert_sql
    items = json.loads(str(session.parameters_for("insert_job_work_items")["items"]))
    assert [item["shard_key"] for item in items] == ["firms:2003-04-01", "firms:2003-04-06"]
    assert items[1]["priority"] == 2


async def test_a_fanned_out_shard_seeds_next_attempt_at_so_the_claim_index_can_actually_see_it() -> None:
    session = RecordingSession()
    session.answer("insert_job_run", [{"id": JOB_RUN_ID}])
    session.answer("insert_job_work_items", [{"id": WORK_ITEM_ID}])
    session.answer("refresh_job_run_rollup", [_rollup_row(total_work_items=1)])
    await open_job_run(
        session,
        DEFINITION,
        logical_run_key="agri.backfill.firms:2003",
        work_items=[JobWorkItemSpec(shard_key="firms:2003-04-01", kind="history_window")],
    )
    # Seeding is a convenience, not a correctness requirement -- _CLAIM_WORK_ITEM spells the NULL case
    # out -- but it keeps every row in the one claim-eligibility shape a predicate written without that
    # disjunction can still see. See jobs/AGENTS.md on ix_job_work_item_claim.
    assert session.sql_for("insert_job_work_items").count("COALESCE(item.available_at, now())") == 2


async def test_opening_a_run_with_no_shards_writes_no_work_item_statement_at_all() -> None:
    session = RecordingSession()
    session.answer("insert_job_run", [{"id": JOB_RUN_ID}])
    session.answer("refresh_job_run_rollup", [_rollup_row(total_work_items=0)])
    opened = await open_job_run(session, DEFINITION, logical_run_key="agri.backfill.firms:2003", work_items=[])
    assert opened.added_work_items == 0
    assert session.emitted("insert_job_work_items") is False


async def test_ensuring_a_definition_upserts_by_name_and_version_and_returns_the_parsed_policy() -> None:
    session = RecordingSession()
    session.answer(
        "upsert_job_definition",
        [_definition_row(retry_policy={"initial_backoff_seconds": 15.0, "backoff_multiplier": 3.0})],
    )
    spec = JobDefinitionSpec(
        name=DEFINITION_NAME,
        version="2026-08-07",
        handler=HANDLER_TOKEN,
        retry_policy=RetryPolicy(initial_backoff_seconds=15.0, backoff_multiplier=3.0),
        parameters={"bbox": "-125,42,-111,49"},
    )
    record = await ensure_job_definition(session, spec)
    assert record.id == DEFINITION_ID
    assert record.retry_policy.initial_backoff_seconds == 15.0
    assert record.retry_policy.backoff_multiplier == 3.0
    assert "ON CONFLICT (name, version) DO UPDATE" in session.sql_for("upsert_job_definition")
    written = json.loads(str(session.parameters_for("upsert_job_definition")["retry_policy"]))
    assert written["initial_backoff_seconds"] == 15.0
