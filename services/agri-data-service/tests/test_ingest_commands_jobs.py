"""The `jobs-*` and `validate-streams` verbs: registration, argument validation, and above all the exit rules.

The exit rules are the reason this file exists. A durable archive backfill is INCOMPLETE for weeks by
design, so the two verbs a cron container invokes must distinguish "still working" from "something is
lost", and they must get that distinction right in both directions:

- `jobs-run` exits 0 with work remaining, with the budget spent, and with nothing claimable. It exits 1
  only when a window DEAD-LETTERED -- the one state that means a window is missing until a human acts.
- `validate-streams` exits 0 on `incomplete` and 1 only on `invalid`.

Every async entry point is mocked. Nothing here opens a database, a Redis connection or a socket; the
`ingest_session` fixture is the same monkeypatch `test_ingest_commands.py` uses, for the same reason.
"""

# ruff: noqa: PLR2004, ARG001, ARG002

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import click
import pytest
from click.testing import CliRunner

from agri_data_service.ingest import commands as commands_module
from agri_data_service.ingest.archive_walk import (
    ArchiveWalkContextError,
    archive_lane_definition_name,
    archive_lane_run_key,
    archive_lane_work_items,
    current_archive_walk_context,
)
from agri_data_service.ingest.commands import WORKER_ID_MAX_LENGTH, WORKER_ID_VARIABLE, register_ingest_commands
from agri_data_service.ingest.lanes import FIRMS_ARCHIVE_LANE, STREAMFLOW_ARCHIVE_LANE, BackfillLane
from agri_data_service.ingest.reconcile import LaneReconciliation, build_coverage_category
from agri_data_service.ingest.validation import (
    NULL_GEOMETRY_CHECK,
    ObservedDay,
    StreamDefinition,
    StreamObservations,
    ValidationReport,
    build_stream_report,
)
from agri_data_service.jobs import JobDefinitionNotFoundError, JobSliceSummary, OpenedJobRun, ShutdownSignal

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

    from agri_data_service.ingest.validation import StreamReport

JOB_RUN_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
FIRMS_DEFINITION = archive_lane_definition_name(FIRMS_ARCHIVE_LANE)
STREAMFLOW_DEFINITION = archive_lane_definition_name(STREAMFLOW_ARCHIVE_LANE)

JOBS_VERBS = ("jobs-plan-lane", "jobs-run", "jobs-status", "jobs-reconcile-lane", "validate-streams")


class FakeSession:
    """The narrow slice of AsyncSession these verbs touch: a statement sink and two transaction boundaries."""

    def __init__(self) -> None:
        """Start with nothing executed and no boundary crossed."""
        self.statements: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement: object, parameters: Mapping[str, object] | None = None) -> None:
        """Record the statement; every verb here reads its rows through a mocked entry point instead."""
        self.statements.append(str(statement))

    async def commit(self) -> None:
        """Count a commit."""
        self.commits += 1

    async def rollback(self) -> None:
        """Count a rollback."""
        self.rollbacks += 1


SESSIONS: list[FakeSession] = []


