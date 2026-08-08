"""The fenced-lease protocol: what each statement must move together, and what happens when the fence moves.

Pure-unit coverage with no database: the seam is `AsyncSession.execute`, and `RecordingSession` answers
each statement by the `-- <name>` marker comment it opens with, so a test names the statement it means.
"""

# ruff: noqa: PLR2004

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import OperationalError

from agri_data_service.ingest.results import failure_reason, redact_secrets
from agri_data_service.jobs.lease import (
    FAILURE_CLASS_MAX_LENGTH,
    ClaimedWorkItem,
    JobCursorError,
    canonical_json,
    claim_work_item,
    complete_work_item,
    cursor_checksum,
    defer_work_item,
    extend_lease,
    fail_work_item,
    failure_summary,
    reclaim_expired_leases,
    record_checkpoint,
    redact_text,
    release_lost_attempt,
)
from agri_data_service.jobs.registry import RetryPolicy

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    # One lease call driven far enough to emit its attempt close, with every later statement unanswered.
    LeaseCall = Callable[["RecordingSession"], Awaitable[object]]

_MARKER = re.compile(r"^--\s+(\w+)\s*$", re.MULTILINE)

WORK_ITEM_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
JOB_RUN_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
ATTEMPT_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
OTHER_WORK_ITEM_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")
LEASE_EXPIRES_AT = datetime(2026, 8, 7, 12, 5, tzinfo=UTC)


class FakeResult:
    """The narrow slice of SQLAlchemy's Result the lease protocol actually uses."""

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


class RecordingSession:
    """An AsyncSession stand-in that records every statement and answers it by its `-- marker` comment."""

    def __init__(self) -> None:
        """Start with no scripted answers; an unscripted statement returns no rows."""
        self.statements: list[tuple[str, dict[str, object]]] = []
        self.commits = 0
        self.rollbacks = 0
        self._answers: dict[str, list[list[Mapping[str, object]]]] = {}

    def answer(self, marker: str, *responses: Sequence[Mapping[str, object]]) -> None:
        """Answer statements carrying `-- marker` with these row sets in order; the last one repeats."""
        self._answers[marker] = [list(response) for response in responses]

    async def execute(self, statement: object, parameters: Mapping[str, object] | None = None) -> FakeResult:
        """Record the statement and answer it from the script."""
        sql = str(statement)
        self.statements.append((sql, dict(parameters or {})))
        marker = self.marker_of(sql)
        responses = self._answers.get(marker or "")
        if not responses:
            return FakeResult([])
        rows = responses[0] if len(responses) == 1 else responses.pop(0)
        return FakeResult(rows)

    async def commit(self) -> None:
        """Count a commit."""
        self.commits += 1

    async def rollback(self) -> None:
        """Count a rollback."""
        self.rollbacks += 1

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


def _claim_row(**overrides: object) -> dict[str, object]:
    """One row shaped like the claim statement's RETURNING clause."""
    row: dict[str, object] = {
        "id": WORK_ITEM_ID,
        "job_run_id": JOB_RUN_ID,
        "shard_key": "firms:2003-04-01",
        "kind": "history_window",
        "payload": {"window_start": "2003-04-01", "window_days": 5},
        "fencing_token": 7,
        "attempt_count": 2,
        "max_attempts": 5,
        "checkpoint_sequence": 3,
        "progress_fraction": 0.4,
        "lease_expires_at": LEASE_EXPIRES_AT,
    }
    row.update(overrides)
    return row


def _claim(**overrides: object) -> ClaimedWorkItem:
    """A claim this worker already holds, so a test can exercise a single fenced write."""
    fields: dict[str, object] = {
        "work_item_id": WORK_ITEM_ID,
        "job_run_id": JOB_RUN_ID,
        "attempt_id": ATTEMPT_ID,
        "worker_id": "agri-worker-1",
        "shard_key": "firms:2003-04-01",
        "kind": "history_window",
        "payload": {"window_start": "2003-04-01"},
        "cursor": None,
        "fencing_token": 7,
        "attempt_number": 2,
        "max_attempts": 5,
        "checkpoint_sequence": 3,
        "progress_fraction": 0.4,
        "lease_expires_at": LEASE_EXPIRES_AT,
    }
    fields.update(overrides)
    return ClaimedWorkItem(**fields)  # type: ignore[arg-type]


