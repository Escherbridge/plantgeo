"""Gap planning: turning a detected hole into a claimable window, and everything it must refuse to touch.

Pure-unit coverage on the same seam `test_ingest_reconcile.py`, `test_jobs_lease.py` and `test_jobs_worker.py`
use: `RecordingSession` answers each statement by the `-- <name>` marker it opens with, so no test here opens
a connection.

The file exists to hold two lines. A gap day maps onto the lane's OWN floor-anchored grid and nowhere else,
so a shard planned by `jobs-plan-gaps` is byte-identical to one `jobs-plan-lane` would have planned. And only
a `succeeded` window is ever reopened -- a leased one is held by a live fence, a dead letter is the evidence
that every attempt failed, a cancelled one is an operator's decision, and all three are reported instead.
"""

# ruff: noqa: PLR2004

from __future__ import annotations

import json
import re
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pytest

from agri_data_service.ingest import reconcile as reconcile_module
from agri_data_service.ingest.archive_walk import (
    ARCHIVE_WALK_MAX_ATTEMPTS,
    PAYLOAD_WALK_GENERATION,
    archive_lane_run_key,
    archive_window_shard_key,
)
from agri_data_service.ingest.lanes import FIRMS_ARCHIVE_LANE, lane_windows
from agri_data_service.ingest.reconcile import (
    GAP_PLAN_MARKER_KEY,
    GapWindow,
    gap_reopen_marker,
    gap_window_action,
    map_days_to_grid,
    plan_lane_gaps,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agri_data_service.ingest.lanes import BackfillLane

_MARKER = re.compile(r"^--\s+(\w+)\s*$", re.MULTILINE)

JOB_RUN_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
DEFINITION_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
LAYER_ID = "11111111-1111-4111-8111-111111111111"

OBSERVED_DAYS_MARKER = "observed_days"
LANE_WINDOWS_MARKER = "reconcile_lane_run_windows"
REOPEN_MARKER = "reconcile_reopen_gap_windows"
UPSERT_DEFINITION_MARKER = "upsert_job_definition"
INSERT_RUN_MARKER = "insert_job_run"
INSERT_WORK_ITEMS_MARKER = "insert_job_work_items"
ROLLUP_MARKER = "refresh_job_run_rollup"

# A five-day grid anchored on a day the arithmetic is easy to read: windows are 08-05..08-10, 08-10..08-15
# and 08-15..08-20, and the walk boundary below leaves exactly those three whole.
GAP_TEST_LANE = replace(FIRMS_ARCHIVE_LANE, floor=datetime(2022, 8, 5, tzinfo=UTC))
WALK_BOUNDARY = datetime(2022, 8, 20, tzinfo=UTC)

FIRST_WINDOW_SHARD = "firms-archive:2022-08-05..2022-08-10"
LAST_WINDOW_SHARD = "firms-archive:2022-08-15..2022-08-20"


class FakeResult:
    """The narrow slice of SQLAlchemy's Result this module actually uses."""

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
        self._answers: dict[str, list[Mapping[str, object]]] = {}

    def answer(self, marker: str, rows: Sequence[Mapping[str, object]]) -> None:
        """Answer every statement carrying `-- marker` with these rows."""
        self._answers[marker] = list(rows)

    async def execute(self, statement: object, parameters: Mapping[str, object] | None = None) -> FakeResult:
        """Record the statement and answer it from the script."""
        sql = str(statement)
        self.statements.append((sql, dict(parameters or {})))
        return FakeResult(self._answers.get(self.marker_of(sql) or "", []))

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

    def parameters_for(self, marker: str) -> dict[str, object]:
        """The bound parameters of the first statement carrying this marker."""
        for sql, parameters in self.statements:
            if self.marker_of(sql) == marker:
                return parameters
        raise AssertionError(f"no statement carrying marker {marker!r} was executed")

    def sql_for(self, marker: str) -> str:
        """The text of the first statement carrying this marker."""
        for sql, _ in self.statements:
            if self.marker_of(sql) == marker:
                return sql
        raise AssertionError(f"no statement carrying marker {marker!r} was executed")

    def statement_body(self, marker: str) -> str:
        """The statement with every comment line stripped, for asserting what it actually WRITES.

        A `.sql` file's documentation header is part of `str(text(...))`, and this repo's headers name the
        columns they deliberately leave alone -- so an "x is not in the statement" assertion against the raw
        text matches its own explanation and proves nothing.
        """
        return "\n".join(line for line in self.sql_for(marker).splitlines() if not line.lstrip().startswith("--"))


@pytest.fixture(autouse=True)
def _patch_layer_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve the lane's layer without a database; writer.py owns and tests that lookup itself."""

    async def fake_resolve_layer_id(_session: object, _layer_reference: str) -> str:
        return LAYER_ID

    monkeypatch.setattr(reconcile_module, "resolve_layer_id", fake_resolve_layer_id)


def _observed(*days: str) -> list[dict[str, object]]:
    return [{"observed_day": date.fromisoformat(day), "observation_count": 7} for day in days]


def _every_day_except(*absent: str) -> list[dict[str, object]]:
    """The whole measured span present, minus the named days; the shape a real archive walk leaves behind."""
    skipped = {date.fromisoformat(day) for day in absent}
    span = [date(2022, 8, 5) + (date(2022, 8, 6) - date(2022, 8, 5)) * offset for offset in range(15)]
    return [{"observed_day": day, "observation_count": 7} for day in span if day not in skipped]


def _existing_window(shard_key: str, status: str) -> dict[str, object]:
    """One `job_work_item` row as the lane's own planner would have written it."""
    return {"job_run_id": JOB_RUN_ID, "shard_key": shard_key, "status": status, "payload": {"lane": "firms-archive"}}


def _definition_row() -> dict[str, object]:
    """The `job_definition` row `ensure_job_definition` reads back, with every column its record declares."""
    return {
        "id": DEFINITION_ID,
        "name": "agri.ingest.archive_walk.firms-archive",
        "version": "2026-08-08",
        "handler": "ingest.archive_walk",
        "queue_name": "archive-backfill",
        "concurrency_key": "archive-walk:firms-archive",
        "max_attempts": ARCHIVE_WALK_MAX_ATTEMPTS,
        "lease_seconds": 2400,
        "time_budget_seconds": 780,
        "retry_policy": {"initial_backoff_seconds": 30.0, "backoff_multiplier": 2.0, "maximum_backoff_seconds": 3600.0},
        "parameters": {"lane": "firms-archive"},
    }


def _script_ledger(session: RecordingSession, *, opened: Sequence[str] = (), reopened: Sequence[str] = ()) -> None:
    """Answer every ledger statement an `--apply` run issues, so nothing falls through to an empty result."""
    session.answer(UPSERT_DEFINITION_MARKER, [_definition_row()])
    session.answer(INSERT_RUN_MARKER, [{"id": JOB_RUN_ID}])
    session.answer(INSERT_WORK_ITEMS_MARKER, [{"id": uuid.uuid4()} for _ in opened])
    session.answer(REOPEN_MARKER, [{"shard_key": shard_key} for shard_key in reopened])
    session.answer(
        ROLLUP_MARKER,
        [{"status": "running", "total_work_items": 3, "succeeded_work_items": 1, "failed_work_items": 0}],
    )


async def _plan(
    session: RecordingSession,
    lane: BackfillLane = GAP_TEST_LANE,
    *,
    apply_changes: bool = False,
) -> reconcile_module.LaneGapPlan:
    return await plan_lane_gaps(  # type: ignore[arg-type]
        session,
        lane,
        apply_changes=apply_changes,
        end=WALK_BOUNDARY,
    )


# --- Mapping a day onto the lane's own grid ----------------------------------------------------------


def test_a_missing_day_lands_on_the_window_the_planner_would_have_given_it_and_not_a_new_one() -> None:
    grid = lane_windows(GAP_TEST_LANE, WALK_BOUNDARY)

    mapped, unplannable = map_days_to_grid(GAP_TEST_LANE, [date(2022, 8, 16)], grid)

    assert unplannable == ()
    assert len(mapped) == 1
    lane_window, days = mapped[0]
    assert days == (date(2022, 8, 16),)
    # The shard key is the planner's own, byte for byte, which is what makes `ON CONFLICT DO NOTHING` a
    # no-op instead of a second grid growing beside the first.
    assert archive_window_shard_key(GAP_TEST_LANE, lane_window) == LAST_WINDOW_SHARD


def test_several_missing_days_inside_one_window_collapse_onto_that_one_shard() -> None:
    grid = lane_windows(GAP_TEST_LANE, WALK_BOUNDARY)

    mapped, _ = map_days_to_grid(GAP_TEST_LANE, [date(2022, 8, 5), date(2022, 8, 7), date(2022, 8, 9)], grid)

    assert len(mapped) == 1
    lane_window, days = mapped[0]
    assert archive_window_shard_key(GAP_TEST_LANE, lane_window) == FIRST_WINDOW_SHARD
    assert days == (date(2022, 8, 5), date(2022, 8, 7), date(2022, 8, 9))


def test_a_day_above_the_newest_whole_window_is_reported_unplannable_rather_than_given_a_partial_window() -> None:
    # 2022-08-20 and 2022-08-21 sit above the last whole window. Planning them would mint a trailing partial
    # whose end moves with the calendar -- a NEW shard key every day, which is exactly the re-keying
    # `lane_windows` refuses to do. The forward hourly cron owns those days.
    grid = lane_windows(GAP_TEST_LANE, WALK_BOUNDARY)

    mapped, unplannable = map_days_to_grid(GAP_TEST_LANE, [date(2022, 8, 20), date(2022, 8, 21)], grid)

    assert mapped == ()
    assert unplannable == (date(2022, 8, 20), date(2022, 8, 21))


def test_a_day_below_the_lane_floor_is_reported_unplannable_rather_than_keyed_to_a_negative_grid_index() -> None:
    grid = lane_windows(GAP_TEST_LANE, WALK_BOUNDARY)

    mapped, unplannable = map_days_to_grid(GAP_TEST_LANE, [date(2022, 8, 4), date(1999, 1, 1)], grid)

    assert mapped == ()
    assert unplannable == (date(2022, 8, 4), date(1999, 1, 1))


# --- Which states may be converted, and which are only reported ---------------------------------------


def test_a_gap_day_under_no_planned_window_at_all_is_opened() -> None:
    assert gap_window_action(None) == "open"


def test_a_gap_day_under_a_succeeded_window_is_the_one_state_this_verb_converts() -> None:
    assert gap_window_action("succeeded") == "reopen"


@pytest.mark.parametrize("status", ["queued", "retry_wait", "deferred"])
def test_a_window_that_is_already_claimable_is_left_exactly_where_it_is(status: str) -> None:
    assert gap_window_action(status) == "already_queued"


@pytest.mark.parametrize("status", ["leased", "running"])
def test_a_window_a_live_worker_holds_is_reported_and_never_rewritten_behind_its_fence(status: str) -> None:
    assert gap_window_action(status) == "held"


def test_a_dead_lettered_window_is_evidence_and_is_reported_rather_than_converted() -> None:
    assert gap_window_action("dead_letter") == "dead_lettered"


def test_a_cancelled_window_is_an_operators_decision_and_is_reported_rather_than_overridden() -> None:
    assert gap_window_action("cancelled") == "cancelled"


# --- plan_lane_gaps, against the recording session -----------------------------------------------------


async def test_a_dry_run_measures_every_gap_and_writes_absolutely_nothing() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _every_day_except("2022-08-16"))
    session.answer(LANE_WINDOWS_MARKER, [_existing_window(LAST_WINDOW_SHARD, "succeeded")])

    plan = await _plan(session)

    assert plan.missing_days == (date(2022, 8, 16),)
    assert plan.would_reopen == 1
    assert plan.reopened_count == 0
    assert plan.applied is False
    assert not session.emitted(REOPEN_MARKER)
    assert not session.emitted(INSERT_WORK_ITEMS_MARKER)
    assert not session.emitted(UPSERT_DEFINITION_MARKER)
    assert session.commits == 0