@pytest.fixture(autouse=True)
def _patch_ingest_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hand every verb a fresh recorded session, so "exactly one session per run" is an assertable fact."""
    SESSIONS.clear()

    @asynccontextmanager
    async def fake_ingest_session() -> AsyncIterator[FakeSession]:
        session = FakeSession()
        SESSIONS.append(session)
        yield session

    monkeypatch.setattr(commands_module, "ingest_session", fake_ingest_session)


def _group() -> click.Group:
    group = click.Group("agri-cli")
    register_ingest_commands(group)
    return group


def _invoke(*arguments: str) -> Any:
    return CliRunner().invoke(_group(), list(arguments))


def _last_json_line(output: str) -> dict[str, object]:
    lines = [line for line in output.splitlines() if line.startswith("{")]
    assert lines, f"no JSON line in output: {output!r}"
    return json.loads(lines[-1])


# --- Registration -----------------------------------------------------------------------------------


@pytest.mark.parametrize("verb", JOBS_VERBS)
def test_every_durable_lane_verb_is_registered_on_the_cli_group(verb: str) -> None:
    assert verb in _group().commands


def test_the_jobs_run_help_states_its_exit_rule_where_an_operator_will_read_it() -> None:
    result = _invoke("jobs-run", "--help")

    assert result.exit_code == 0
    assert "--budget-seconds" in result.output
    assert "--worker-id" in result.output
    assert "dead" in result.output.lower()


# --- jobs-plan-lane ---------------------------------------------------------------------------------


class FakeJobLedger:
    """A ledger stand-in applying only the two unique keys `plan_archive_lane` leans on, and nothing else."""

    def __init__(self) -> None:
        """Start with no runs and no landed shard keys."""
        self.run_ids: dict[str, uuid.UUID] = {}
        self.shard_keys: dict[str, set[str]] = {}
        self.calls: list[tuple[str, datetime | None]] = []

    async def plan(
        self,
        _session: object,
        lane: BackfillLane,
        *,
        end: datetime | None = None,
        bbox: str | None = None,
        requested_by: str | None = None,
    ) -> OpenedJobRun:
        """Open (or re-open) the lane's run and fan its windows out, honouring both DO NOTHING clauses."""
        run_key = archive_lane_run_key(lane)
        self.calls.append((run_key, end))
        created = run_key not in self.run_ids
        run_id = self.run_ids.setdefault(run_key, JOB_RUN_ID)
        landed = self.shard_keys.setdefault(run_key, set())
        # Derived through the lane's own planner rather than from a hand-written list, so "the second call
        # adds nothing" rests on the real shard-key derivation and would break if that derivation moved.
        planned = {item.shard_key for item in archive_lane_work_items(lane, end=end, bbox=bbox)}
        added = planned - landed
        landed.update(planned)
        return OpenedJobRun(
            job_run_id=run_id,
            logical_run_key=run_key,
            created=created,
            added_work_items=len(added),
            total_work_items=len(landed),
            status="queued",
        )


@pytest.fixture
def job_ledger(monkeypatch: pytest.MonkeyPatch) -> FakeJobLedger:
    ledger = FakeJobLedger()
    monkeypatch.setattr(commands_module, "plan_archive_lane", ledger.plan)
    return ledger


def test_planning_a_lane_reports_the_run_it_opened_and_the_windows_it_fanned_out(job_ledger: FakeJobLedger) -> None:
    result = _invoke("jobs-plan-lane", "--lane", "streamflow-archive", "--until", "2022-10-05")

    assert result.exit_code == 0
    summary = _last_json_line(result.output)
    assert summary["lane"] == "streamflow-archive"
    assert summary["definition"] == STREAMFLOW_DEFINITION
    assert summary["run_key"] == archive_lane_run_key(STREAMFLOW_ARCHIVE_LANE)
    assert summary["created"] is True
    assert int(str(summary["added_work_items"])) > 0
    assert summary["floor_day"] == STREAMFLOW_ARCHIVE_LANE.floor_day
    assert SESSIONS[0].commits == 1


def test_planning_the_same_lane_twice_on_the_same_day_adds_no_second_copy_of_any_window(
    job_ledger: FakeJobLedger,
) -> None:
    first = _invoke("jobs-plan-lane", "--lane", "streamflow-archive", "--until", "2022-10-05")
    second = _invoke("jobs-plan-lane", "--lane", "streamflow-archive", "--until", "2022-10-05")

    assert first.exit_code == 0
    assert second.exit_code == 0
    opened = _last_json_line(first.output)
    replanned = _last_json_line(second.output)
    assert replanned["run_key"] == opened["run_key"]
    assert replanned["created"] is False
    # The whole point of the floor-anchored grid: a replan is a set of DO NOTHING inserts, so re-running
    # this verb on every tick of every day costs one statement and never re-fetches a window.
    assert replanned["added_work_items"] == 0
    assert replanned["total_work_items"] == opened["total_work_items"]


def test_replanning_a_later_day_adds_only_the_whole_windows_that_day_completed(job_ledger: FakeJobLedger) -> None:
    first = _last_json_line(_invoke("jobs-plan-lane", "--lane", "streamflow-archive", "--until", "2022-10-05").output)
    later = _last_json_line(_invoke("jobs-plan-lane", "--lane", "streamflow-archive", "--until", "2022-12-05").output)

    grown = int(str(later["total_work_items"])) - int(str(first["total_work_items"]))
    assert later["added_work_items"] == grown
    assert grown > 0


def test_a_floor_override_mints_its_own_run_rather_than_reopening_the_declared_one(job_ledger: FakeJobLedger) -> None:
    # The floor is part of `logical_run_key` precisely so that changing it opens a SECOND run with its own
    # grid and its own counters, and leaves the first exactly as complete as it was.
    declared = _invoke("jobs-plan-lane", "--lane", "streamflow-archive", "--until", "2022-12-05")
    shifted = _invoke(
        "jobs-plan-lane", "--lane", "streamflow-archive", "--floor", "2022-09-04", "--until", "2022-12-05"
    )

    assert shifted.exit_code == 0
    assert _last_json_line(shifted.output)["run_key"] != _last_json_line(declared.output)["run_key"]
    assert _last_json_line(shifted.output)["created"] is True
    assert _last_json_line(shifted.output)["floor_day"] == "2022-09-04"