def _claimable_session(**row_overrides: object) -> RecordingSession:
    """A session scripted so one claim succeeds and opens its attempt."""
    session = RecordingSession()
    session.answer("claim_work_item", [_claim_row(**row_overrides)])
    session.answer("open_attempt", [{"id": ATTEMPT_ID}])
    return session


async def _complete(session: RecordingSession) -> object:
    """Drive the success landing, which closes the attempt before it touches the item."""
    return await complete_work_item(session, _claim())


async def _fail(session: RecordingSession) -> object:
    """Drive the failure landing, which closes the attempt before it retries or dead-letters the item."""
    return await fail_work_item(
        session,
        _claim(),
        failure_class="UpstreamHttpError",
        reason="upstream returned 503",
        retry_policy=RetryPolicy(),
    )


async def _defer(session: RecordingSession) -> object:
    """Drive the deferral landing, which closes the attempt before it parks the item."""
    return await defer_work_item(session, _claim(), resume_at=None, reason="upstream had nothing")


async def test_a_claim_returns_the_fencing_token_the_update_minted_and_opens_its_attempt_under_that_token() -> None:
    session = _claimable_session()
    claim = await claim_work_item(session, job_run_id=JOB_RUN_ID, worker_id="agri-worker-1", lease_seconds=300)
    assert claim is not None
    assert claim.fencing_token == 7
    assert claim.attempt_id == ATTEMPT_ID
    attempt_parameters = session.parameters_for("open_attempt")
    # attempt_number must equal the item's post-increment attempt_count, or uq_job_attempt_item_number
    # and job_work_item.attempt_count diverge silently and the two sequences never line up again.
    assert attempt_parameters["attempt_number"] == 2
    assert attempt_parameters["fencing_token"] == 7


async def test_a_claim_that_matches_no_row_returns_none_rather_than_raising() -> None:
    session = RecordingSession()
    claim = await claim_work_item(session, job_run_id=JOB_RUN_ID, worker_id="agri-worker-1", lease_seconds=300)
    assert claim is None
    assert session.emitted("open_attempt") is False


async def test_the_claim_refuses_an_exhausted_item_in_sql_so_a_worker_gets_no_work_instead_of_an_error() -> None:
    session = _claimable_session()
    await claim_work_item(session, job_run_id=JOB_RUN_ID, worker_id="agri-worker-1", lease_seconds=300)
    # Without this predicate, `attempt_count + 1` on an exhausted item violates
    # ck_job_work_item_attempt_count_within_limit and aborts the whole claim statement.
    assert "item.attempt_count < item.max_attempts" in session.sql_for("claim_work_item")


async def test_the_claim_also_takes_a_lease_whose_owner_never_came_back() -> None:
    session = _claimable_session()
    await claim_work_item(session, job_run_id=JOB_RUN_ID, worker_id="agri-worker-1", lease_seconds=300)
    claim_sql = session.sql_for("claim_work_item")
    assert "item.lease_expires_at <= now()" in claim_sql
    assert "FOR UPDATE SKIP LOCKED" in claim_sql


async def test_the_claim_moves_status_owner_expiry_and_token_in_one_statement_because_the_checks_are_immediate() -> (
    None
):
    session = _claimable_session()
    await claim_work_item(session, job_run_id=JOB_RUN_ID, worker_id="agri-worker-1", lease_seconds=300)
    claim_sql = session.sql_for("claim_work_item")
    for column in (
        "status = 'leased'",
        "fencing_token = item.fencing_token + 1",
        "lease_owner =",
        "lease_expires_at =",
    ):
        assert column in claim_sql