async def test_a_gap_under_a_window_that_was_never_planned_opens_that_window_on_conflict_do_nothing() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _every_day_except("2022-08-16"))
    session.answer(LANE_WINDOWS_MARKER, [])
    _script_ledger(session, opened=[LAST_WINDOW_SHARD])

    plan = await _plan(session, apply_changes=True)

    assert plan.would_open == 1
    assert plan.opened_count == 1
    items = json.loads(str(session.parameters_for(INSERT_WORK_ITEMS_MARKER)["items"]))
    assert [item["shard_key"] for item in items] == [LAST_WINDOW_SHARD]
    # The priority is the window's index on the lane's fixed grid, so a gap-planned shard takes its turn in
    # the same newest-first order rather than jumping the queue by virtue of having been planned late.
    assert [item["priority"] for item in items] == [2]
    assert "ON CONFLICT (job_run_id, shard_key) DO NOTHING" in session.sql_for(INSERT_WORK_ITEMS_MARKER)


async def test_a_hole_inside_an_already_succeeded_window_is_reopened_which_is_the_whole_point_of_the_verb() -> None:
    # The failure this verb exists to end: the walk marked the window succeeded, the data is not there, and
    # no verb in the package could move it back. `jobs-plan-lane` only ever appends whole windows BELOW
    # today, so a hole in the middle of a finished run was unreachable.
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _every_day_except("2022-08-16", "2022-08-18"))
    session.answer(LANE_WINDOWS_MARKER, [_existing_window(LAST_WINDOW_SHARD, "succeeded")])
    _script_ledger(session, reopened=[LAST_WINDOW_SHARD])

    plan = await _plan(session, apply_changes=True)

    assert plan.reopened_count == 1
    assert plan.opened_count == 0
    assert not session.emitted(INSERT_WORK_ITEMS_MARKER)
    reopened = json.loads(str(session.parameters_for(REOPEN_MARKER)["reopened"]))
    assert [entry["shard_key"] for entry in reopened] == [LAST_WINDOW_SHARD]
    marker = json.loads(str(reopened[0]["marker"]))
    assert marker["missing_days"] == ["2022-08-16", "2022-08-18"]
    assert marker["previous_status"] == "succeeded"
    # The run's counters have to be recomputed AFTER a shard leaves `succeeded`, or the run keeps claiming
    # a completion it no longer has.
    assert session.markers().index(ROLLUP_MARKER) > session.markers().index(REOPEN_MARKER)