def test_an_unknown_lane_token_is_refused_by_naming_the_registry_rather_than_planning_nothing() -> None:
    result = _invoke("jobs-plan-lane", "--lane", "not-a-lane")

    assert result.exit_code == 2
    assert "firms-archive" in result.output
    assert "streamflow-archive" in result.output


@pytest.mark.parametrize(
    ("lane_name", "floor"),
    [("firms-archive", "1990-01-01"), ("streamflow-archive", "2021-01-01")],
)
def test_a_floor_below_what_the_source_serves_is_refused_before_thousands_of_doomed_windows_are_planned(
    job_ledger: FakeJobLedger,
    lane_name: str,
    floor: str,
) -> None:
    # Every one of those windows would fail upstream, spend all eight attempts on a 30s-doubling-to-an-hour
    # backoff, and dead-letter. The source's own HistoryCapability already states the boundary, so it is
    # read here rather than re-declared -- and walking DEEPER than a lane declares means lowering the
    # source's `earliest` first, because that is the thing doing the refusing.
    result = _invoke("jobs-plan-lane", "--lane", lane_name, "--floor", floor)

    assert result.exit_code == 2
    assert "serves no history before" in result.output
    assert job_ledger.calls == []


@pytest.mark.parametrize(("option", "value"), [("--floor", "2021-01"), ("--until", "yesterday")])
def test_a_lane_boundary_that_is_not_a_plain_calendar_day_is_refused(
    job_ledger: FakeJobLedger,
    option: str,
    value: str,
) -> None:
    result = _invoke("jobs-plan-lane", "--lane", "streamflow-archive", option, value)

    assert result.exit_code == 2
    assert "YYYY-MM-DD" in result.output


def test_the_until_boundary_reaches_the_planner_as_a_utc_midnight_instant(job_ledger: FakeJobLedger) -> None:
    _invoke("jobs-plan-lane", "--lane", "streamflow-archive", "--until", "2022-10-05")

    assert job_ledger.calls[0][1] == datetime(2022, 10, 5, tzinfo=UTC)


# --- jobs-run ---------------------------------------------------------------------------------------


def _slice_summary(**overrides: object) -> JobSliceSummary:
    fields: dict[str, Any] = {
        "definition_name": FIRMS_DEFINITION,
        "worker_id": "jobs-run:test",
        "job_run_id": JOB_RUN_ID,
        "stop_reason": "time_budget_exhausted",
        "claimed": 3,
        "succeeded": 2,
        "run_status": "running",
    }
    fields.update(overrides)
    return JobSliceSummary(**fields)


@pytest.fixture
def slice_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    async def fake_run_job_slice(
        session: object,
        *,
        definition_name: str,
        worker_id: str,
        budget_seconds: float | None = None,
        stop: ShutdownSignal | None = None,
    ) -> JobSliceSummary:
        # A slice runs with the tick's write path already bound; a handler that had to open its own would
        # mean a full TCP+TLS+auth handshake per window. This raises if `jobs-run` forgot to install it.
        context = current_archive_walk_context()
        calls.append(
            {
                "session": session,
                "definition_name": definition_name,
                "worker_id": worker_id,
                "budget_seconds": budget_seconds,
                "write_features": context.write_features,
                "client": context.client,
                "stop": stop,
            }
        )
        return _slice_summary(definition_name=definition_name, worker_id=worker_id)

    monkeypatch.setattr(commands_module, "run_job_slice", fake_run_job_slice)
    return calls


@pytest.mark.parametrize(
    ("stop_reason", "claimed", "succeeded"),
    [("time_budget_exhausted", 3, 2), ("no_claimable_work", 1, 1), ("no_open_run", 0, 0)],
)
def test_a_slice_that_left_work_behind_exits_zero_because_an_in_flight_backfill_is_not_an_incident(
    monkeypatch: pytest.MonkeyPatch,
    stop_reason: str,
    claimed: int,
    succeeded: int,
) -> None:
    async def fake_run_job_slice(_session: object, **_keywords: object) -> JobSliceSummary:
        return _slice_summary(stop_reason=stop_reason, claimed=claimed, succeeded=succeeded)

    monkeypatch.setattr(commands_module, "run_job_slice", fake_run_job_slice)

    result = _invoke("jobs-run", "--definition", FIRMS_DEFINITION)

    assert result.exit_code == 0
    assert _last_json_line(result.output)["stop_reason"] == stop_reason