async def test_a_resumed_claim_carries_the_cursor_its_last_checkpoint_left_behind() -> None:
    session = _claimable_session()
    session.answer("latest_checkpoint_cursor", [{"cursor": {"day": "2003-04-11"}}])
    claim = await claim_work_item(session, job_run_id=JOB_RUN_ID, worker_id="agri-worker-1", lease_seconds=300)
    assert claim is not None
    assert claim.cursor == {"day": "2003-04-11"}


async def test_a_first_claim_carries_no_cursor_rather_than_an_empty_one() -> None:
    session = _claimable_session()
    claim = await claim_work_item(session, job_run_id=JOB_RUN_ID, worker_id="agri-worker-1", lease_seconds=300)
    assert claim is not None
    assert claim.cursor is None


async def test_a_heartbeat_that_no_longer_owns_the_fence_reports_false_and_never_touches_the_attempt_row() -> None:
    session = RecordingSession()
    held = await extend_lease(session, _claim(), lease_seconds=300)
    assert held is False
    assert session.emitted("extend_attempt_heartbeat") is False


async def test_a_heartbeat_that_still_owns_the_fence_extends_both_the_lease_and_the_attempt() -> None:
    session = RecordingSession()
    session.answer("extend_work_item_lease", [{"id": WORK_ITEM_ID}])
    session.answer("extend_attempt_heartbeat", [{"id": ATTEMPT_ID}])
    assert await extend_lease(session, _claim(), lease_seconds=300) is True
    assert session.markers() == ["extend_work_item_lease", "extend_attempt_heartbeat"]
    assert session.parameters_for("extend_work_item_lease")["fencing_token"] == 7


async def test_a_checkpoint_refuses_without_writing_when_the_fence_moved() -> None:
    session = RecordingSession()
    record = await record_checkpoint(
        session,
        _claim(),
        cursor={"day": "2003-04-11"},
        progress_fraction=0.6,
        lease_seconds=300,
    )
    assert record is None
    assert session.emitted("append_checkpoint") is False


async def test_a_checkpoint_takes_its_sequence_from_the_locked_item_row_never_from_a_max_query() -> None:
    session = RecordingSession()
    session.answer("advance_checkpoint_sequence", [{"checkpoint_sequence": 4}])
    session.answer("append_checkpoint", [{"id": uuid.uuid4()}])
    record = await record_checkpoint(
        session,
        _claim(),
        cursor={"day": "2003-04-11"},
        progress_fraction=0.6,
        lease_seconds=300,
    )
    assert record is not None
    assert record.sequence == 4
    assert session.markers() == ["advance_checkpoint_sequence", "append_checkpoint"]
    assert session.parameters_for("append_checkpoint")["sequence"] == 4
    assert "max(sequence)" not in session.sql_for("advance_checkpoint_sequence")


async def test_a_checkpoint_checksum_is_the_sha256_of_the_cursors_canonical_json() -> None:
    session = RecordingSession()
    session.answer("advance_checkpoint_sequence", [{"checkpoint_sequence": 1}])
    session.answer("append_checkpoint", [{"id": uuid.uuid4()}])
    cursor = {"day": "2003-04-11", "offset": 120}
    record = await record_checkpoint(session, _claim(), cursor=cursor, progress_fraction=0.6, lease_seconds=300)
    assert record is not None
    expected = hashlib.sha256(b'{"day":"2003-04-11","offset":120}').hexdigest()
    assert record.cursor_checksum == expected
    assert session.parameters_for("append_checkpoint")["cursor"] == '{"day":"2003-04-11","offset":120}'


def test_a_cursor_checksum_ignores_key_order_so_the_same_cursor_always_digests_the_same() -> None:
    assert cursor_checksum({"a": 1, "b": 2}) == cursor_checksum({"b": 2, "a": 1})