async def test_the_reopen_statement_carries_the_state_gate_the_python_side_already_applied() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _every_day_except("2022-08-16"))
    session.answer(LANE_WINDOWS_MARKER, [_existing_window(LAST_WINDOW_SHARD, "succeeded")])
    _script_ledger(session, reopened=[LAST_WINDOW_SHARD])

    await _plan(session, apply_changes=True)

    body = session.statement_body(REOPEN_MARKER)
    # Repeated in SQL and not only in Python: between the read above and this write a cron tick can claim
    # the very window being reopened, and these are what make that race a no-op.
    assert "AND item.status = 'succeeded'" in body
    assert "AND item.lease_owner IS NULL" in body
    # `attempt_count` is never reset -- the next claim derives its attempt NUMBER from it and
    # uq_job_attempt_item_number is unique per work item, so a reset makes the shard permanently unclaimable.
    assert "attempt_count =" not in body
    assert "max_attempts = GREATEST(item.max_attempts, item.attempt_count + CAST(:attempt_budget AS integer))" in body
    assert session.parameters_for(REOPEN_MARKER)["attempt_budget"] == ARCHIVE_WALK_MAX_ATTEMPTS
    # The fencing token and the checkpoint sequence stay where they are: monotonicity is the whole mechanism
    # for the first, and uq_job_checkpoint_item_sequence is for the second.
    assert "fencing_token" not in body
    assert "checkpoint_sequence" not in body