@pytest.mark.parametrize(("landing", "count"), [("retried", 2), ("deferred", 4), ("yielded", 1), ("abandoned", 1)])
def test_a_parked_or_fenced_out_shard_is_not_a_failed_cron_run(
    monkeypatch: pytest.MonkeyPatch,
    landing: str,
    count: int,
) -> None:
    async def fake_run_job_slice(_session: object, **_keywords: object) -> JobSliceSummary:
        return _slice_summary(**{landing: count})

    monkeypatch.setattr(commands_module, "run_job_slice", fake_run_job_slice)

    result = _invoke("jobs-run", "--definition", FIRMS_DEFINITION)

    assert result.exit_code == 0
    assert _last_json_line(result.output)[landing] == count


def test_a_window_that_dead_lettered_during_the_slice_is_the_one_thing_that_fails_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_job_slice(_session: object, **_keywords: object) -> JobSliceSummary:
        return _slice_summary(dead_lettered=1, stop_reason="no_claimable_work")

    monkeypatch.setattr(commands_module, "run_job_slice", fake_run_job_slice)

    result = _invoke("jobs-run", "--definition", FIRMS_DEFINITION)

    assert result.exit_code == 1
    # The summary is still printed before the exit: a failed run must not also lose its own evidence.
    assert _last_json_line(result.output)["dead_lettered"] == 1