def test_a_cursor_may_carry_a_timestamp_and_renders_it_as_an_isoformat_string() -> None:
    rendered = canonical_json({"observed_at": datetime(2026, 8, 7, 12, 0, tzinfo=UTC)})
    assert rendered == '{"observed_at":"2026-08-07T12:00:00+00:00"}'


def test_a_cursor_holding_a_value_json_cannot_carry_is_refused_by_name() -> None:
    with pytest.raises(JobCursorError, match="timedelta"):
        canonical_json({"window": timedelta(days=5)})


async def test_completion_closes_the_attempt_before_the_item_so_a_crash_leaves_the_shard_re_drivable() -> None:
    session = RecordingSession()
    session.answer("close_attempt_succeeded", [{"id": ATTEMPT_ID}])
    session.answer("complete_work_item", [{"id": WORK_ITEM_ID}])
    session.answer("increment_run_succeeded", [{"succeeded_work_items": 1}])
    assert await complete_work_item(session, _claim()) is True
    assert session.markers() == ["close_attempt_succeeded", "complete_work_item", "increment_run_succeeded"]


async def test_completion_that_lost_the_fence_reports_false_and_never_rolls_the_run_counter() -> None:
    session = RecordingSession()
    session.answer("close_attempt_succeeded", [{"id": ATTEMPT_ID}])
    assert await complete_work_item(session, _claim()) is False
    assert session.emitted("increment_run_succeeded") is False


@pytest.mark.parametrize(
    ("marker", "call"),
    [
        ("close_attempt_succeeded", _complete),
        ("close_attempt_failed", _fail),
        ("close_attempt_deferred", _defer),
    ],
)
async def test_every_terminal_attempt_close_is_fenced_through_the_item_row_not_through_the_attempts_own_token(
    marker: str,
    call: LeaseCall,
) -> None:
    session = RecordingSession()
    session.answer(marker, [{"id": ATTEMPT_ID}])
    await call(session)
    close_sql = session.sql_for(marker)
    # `job_attempt.fencing_token` is stamped by `open_attempt` with the claim's own token and is never
    # updated afterwards, so `AND job_attempt.fencing_token = :fencing_token` would match its own row
    # forever and fence nothing at all. The only column that moves when a shard changes hands is
    # `job_work_item.fencing_token`, which is why the close reaches the item row to find it.
    assert "FROM agri.job_work_item AS item" in close_sql
    assert "item.fencing_token = :fencing_token" in close_sql
    assert "item.lease_owner = :lease_owner" in close_sql
    # Locked rather than joined: under READ COMMITTED a bare join reads a snapshot that may predate a
    # competing claim's commit, and the whole point is to lose to that claim.
    assert "FOR UPDATE" in close_sql
    parameters = session.parameters_for(marker)
    assert parameters["fencing_token"] == 7
    assert parameters["lease_owner"] == "agri-worker-1"
    assert parameters["work_item_id"] == WORK_ITEM_ID


async def test_the_run_counter_is_clamped_so_a_stale_total_cannot_abort_a_completion_that_really_happened() -> None:
    session = RecordingSession()
    session.answer("close_attempt_succeeded", [{"id": ATTEMPT_ID}])
    session.answer("complete_work_item", [{"id": WORK_ITEM_ID}])
    session.answer("increment_run_succeeded", [{"succeeded_work_items": 1}])
    await complete_work_item(session, _claim())
    assert "LEAST(succeeded_work_items + 1, total_work_items - failed_work_items)" in (
        session.sql_for("increment_run_succeeded")
    )