async def test_reopening_bumps_the_walk_generation_so_the_window_cannot_resume_at_its_final_chunk() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _every_day_except("2022-08-16"))
    session.answer(LANE_WINDOWS_MARKER, [_existing_window(LAST_WINDOW_SHARD, "succeeded")])
    _script_ledger(session, reopened=[LAST_WINDOW_SHARD])

    await _plan(session, apply_changes=True)

    parameters = session.parameters_for(REOPEN_MARKER)
    assert parameters["marker_key"] == GAP_PLAN_MARKER_KEY
    assert parameters["generation_key"] == PAYLOAD_WALK_GENERATION
    sql = session.sql_for(REOPEN_MARKER)
    # Without this the reopened window resumes from its newest checkpoint -- the one pointing at its FINAL
    # chunk -- walks one day of five, and succeeds again over the same hole.
    assert "jsonb_typeof(item.payload -> CAST(:generation_key AS text)) = 'number'" in sql
    assert "progress_fraction = 0" in sql
    assert "completed_at = NULL" in sql


@pytest.mark.parametrize(
    ("status", "action"),
    [("dead_letter", "dead_lettered"), ("running", "held"), ("cancelled", "cancelled"), ("queued", "already_queued")],
)
async def test_a_window_this_verb_may_not_convert_is_reported_and_no_write_is_issued(status: str, action: str) -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _every_day_except("2022-08-16"))
    session.answer(LANE_WINDOWS_MARKER, [_existing_window(LAST_WINDOW_SHARD, status)])
    _script_ledger(session)

    plan = await _plan(session, apply_changes=True)

    assert [window.shard_key for window in plan.windows_for(action)] == [LAST_WINDOW_SHARD]  # type: ignore[arg-type]
    assert plan.opened_count == 0
    assert plan.reopened_count == 0
    assert not session.emitted(REOPEN_MARKER)
    assert not session.emitted(INSERT_WORK_ITEMS_MARKER)