def test_a_slice_that_raised_fails_the_run_and_names_what_it_could_not_find(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_job_slice(_session: object, **_keywords: object) -> JobSliceSummary:
        raise JobDefinitionNotFoundError("no enabled job definition named 'agri.ingest.archive_walk.firms-archive'")

    monkeypatch.setattr(commands_module, "run_job_slice", fake_run_job_slice)

    result = _invoke("jobs-run", "--definition", FIRMS_DEFINITION)

    assert result.exit_code == 1
    assert "no enabled job definition" in result.output


def test_a_slice_opens_exactly_one_session_and_binds_the_ticks_write_path_around_it(
    slice_calls: list[dict[str, object]],
) -> None:
    result = _invoke("jobs-run", "--definition", FIRMS_DEFINITION, "--budget-seconds", "300")

    assert result.exit_code == 0
    assert len(SESSIONS) == 1
    assert len(slice_calls) == 1
    assert slice_calls[0]["session"] is SESSIONS[0]
    assert slice_calls[0]["budget_seconds"] == 300.0
    assert callable(slice_calls[0]["write_features"])
    # Deliberately no shared client: each archive source owns one per chunk under its own measured bounds,
    # and one handed down from here would impose one lane's timeout on the other.
    assert slice_calls[0]["client"] is None
    # The stop flag is installed HERE, at the process boundary, and handed down. Without one a Railway
    # SIGTERM ends the container mid-shard and strands that window behind a lease no living process owns.
    stop = slice_calls[0]["stop"]
    assert isinstance(stop, ShutdownSignal)
    assert not stop.requested


def test_the_walk_context_is_released_once_the_slice_ends_so_it_cannot_leak_into_another_verb(
    slice_calls: list[dict[str, object]],
) -> None:
    assert _invoke("jobs-run", "--definition", FIRMS_DEFINITION).exit_code == 0

    # A context left bound would outlive the session it writes through, so the next caller's handler would
    # write into a closed connection instead of refusing in typed terms.
    with pytest.raises(ArchiveWalkContextError):
        current_archive_walk_context()


def test_a_lane_token_is_the_deployments_spelling_and_resolves_to_the_one_definition_a_run_lives_under(
    slice_calls: list[dict[str, object]],
) -> None:
    # `railway.json` carries `--lane firms-archive` and never the definition token itself. A second
    # hard-coded spelling would join to nothing the day the naming changes, and a slice that joins to
    # nothing claims no work while still exiting 0.
    result = _invoke("jobs-run", "--lane", "firms-archive")

    assert result.exit_code == 0
    assert slice_calls[0]["definition_name"] == FIRMS_DEFINITION


def test_a_slice_that_names_neither_a_lane_nor_a_definition_is_refused_rather_than_claiming_across_lanes() -> None:
    result = _invoke("jobs-run")

    assert result.exit_code == 2
    assert "--lane" in result.output


def test_a_slice_that_names_both_a_lane_and_a_definition_is_refused_rather_than_preferring_one() -> None:
    result = _invoke("jobs-run", "--lane", "firms-archive", "--definition", FIRMS_DEFINITION)

    assert result.exit_code == 2
    assert "not both" in result.output


def test_an_explicit_worker_id_wins_over_the_environment(
    monkeypatch: pytest.MonkeyPatch,
    slice_calls: list[dict[str, object]],
) -> None:
    monkeypatch.setenv(WORKER_ID_VARIABLE, "replica-from-railway")

    _invoke("jobs-run", "--definition", FIRMS_DEFINITION, "--worker-id", "operator-console")

    assert slice_calls[0]["worker_id"] == "operator-console"


def test_the_railway_replica_id_names_the_lease_owner_when_no_worker_id_was_given(
    monkeypatch: pytest.MonkeyPatch,
    slice_calls: list[dict[str, object]],
) -> None:
    monkeypatch.setenv(WORKER_ID_VARIABLE, "replica-from-railway")

    _invoke("jobs-run", "--definition", FIRMS_DEFINITION)

    assert slice_calls[0]["worker_id"] == "jobs-run:replica-from-railway"


def test_two_ticks_with_no_replica_id_never_share_a_lease_owner(
    monkeypatch: pytest.MonkeyPatch,
    slice_calls: list[dict[str, object]],
) -> None:
    monkeypatch.delenv(WORKER_ID_VARIABLE, raising=False)

    _invoke("jobs-run", "--definition", FIRMS_DEFINITION)
    _invoke("jobs-run", "--definition", FIRMS_DEFINITION)

    assert slice_calls[0]["worker_id"] != slice_calls[1]["worker_id"]


def test_an_over_long_worker_id_is_clamped_here_rather_than_aborting_the_claim(
    slice_calls: list[dict[str, object]],
) -> None:
    _invoke("jobs-run", "--definition", FIRMS_DEFINITION, "--worker-id", "w" * 600)

    # lease_owner and worker_id are both VARCHAR(255); an over-long value does not truncate, it aborts the
    # claiming UPDATE and the whole tick with it.
    assert len(str(slice_calls[0]["worker_id"])) == WORKER_ID_MAX_LENGTH


# --- jobs-status ------------------------------------------------------------------------------------


def _state_row(definition: str, run_key: str, status: str, count: int, oldest: str) -> dict[str, object]:
    return {
        "definition": definition,
        "run_key": run_key,
        "status": status,
        "window_count": count,
        "oldest_shard_key": oldest,
    }


class FakeLedgerRows:
    """The two ledger passes `jobs-status` reads, scripted, plus the parameters each pass was bound with."""

    def __init__(self) -> None:
        """Start with both passes empty; a test fills in only the one it is about."""
        self.states: list[dict[str, object]] = []
        self.dead_letters: list[dict[str, object]] = []
        self.parameters: list[dict[str, object]] = []

    async def fetch(
        self,
        _session: object,
        statement: object,
        parameters: Mapping[str, object],
    ) -> Sequence[Mapping[str, object]]:
        """Answer whichever of the two statements was executed, recording what it was bound with."""
        self.parameters.append(dict(parameters))
        return self.dead_letters if "job_dead_lettered_windows" in str(statement) else self.states


@pytest.fixture
def status_rows(monkeypatch: pytest.MonkeyPatch) -> FakeLedgerRows:
    rows = FakeLedgerRows()
    monkeypatch.setattr(commands_module, "fetch_rows", rows.fetch)
    return rows


def test_status_aggregates_a_definitions_runs_and_keeps_the_per_run_breakdown_beside_it(
    status_rows: FakeLedgerRows,
) -> None:
    status_rows.states = [
        _state_row(FIRMS_DEFINITION, "archive-walk:firms-archive:2000-11-01", "queued", 1400, "firms:2001-01-01.."),
        _state_row(FIRMS_DEFINITION, "archive-walk:firms-archive:2000-11-01", "succeeded", 300, "firms:2022-08-05.."),
        _state_row(FIRMS_DEFINITION, "archive-walk:firms-archive:2012-01-20", "queued", 20, "firms:2012-02-01.."),
    ]

    result = _invoke("jobs-status")

    assert result.exit_code == 0
    entry = _last_json_line(result.output)
    assert entry["definition"] == FIRMS_DEFINITION
    assert entry["run_count"] == 2
    assert entry["states"] == {"queued": 1420, "succeeded": 300}
    assert entry["total_windows"] == 1720
    assert entry["outstanding_windows"] == 1420
    assert len(list(entry["runs"])) == 2


def test_the_oldest_outstanding_window_never_comes_from_a_window_that_already_succeeded(
    status_rows: FakeLedgerRows,
) -> None:
    status_rows.states = [
        # The succeeded group holds the lexicographically smallest key, which is also the oldest calendar
        # day. Reporting it as outstanding would say the lane still owes a window it has already walked.
        _state_row(FIRMS_DEFINITION, "run-a", "succeeded", 2, "firms-archive:2000-11-01..2000-11-06"),
        _state_row(FIRMS_DEFINITION, "run-a", "queued", 5, "firms-archive:2005-03-01..2005-03-06"),
    ]

    entry = _last_json_line(_invoke("jobs-status").output)

    assert entry["oldest_outstanding_window"] == "firms-archive:2005-03-01..2005-03-06"


def test_a_dead_lettered_window_is_listed_by_shard_key_because_that_is_what_an_operator_requeues(
    status_rows: FakeLedgerRows,
) -> None:
    status_rows.states = [_state_row(FIRMS_DEFINITION, "run-a", "dead_letter", 3, "firms-archive:a")]
    status_rows.dead_letters = [
        {
            "definition": FIRMS_DEFINITION,
            "shard_key": f"firms-archive:window-{index}",
            "attempt_count": 8,
            "last_error_class": "upstream_unavailable",
        }
        for index in range(3)
    ]

    entry = _last_json_line(_invoke("jobs-status").output)

    assert entry["dead_lettered"] == 3
    assert len(list(entry["dead_letter_windows"])) == 3
    assert entry["omitted_dead_letter_windows"] == 0
    first_listed = next(iter(entry["dead_letter_windows"]))  # type: ignore[call-overload]
    assert first_listed["last_error_class"] == "upstream_unavailable"


def test_a_truncated_dead_letter_listing_still_reports_the_true_number_it_left_out(
    status_rows: FakeLedgerRows,
) -> None:
    status_rows.states = [_state_row(FIRMS_DEFINITION, "run-a", "dead_letter", 169, "firms-archive:a")]
    status_rows.dead_letters = [
        {
            "definition": FIRMS_DEFINITION,
            "shard_key": f"firms-archive:window-{index:04d}",
            "attempt_count": 8,
            "last_error_class": "upstream_unavailable",
        }
        for index in range(169)
    ]

    entry = _last_json_line(_invoke("jobs-status").output)

    assert len(list(entry["dead_letter_windows"])) == commands_module.MAX_REPORTED_DEAD_LETTER_WINDOWS
    # Counted from the status aggregate, not from the listing's length, so a cap can never under-report.
    assert entry["omitted_dead_letter_windows"] == 169 - commands_module.MAX_REPORTED_DEAD_LETTER_WINDOWS


def test_status_reports_a_dead_letter_without_failing_because_it_answers_rather_than_judges(
    status_rows: FakeLedgerRows,
) -> None:
    status_rows.states = [_state_row(FIRMS_DEFINITION, "run-a", "dead_letter", 169, "firms-archive:a")]

    result = _invoke("jobs-status")

    assert result.exit_code == 0


def test_status_prints_one_line_per_definition(status_rows: FakeLedgerRows) -> None:
    status_rows.states = [
        _state_row(FIRMS_DEFINITION, "run-a", "queued", 1, "firms-archive:a"),
        _state_row(STREAMFLOW_DEFINITION, "run-b", "queued", 1, "streamflow-archive:a"),
    ]

    lines = [line for line in _invoke("jobs-status").output.splitlines() if line.startswith("{")]

    assert [json.loads(line)["definition"] for line in lines] == [FIRMS_DEFINITION, STREAMFLOW_DEFINITION]


def test_a_lane_token_is_resolved_to_the_one_definition_name_a_run_is_written_under(
    status_rows: FakeLedgerRows,
) -> None:
    _invoke("jobs-status", "--lane", "firms-archive")

    assert status_rows.parameters[0]["definition"] == FIRMS_DEFINITION


def test_naming_both_a_definition_and_a_lane_is_refused_rather_than_silently_preferring_one() -> None:
    result = _invoke("jobs-status", "--lane", "firms-archive", "--definition", FIRMS_DEFINITION)

    assert result.exit_code == 2
    assert "not both" in result.output


# --- validate-streams -------------------------------------------------------------------------------


def _stream(stream: str, *, rows: int, days: Sequence[str], failing: bool = False) -> StreamReport:
    definition = StreamDefinition(stream=stream, kind="reference", store="features")
    observations = StreamObservations(
        total_rows=rows,
        day_counts=tuple(ObservedDay(date.fromisoformat(day), rows) for day in days),
        check_counts={NULL_GEOMETRY_CHECK: 3} if failing else {},
    )
    return build_stream_report(definition, observations, (), bbox=None, server_day=date(2026, 8, 7))


def _report(*streams: StreamReport) -> ValidationReport:
    return ValidationReport(
        generated_at=datetime(2026, 8, 7, 6, tzinfo=UTC),
        server_day=date(2026, 8, 7),
        bbox=None,
        streams=streams,
        unknown_streams=(),
        unmatched_lanes=(),
    )


@pytest.fixture
def validation(monkeypatch: pytest.MonkeyPatch) -> dict[str, ValidationReport]:
    holder: dict[str, ValidationReport] = {"report": _report()}

    async def fake_build_validation_report(_session: object, *, bbox: str | None = None) -> ValidationReport:
        holder["bbox"] = bbox  # type: ignore[assignment]
        return holder["report"]

    monkeypatch.setattr(commands_module, "build_validation_report", fake_build_validation_report)
    return holder


def test_an_incomplete_stream_does_not_fail_the_run_because_a_backfill_in_flight_is_incomplete_by_design(
    validation: dict[str, ValidationReport],
) -> None:
    # No published rows at all is the most incomplete a stream can be, and it is exactly the state a lane
    # is in on the day it is planned. A daily cron that went red for the weeks of correct walking that
    # follow would be ignored by the time it mattered.
    validation["report"] = _report(_stream("watersheds", rows=0, days=()))

    result = _invoke("validate-streams")

    assert result.exit_code == 0
    assert json.loads(result.output.strip())["verdicts"]["incomplete"] == 1


def test_an_invalid_stream_fails_the_run_because_no_amount_of_further_walking_repairs_a_wrong_row(
    validation: dict[str, ValidationReport],
) -> None:
    validation["report"] = _report(_stream("watersheds", rows=10, days=("2026-08-07",), failing=True))

    result = _invoke("validate-streams")

    assert result.exit_code == 1
    assert json.loads(result.output.strip())["verdicts"]["invalid"] == 1


def test_a_complete_stream_alongside_an_incomplete_one_still_exits_zero(
    validation: dict[str, ValidationReport],
) -> None:
    validation["report"] = _report(
        _stream("watersheds", rows=10, days=("2026-08-07",)),
        _stream("interventions", rows=0, days=()),
    )

    result = _invoke("validate-streams")

    assert result.exit_code == 0
    verdicts = json.loads(result.output.strip())["verdicts"]
    assert verdicts == {"complete": 1, "incomplete": 1, "invalid": 0}


def test_the_json_form_is_one_parseable_line_so_a_cron_log_stays_machine_readable(
    validation: dict[str, ValidationReport],
) -> None:
    validation["report"] = _report(_stream("watersheds", rows=10, days=("2026-08-07",)))

    output = _invoke("validate-streams").output.strip()

    assert len(output.splitlines()) == 1
    assert json.loads(output)["server_day"] == "2026-08-07"


def test_the_markdown_form_is_written_for_a_reader_rather_than_a_parser(
    validation: dict[str, ValidationReport],
) -> None:
    validation["report"] = _report(_stream("watersheds", rows=10, days=("2026-08-07",)))

    result = _invoke("validate-streams", "--format", "markdown")

    assert result.exit_code == 0
    assert result.output.lstrip().startswith("#")


def test_writing_the_report_to_a_file_leaves_stdout_carrying_only_a_receipt(
    tmp_path: Any,
    validation: dict[str, ValidationReport],
) -> None:
    validation["report"] = _report(_stream("watersheds", rows=10, days=("2026-08-07",)))
    destination = tmp_path / "reports" / "streams.md"

    result = _invoke("validate-streams", "--format", "markdown", "--output", str(destination))

    assert result.exit_code == 0
    assert destination.read_text(encoding="utf-8").lstrip().startswith("#")
    receipt = json.loads(result.output.strip())
    assert receipt == {
        "state": "written",
        "output": str(destination),
        "format": "markdown",
        "verdicts": {"complete": 1, "incomplete": 0, "invalid": 0},
    }


def test_a_written_report_that_found_an_invalid_stream_still_fails_the_run(
    tmp_path: Any,
    validation: dict[str, ValidationReport],
) -> None:
    validation["report"] = _report(_stream("watersheds", rows=10, days=("2026-08-07",), failing=True))

    result = _invoke("validate-streams", "--output", str(tmp_path / "streams.json"))

    assert result.exit_code == 1
    assert (tmp_path / "streams.json").exists()


def test_the_bbox_option_reaches_the_report_so_the_boundary_check_can_run(
    validation: dict[str, ValidationReport],
) -> None:
    _invoke("validate-streams", "--bbox", "-125,42,-116,49")

    assert validation["bbox"] == "-125,42,-116,49"


# --- jobs-reconcile-lane ----------------------------------------------------------------------------


def _reconciliation(lane: BackfillLane, *, applied: bool, marked: int) -> LaneReconciliation:
    empty = build_coverage_category("partial", ())
    return LaneReconciliation(
        lane=lane.name,
        definition=archive_lane_definition_name(lane),
        run_key=archive_lane_run_key(lane),
        job_run_id=JOB_RUN_ID,
        layer_reference="fire-detections",
        applied=applied,
        observed_day_count=1_200,
        first_observed_day=date(2022, 8, 5),
        last_observed_day=date(2026, 8, 6),
        planned_window_count=1_900,
        covered=build_coverage_category("covered", ()),
        partial=empty,
        absent=build_coverage_category("absent", ()),
        settled_window_count=0,
        held_shard_keys=(),
        dead_lettered_shard_keys=(),
        dead_lettered_covered=(),
        marked_succeeded_count=marked,
    )


@pytest.fixture
def reconcile_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    async def fake_reconcile_lane(
        session: object,
        lane: BackfillLane,
        *,
        apply_changes: bool = False,
    ) -> LaneReconciliation:
        calls.append({"session": session, "lane": lane, "apply_changes": apply_changes})
        return _reconciliation(lane, applied=apply_changes, marked=7 if apply_changes else 0)

    monkeypatch.setattr(commands_module, "reconcile_lane", fake_reconcile_lane)
    return calls


def test_reconciling_defaults_to_a_dry_run_that_rolls_back_rather_than_commits(
    reconcile_calls: list[dict[str, object]],
) -> None:
    result = _invoke("jobs-reconcile-lane", "--lane", "firms-archive")

    assert result.exit_code == 0
    assert reconcile_calls[0]["apply_changes"] is False
    assert SESSIONS[0].commits == 0
    assert SESSIONS[0].rollbacks == 1
    summary = _last_json_line(result.output)
    assert summary["state"] == "dry_run"
    assert summary["marked_succeeded"] == 0


def test_apply_commits_the_settlement_and_reports_how_many_windows_it_settled(
    reconcile_calls: list[dict[str, object]],
) -> None:
    result = _invoke("jobs-reconcile-lane", "--lane", "firms-archive", "--apply")

    assert result.exit_code == 0
    assert reconcile_calls[0]["apply_changes"] is True
    assert SESSIONS[0].commits == 1
    summary = _last_json_line(result.output)
    assert summary["state"] == "applied"
    assert summary["marked_succeeded"] == 7


def test_the_dry_run_reports_the_observed_span_a_human_checks_before_spending_apply(
    reconcile_calls: list[dict[str, object]],
) -> None:
    summary = _last_json_line(_invoke("jobs-reconcile-lane", "--lane", "firms-archive").output)

    assert summary["first_observed_day"] == "2022-08-05"
    assert summary["last_observed_day"] == "2026-08-06"
    assert summary["observed_day_count"] == 1_200
    assert summary["planned_window_count"] == 1_900
    assert "landed" in str(summary["coverage_rule"])


def test_reconciling_an_unknown_lane_is_refused_before_any_session_is_opened(
    reconcile_calls: list[dict[str, object]],
) -> None:
    result = _invoke("jobs-reconcile-lane", "--lane", "not-a-lane")

    assert result.exit_code == 2
    assert reconcile_calls == []
    assert SESSIONS == []


def test_a_floor_override_reconciles_the_run_that_floor_actually_opened(
    reconcile_calls: list[dict[str, object]],
) -> None:
    # A run planned with `--floor` lives under its own `logical_run_key`, so reconciling it requires naming
    # the same floor. Reconciling the declared run instead would measure a window set that is not the one
    # the operator planned, and settle nothing.
    _invoke("jobs-reconcile-lane", "--lane", "streamflow-archive", "--floor", "2022-09-04")

    lane = reconcile_calls[0]["lane"]
    assert isinstance(lane, BackfillLane)
    assert lane.floor_day == "2022-09-04"
    assert archive_lane_run_key(lane) != archive_lane_run_key(STREAMFLOW_ARCHIVE_LANE)