async def test_a_retryable_failure_waits_the_policys_backoff_for_the_attempt_that_just_failed() -> None:
    session = RecordingSession()
    session.answer("close_attempt_failed", [{"id": ATTEMPT_ID}])
    session.answer("retry_work_item", [{"id": WORK_ITEM_ID}])
    policy = RetryPolicy(initial_backoff_seconds=30.0, backoff_multiplier=2.0, maximum_backoff_seconds=3600.0)
    failure = await fail_work_item(
        session,
        _claim(attempt_number=3, max_attempts=5),
        failure_class="UpstreamHttpError",
        reason="upstream returned 503",
        retry_policy=policy,
    )
    assert failure is not None
    assert failure.disposition == "retry_wait"
    # The third attempt just failed, so the wait is 30 * 2^(3-1) = 120s, not the first delay again.
    assert failure.backoff_seconds == 120.0
    assert session.parameters_for("retry_work_item")["backoff_seconds"] == 120.0
    assert session.emitted("dead_letter_work_item") is False


async def test_the_last_attempt_dead_letters_the_shard_and_never_reports_success() -> None:
    session = RecordingSession()
    session.answer("close_attempt_failed", [{"id": ATTEMPT_ID}])
    session.answer("dead_letter_work_item", [{"id": WORK_ITEM_ID}])
    session.answer("increment_run_failed", [{"failed_work_items": 1}])
    failure = await fail_work_item(
        session,
        _claim(attempt_number=5, max_attempts=5),
        failure_class="UpstreamHttpError",
        reason="upstream returned 503",
        retry_policy=RetryPolicy(),
    )
    assert failure is not None
    assert failure.disposition == "dead_letter"
    assert failure.is_dead_letter is True
    assert failure.backoff_seconds is None
    assert session.emitted("retry_work_item") is False
    assert session.emitted("increment_run_succeeded") is False


async def test_a_dead_letter_sets_its_completion_time_in_the_same_statement_as_its_terminal_status() -> None:
    session = RecordingSession()
    session.answer("close_attempt_failed", [{"id": ATTEMPT_ID}])
    session.answer("dead_letter_work_item", [{"id": WORK_ITEM_ID}])
    session.answer("increment_run_failed", [{"failed_work_items": 1}])
    await fail_work_item(
        session,
        _claim(attempt_number=5, max_attempts=5),
        failure_class="UpstreamHttpError",
        reason="gone",
        retry_policy=RetryPolicy(),
    )
    dead_letter_sql = session.sql_for("dead_letter_work_item")
    assert "status = 'dead_letter'" in dead_letter_sql
    assert "completed_at = now()" in dead_letter_sql


async def test_a_failure_that_lost_the_fence_returns_none_so_the_caller_can_roll_back() -> None:
    session = RecordingSession()
    session.answer("close_attempt_failed", [{"id": ATTEMPT_ID}])
    failure = await fail_work_item(
        session,
        _claim(),
        failure_class="UpstreamHttpError",
        reason="upstream returned 503",
        retry_policy=RetryPolicy(),
    )
    assert failure is None
    assert session.emitted("increment_run_failed") is False


async def test_a_deferral_raises_the_attempt_ceiling_so_waiting_never_spends_the_retry_budget() -> None:
    session = RecordingSession()
    session.answer("close_attempt_deferred", [{"id": ATTEMPT_ID}])
    session.answer("defer_work_item", [{"id": WORK_ITEM_ID}])
    resume_at = datetime(2026, 8, 13, 14, 0, tzinfo=UTC)
    assert await defer_work_item(session, _claim(), resume_at=resume_at, reason="publishes Thursdays") is True
    defer_sql = session.sql_for("defer_work_item")
    assert "max_attempts = max_attempts + 1" in defer_sql
    assert session.parameters_for("defer_work_item")["resume_at"] == resume_at


async def test_a_deferral_records_its_reason_on_the_attempt_metrics_not_in_the_items_error_summary() -> None:
    session = RecordingSession()
    session.answer("close_attempt_deferred", [{"id": ATTEMPT_ID}])
    session.answer("defer_work_item", [{"id": WORK_ITEM_ID}])
    await defer_work_item(session, _claim(), resume_at=None, reason="publishes Thursdays")
    assert session.parameters_for("close_attempt_deferred")["metrics"] == '{"deferral_reason":"publishes Thursdays"}'
    assert "last_error_summary" not in session.sql_for("defer_work_item")