async def test_a_lane_whose_layer_serves_every_day_it_owes_plans_nothing_at_all() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _every_day_except())
    session.answer(LANE_WINDOWS_MARKER, [_existing_window(LAST_WINDOW_SHARD, "succeeded")])

    plan = await _plan(session, apply_changes=True)

    assert plan.missing_days == ()
    assert plan.windows == ()
    assert plan.opened_count == 0
    assert plan.reopened_count == 0
    assert not session.emitted(REOPEN_MARKER)


async def test_a_lane_whose_grid_holds_no_whole_window_yet_reports_an_empty_plan_rather_than_a_partial_one() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, [])
    session.answer(LANE_WINDOWS_MARKER, [])

    plan = await plan_lane_gaps(  # type: ignore[arg-type]
        session,
        GAP_TEST_LANE,
        apply_changes=True,
        end=datetime(2022, 8, 7, tzinfo=UTC),
    )

    assert plan.through_day is None
    assert plan.missing_days == ()
    assert plan.applied is False
    assert not session.emitted(REOPEN_MARKER)


async def test_the_measured_span_stops_at_the_newest_whole_window_and_never_reaches_the_forward_crons_days() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _observed("2022-08-05"))
    session.answer(LANE_WINDOWS_MARKER, [])

    plan = await _plan(session)

    assert plan.floor_day == date(2022, 8, 5)
    assert plan.through_day == date(2022, 8, 19)
    assert plan.missing_days[-1] == date(2022, 8, 19)
    assert plan.unplannable_days == ()


async def test_the_day_census_is_read_for_one_layer_through_the_report_s_own_statement() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _observed("2022-08-05"))
    session.answer(LANE_WINDOWS_MARKER, [])

    await _plan(session)

    parameters = session.parameters_for(OBSERVED_DAYS_MARKER)
    assert parameters["layer_id"] == LAYER_ID
    assert parameters["published_status"] == "published"
    sql = session.sql_for(OBSERVED_DAYS_MARKER)
    # The same three filters the completeness report applies, because it is the same statement: a row that
    # is unpublished or unlinked is drawn nowhere the time axis can reach, so it cannot vouch for a window.
    assert "features.status = :published_status" in sql
    assert "features.geometry_id IS NOT NULL" in sql
    assert "AND features.layer_id = CAST(:layer_id AS uuid)" in sql