async def test_a_deferral_with_no_resume_time_wakes_on_the_next_tick() -> None:
    session = RecordingSession()
    session.answer("close_attempt_deferred", [{"id": ATTEMPT_ID}])
    session.answer("defer_work_item", [{"id": WORK_ITEM_ID}])
    await defer_work_item(session, _claim(), resume_at=None, reason="budget")
    assert "COALESCE(CAST(:resume_at AS timestamptz), now())" in session.sql_for("defer_work_item")
    assert session.parameters_for("defer_work_item")["resume_at"] is None


async def test_a_fenced_out_worker_closes_its_own_attempt_as_lost_so_it_stops_looking_like_live_work() -> None:
    session = RecordingSession()
    session.answer("close_attempt_lost", [{"id": ATTEMPT_ID}])
    await release_lost_attempt(session, _claim())
    assert session.parameters_for("close_attempt_lost")["failure_class"] == "fence_lost"


async def test_the_lost_close_is_the_one_attempt_write_that_must_stay_unfenced_or_it_reaps_nothing() -> None:
    session = RecordingSession()
    session.answer("close_attempt_lost", [{"id": ATTEMPT_ID}])
    await release_lost_attempt(session, _claim())
    # It runs precisely when the fence has already moved, so fencing it the way the three terminal closes
    # are fenced would match zero rows and leave the dangling 'running' attempt it exists to reap.
    assert "job_work_item" not in session.sql_for("close_attempt_lost")
    assert "fencing_token" not in session.sql_for("close_attempt_lost")


async def test_reclaiming_splits_expired_leases_into_requeued_and_dead_lettered_and_closes_their_attempts() -> None:
    session = RecordingSession()
    session.answer(
        "reclaim_expired_leases",
        [
            {"id": WORK_ITEM_ID, "job_run_id": JOB_RUN_ID, "status": "retry_wait"},
            {"id": OTHER_WORK_ITEM_ID, "job_run_id": JOB_RUN_ID, "status": "dead_letter"},
        ],
    )
    session.answer("close_lost_attempts", [{"id": ATTEMPT_ID}, {"id": uuid.uuid4()}])
    session.answer("increment_run_failed", [{"failed_work_items": 1}])
    summary = await reclaim_expired_leases(session, backoff_seconds=30.0, job_run_id=JOB_RUN_ID)
    assert (summary.requeued, summary.dead_lettered, summary.total) == (1, 1, 2)
    assert summary.attempts_closed == 2
    assert summary.work_item_ids == (WORK_ITEM_ID, OTHER_WORK_ITEM_ID)
    assert session.parameters_for("increment_run_failed")["increment"] == 1


async def test_reclaiming_nothing_touches_no_further_statement() -> None:
    session = RecordingSession()
    summary = await reclaim_expired_leases(session, backoff_seconds=30.0)
    assert summary.total == 0
    assert session.emitted("close_lost_attempts") is False
    assert session.emitted("increment_run_failed") is False


async def test_reclaiming_never_bumps_the_fencing_token_because_only_the_next_claim_may_mint_one() -> None:
    session = RecordingSession()
    session.answer("reclaim_expired_leases", [{"id": WORK_ITEM_ID, "job_run_id": JOB_RUN_ID, "status": "retry_wait"}])
    session.answer("close_lost_attempts", [{"id": ATTEMPT_ID}])
    await reclaim_expired_leases(session, backoff_seconds=30.0)
    reclaim_sql = session.sql_for("reclaim_expired_leases")
    assert "fencing_token" not in reclaim_sql
    # 'queued' plus a stale non-NULL lease pair passes every CHECK and produces a row that lies about
    # who owns it, so the reaper lands on 'retry_wait', which forces next_attempt_at instead.
    assert "'queued'" not in reclaim_sql
    assert "lease_owner = NULL" in reclaim_sql


async def test_a_failure_reason_is_redacted_at_the_chokepoint_not_only_on_the_exception_path() -> None:
    session = RecordingSession()
    session.answer("close_attempt_failed", [{"id": ATTEMPT_ID}])
    session.answer("retry_work_item", [{"id": WORK_ITEM_ID}])
    # A handler's own reason string never passes through failure_summary, so the guarantee has to live
    # here rather than resting on every future reason producer happening to stay safe.
    await fail_work_item(
        session,
        _claim(),
        failure_class="UpstreamHttpError",
        reason="GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/abc123KEY/VIIRS returned 503",
        retry_policy=RetryPolicy(),
    )
    assert "abc123KEY" not in str(session.parameters_for("close_attempt_failed")["error_summary"])
    assert "abc123KEY" not in str(session.parameters_for("retry_work_item")["error_summary"])


async def test_a_dead_letter_summary_is_redacted_too_so_the_terminal_row_never_publishes_a_key() -> None:
    session = RecordingSession()
    session.answer("close_attempt_failed", [{"id": ATTEMPT_ID}])
    session.answer("dead_letter_work_item", [{"id": WORK_ITEM_ID}])
    session.answer("increment_run_failed", [{"failed_work_items": 1}])
    await fail_work_item(
        session,
        _claim(attempt_number=5, max_attempts=5),
        failure_class="UpstreamHttpError",
        reason="could not connect to postgresql://postgres:hunter2@proxy:37967/plantgeo",
        retry_policy=RetryPolicy(),
    )
    assert "hunter2" not in str(session.parameters_for("dead_letter_work_item")["error_summary"])


async def test_a_deferral_reason_is_redacted_too_because_a_wait_can_quote_the_url_it_waited_on() -> None:
    session = RecordingSession()
    session.answer("close_attempt_deferred", [{"id": ATTEMPT_ID}])
    session.answer("defer_work_item", [{"id": WORK_ITEM_ID}])
    await defer_work_item(
        session,
        _claim(),
        resume_at=None,
        reason="https://firms.modaps.eosdis.nasa.gov/api/area/csv/abc123KEY/VIIRS has no data yet",
    )
    assert "abc123KEY" not in str(session.parameters_for("close_attempt_deferred")["metrics"])


async def test_a_failed_attempt_records_the_metrics_an_operator_needs_to_triage_a_dead_letter() -> None:
    session = RecordingSession()
    session.answer("close_attempt_failed", [{"id": ATTEMPT_ID}])
    session.answer("retry_work_item", [{"id": WORK_ITEM_ID}])
    await fail_work_item(
        session,
        _claim(),
        failure_class="UpstreamHttpError",
        reason="upstream returned 503",
        retry_policy=RetryPolicy(),
        metrics={"chunks_walked": 4, "window_records_written": 120},
    )
    assert session.parameters_for("close_attempt_failed")["metrics"] == (
        '{"chunks_walked":4,"window_records_written":120}'
    )


async def test_a_deferral_carries_the_handlers_metrics_and_never_lets_them_shadow_the_runtimes_reason() -> None:
    session = RecordingSession()
    session.answer("close_attempt_deferred", [{"id": ATTEMPT_ID}])
    session.answer("defer_work_item", [{"id": WORK_ITEM_ID}])
    await defer_work_item(
        session,
        _claim(),
        resume_at=None,
        reason="publishes Thursdays",
        metrics={"chunks_walked": 4, "deferral_reason": "a handler tried to overwrite this"},
    )
    written = json.loads(str(session.parameters_for("close_attempt_deferred")["metrics"]))
    assert written == {"chunks_walked": 4, "deferral_reason": "publishes Thursdays"}