async def test_the_plan_is_scoped_to_the_run_key_that_carries_the_lane_s_floor() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _every_day_except("2022-08-16"))
    session.answer(LANE_WINDOWS_MARKER, [])

    plan = await _plan(session)

    assert plan.run_key == archive_lane_run_key(GAP_TEST_LANE)
    assert session.parameters_for(LANE_WINDOWS_MARKER)["logical_run_key"] == plan.run_key


# --- The summary an operator reads ---------------------------------------------------------------------


async def test_the_summary_names_the_calendar_span_even_when_its_sample_is_truncated() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _observed("2022-08-05"))
    session.answer(LANE_WINDOWS_MARKER, [])

    plan = await _plan(session)
    summary = plan.to_summary(max_reported_windows=2)

    assert summary["state"] == "dry_run"
    assert summary["first_missing_day"] == "2022-08-06"
    assert summary["last_missing_day"] == "2022-08-19"
    assert summary["missing_day_count"] == 14
    assert len(list(summary["missing_day_sample"])) == 2  # type: ignore[call-overload]
    assert summary["omitted_missing_days"] == 12
    opened = summary["open"]
    assert isinstance(opened, dict)
    assert opened["window_count"] == 3
    assert opened["first_day"] == "2022-08-05"
    assert opened["last_day"] == "2022-08-19"
    assert opened["omitted_window_count"] == 1


async def test_every_bind_the_reopen_statement_declares_is_actually_supplied() -> None:
    # The unit seam answers `execute` from a stub, so no bind ever reaches a type resolver and a missing or
    # unresolvable parameter passes silently here and fails on the first real `--apply`. That is not
    # hypothetical: `mark_windows_reconciled.sql` records exactly that incident against production on
    # 2026-08-07. This catches the missing-bind half of it; the type-resolution half needs a real database,
    # and every parameter below is CAST at its use site for that reason.
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _every_day_except("2022-08-16"))
    session.answer(LANE_WINDOWS_MARKER, [_existing_window(LAST_WINDOW_SHARD, "succeeded")])
    _script_ledger(session, reopened=[LAST_WINDOW_SHARD])

    await _plan(session, apply_changes=True)

    declared = set(re.findall(r"(?<![:\w$]):(\w+)(?![:\w$])", session.statement_body(REOPEN_MARKER)))
    assert declared == set(session.parameters_for(REOPEN_MARKER))
    # Each one is cast where it is used, because an untyped bind in a `jsonb_build_object` key position
    # aborts the whole statement with "could not determine data type of parameter".
    body = session.statement_body(REOPEN_MARKER)
    for name in sorted(declared):
        assert f"CAST(:{name} AS " in body, f"bind :{name} reaches the statement without a pinned type"


def test_the_reopen_marker_records_what_was_measured_rather_than_asserting_an_attempt_that_never_ran() -> None:
    lane_window = lane_windows(GAP_TEST_LANE, WALK_BOUNDARY)[0]
    window = GapWindow(
        shard_key=archive_window_shard_key(GAP_TEST_LANE, lane_window),
        lane_window=lane_window,
        action="reopen",
        existing_status="succeeded",
        missing_days=(date(2022, 8, 16),),
        omitted_missing_days=0,
    )

    marker = gap_reopen_marker(window, layer_reference="fire-detections", publication_cadence_days=1)

    assert marker == {
        "layer": "fire-detections",
        "publication_cadence_days": 1,
        "first_day": "2022-08-15",
        "last_day": "2022-08-19",
        "missing_days": ["2022-08-16"],
        "omitted_missing_days": 0,
        "previous_status": "succeeded",
        "tool": "jobs-plan-gaps",
    }
    # No worker id, no fencing token, no attempt number: nothing that would let a later query mistake this
    # for work a worker performed.
    assert "worker_id" not in marker
    assert "fencing_token" not in marker