async def test_a_checkpoint_never_rewinds_the_items_progress_because_a_park_states_where_it_resumes() -> None:
    session = RecordingSession()
    session.answer("advance_checkpoint_sequence", [{"checkpoint_sequence": 4}])
    session.answer("append_checkpoint", [{"id": uuid.uuid4()}])
    await record_checkpoint(session, _claim(), cursor={"chunk": 5}, progress_fraction=0.0, lease_seconds=300)
    # A deferral or a budget yield says where it resumes, not how far it got, so its fraction defaults
    # to zero. The checkpoint row keeps the raw value; the item column is the high-water mark.
    assert "GREATEST(progress_fraction," in session.sql_for("advance_checkpoint_sequence")
    assert session.parameters_for("append_checkpoint")["progress_fraction"] == 0.0


def test_an_error_summary_never_carries_a_url_or_a_dsn() -> None:
    keyed_url = failure_summary(RuntimeError("GET https://firms.modaps.eosdis.nasa.gov/api?MAP_KEY=abc123 failed"))
    assert "MAP_KEY" not in keyed_url
    assert "[redacted]" in keyed_url
    dsn = failure_summary(RuntimeError("could not connect to postgresql://postgres:hunter2@proxy:37967/plantgeo"))
    assert "hunter2" not in dsn


def test_a_sqlalchemy_error_degrades_to_its_class_name_so_its_statement_and_parameters_stay_out_of_the_log() -> None:
    error = OperationalError("INSERT INTO agri.job_work_item ...", {"payload": "secret"}, Exception("boom"))
    assert failure_summary(error) == "job step failed (OperationalError)"


def test_an_empty_error_message_still_produces_a_summary_rather_than_an_empty_column() -> None:
    assert failure_summary(RuntimeError("")) == "unknown job failure"


def test_a_firms_key_in_a_url_path_is_redacted_because_firms_puts_its_key_in_the_path_not_the_query() -> None:
    keyed = "GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/abc123KEY/VIIRS/-125,42,-111,49/5 failed"
    assert "abc123KEY" not in redact_text(keyed)


def test_a_bare_query_string_is_redacted_because_a_message_can_name_a_key_without_its_scheme() -> None:
    assert "abc123KEY" not in redact_text("request to /api/area?MAP_KEY=abc123KEY failed")


# The `ingest` copy of this redaction is deliberately duplicated rather than imported: `jobs` is the
# reusable primitive `ingest` builds on, and a dependency either way would invert that layering. This
# test is the thing that keeps the two honest, and it lives here because this file owns that contract.
def test_the_ingest_copy_of_the_redaction_behaves_identically_because_neither_may_import_the_other() -> None:
    keyed = "GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/abc123KEY/VIIRS returned 503"
    dsn = "could not connect to postgresql://postgres:hunter2@proxy:37967/plantgeo"
    for message in (keyed, dsn):
        assert redact_text(message) == redact_secrets(message)
    # ingest/results.py::failure_reason claimed to redact and performed no substitution at all, so an
    # httpx error carrying a FIRMS URL published a live key into the cron log verbatim.
    assert "abc123KEY" not in failure_reason(RuntimeError(keyed))
    assert "hunter2" not in failure_reason(RuntimeError(dsn))
    assert failure_reason(OperationalError("INSERT INTO geo.features ...", {}, Exception("boom"))) == (
        "ingest write failed (OperationalError)"
    )


async def test_an_over_long_failure_class_is_cut_to_its_column_width_rather_than_aborting_the_write() -> None:
    session = RecordingSession()
    session.answer("close_attempt_failed", [{"id": ATTEMPT_ID}])
    session.answer("retry_work_item", [{"id": WORK_ITEM_ID}])
    failure = await fail_work_item(
        session,
        _claim(),
        failure_class="E" * (FAILURE_CLASS_MAX_LENGTH + 40),
        reason="upstream returned 503",
        retry_policy=RetryPolicy(),
    )
    assert failure is not None
    assert len(failure.failure_class) == FAILURE_CLASS_MAX_LENGTH
