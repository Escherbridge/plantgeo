"""The coverage reconciler: which windows count as landed, and what a settlement is allowed to write.

Pure-unit coverage on the same seam `test_jobs_lease.py` and `test_jobs_worker.py` use: `RecordingSession`
answers each statement by the `-- <name>` marker it opens with, so no test here opens a connection.

The whole file exists to hold one line: a window may only reach a terminal SUCCEEDED state when every one
of its days is already served. A dry run writes nothing, a partial window stays queued, a leased window is
left to the worker that owns it, and a dead letter is never converted into a success.
"""

# ruff: noqa: PLR2004

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pytest

from agri_data_service.ingest import reconcile as reconcile_module
from agri_data_service.ingest.archive_walk import (
    archive_lane_run_key,
    archive_window_payload,
    archive_window_shard_key,
)
from agri_data_service.ingest.lanes import FIRMS_ARCHIVE_LANE, STREAMFLOW_ARCHIVE_LANE, lane_windows
from agri_data_service.ingest.reconcile import (
    MAX_LANE_WINDOW_ROWS,
    MAX_OBSERVED_DAY_ROWS,
    RECONCILIATION_MARKER_KEY,
    ReconciliationError,
    ReconciliationScanTooLargeError,
    build_coverage_category,
    reconcile_lane,
    reconciliation_marker,
    window_coverage,
    window_days,
)
from agri_data_service.ingest.source import HistoryWindow

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agri_data_service.ingest.lanes import BackfillLane

_MARKER = re.compile(r"^--\s+(\w+)\s*$", re.MULTILINE)

JOB_RUN_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
LAYER_ID = "11111111-1111-4111-8111-111111111111"

OBSERVED_DAYS_MARKER = "reconcile_observed_layer_days"
LANE_WINDOWS_MARKER = "reconcile_lane_run_windows"
MARK_MARKER = "reconcile_mark_windows_succeeded"
ROLLUP_MARKER = "refresh_job_run_rollup"


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


@pytest.fixture(autouse=True)
def _patch_layer_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve the lane's layer without a database; writer.py owns and tests that lookup itself."""

    async def fake_resolve_layer_id(_session: object, _layer_reference: str) -> str:
        return LAYER_ID

    monkeypatch.setattr(reconcile_module, "resolve_layer_id", fake_resolve_layer_id)


def _window(start: str, end: str) -> HistoryWindow:
    return HistoryWindow(start=datetime.fromisoformat(start), end=datetime.fromisoformat(end))


def _window_row(lane: BackfillLane, start: str, end: str, status: str = "queued") -> dict[str, object]:
    """One `job_work_item` row as the lane's own planner would have written it."""
    window = _window(start, end)
    shard_key = f"{lane.name}:{window.start.date().isoformat()}..{window.end.date().isoformat()}"
    return {
        "job_run_id": JOB_RUN_ID,
        "shard_key": shard_key,
        "status": status,
        "payload": {
            "lane": lane.name,
            "window_start": window.start.isoformat(),
            "window_end": window.end.isoformat(),
        },
    }


def _observed(*days: str) -> list[dict[str, object]]:
    return [{"observed_day": date.fromisoformat(day)} for day in days]


# --- The half-open day rule -------------------------------------------------------------------------


def test_a_window_that_ends_at_midnight_does_not_own_the_day_it_ends_on() -> None:
    days = window_days(_window("2000-11-01T00:00:00+00:00", "2000-11-06T00:00:00+00:00"))

    assert days == (date(2000, 11, 1), date(2000, 11, 2), date(2000, 11, 3), date(2000, 11, 4), date(2000, 11, 5))


def test_a_five_day_grid_window_expects_exactly_its_own_window_days_and_no_more() -> None:
    assert len(window_days(_window("2022-08-05T00:00:00+00:00", "2022-08-10T00:00:00+00:00"))) == 5
    assert len(window_days(_window("2022-08-05T00:00:00+00:00", "2022-09-04T00:00:00+00:00"))) == 30


def test_a_window_whose_end_carries_a_time_still_owns_the_day_that_time_falls_in() -> None:
    days = window_days(_window("2022-08-05T00:00:00+00:00", "2022-08-06T09:30:00+00:00"))

    assert days == (date(2022, 8, 5), date(2022, 8, 6))


# --- The three coverage verdicts --------------------------------------------------------------------


def test_a_window_whose_every_day_is_already_served_is_the_only_shape_that_counts_as_landed() -> None:
    coverage = window_coverage(
        "firms-archive:2022-08-05..2022-08-10",
        "queued",
        _window("2022-08-05T00:00:00+00:00", "2022-08-10T00:00:00+00:00"),
        frozenset(date(2022, 8, day) for day in (5, 6, 7, 8, 9)),
    )

    assert coverage.verdict == "covered"
    assert coverage.observed_days == 5
    assert coverage.expected_days == 5
    assert coverage.missing_sample == ()


def test_a_window_missing_one_single_day_is_partial_and_partial_is_never_landed() -> None:
    coverage = window_coverage(
        "firms-archive:2022-08-05..2022-08-10",
        "queued",
        _window("2022-08-05T00:00:00+00:00", "2022-08-10T00:00:00+00:00"),
        frozenset(date(2022, 8, day) for day in (5, 6, 8, 9)),
    )

    assert coverage.verdict == "partial"
    assert coverage.observed_days == 4
    assert coverage.missing_sample == (date(2022, 8, 7),)


def test_a_window_with_no_observed_day_at_all_is_absent_rather_than_partial() -> None:
    coverage = window_coverage(
        "firms-archive:2000-11-01..2000-11-06",
        "queued",
        _window("2000-11-01T00:00:00+00:00", "2000-11-06T00:00:00+00:00"),
        frozenset({date(2022, 8, 5)}),
    )

    assert coverage.verdict == "absent"
    assert coverage.observed_days == 0
    assert coverage.omitted_missing_days == 0
    assert len(coverage.missing_sample) == 5


def test_a_windows_missing_day_listing_is_bounded_and_reports_how_many_it_left_out() -> None:
    coverage = window_coverage(
        "streamflow-archive:2022-08-05..2022-09-04",
        "queued",
        _window("2022-08-05T00:00:00+00:00", "2022-09-04T00:00:00+00:00"),
        frozenset(),
        max_reported_missing_days=4,
    )

    assert len(coverage.missing_sample) == 4
    assert coverage.omitted_missing_days == 26


# --- The printable category -------------------------------------------------------------------------


def test_a_category_reports_the_calendar_span_it_covers_even_when_its_sample_is_truncated() -> None:
    windows = [
        window_coverage(
            f"firms-archive:window-{index}",
            "queued",
            _window(f"2022-08-{5 + index:02d}T00:00:00+00:00", f"2022-08-{6 + index:02d}T00:00:00+00:00"),
            frozenset({date(2022, 8, 5 + index)}),
        )
        for index in range(10)
    ]

    category = build_coverage_category("covered", windows, max_reported_windows=3)

    assert category.window_count == 10
    assert len(category.windows) == 3
    assert category.omitted_window_count == 7
    # The span is what a human sanity-checks before spending `--apply`, so it must survive truncation.
    assert category.first_day == date(2022, 8, 5)
    assert category.last_day == date(2022, 8, 14)


# --- reconcile_lane, against the recording session ---------------------------------------------------


async def _reconcile(
    session: RecordingSession,
    lane: BackfillLane = FIRMS_ARCHIVE_LANE,
    *,
    apply_changes: bool = False,
) -> reconcile_module.LaneReconciliation:
    return await reconcile_lane(session, lane, apply_changes=apply_changes)  # type: ignore[arg-type]


async def test_a_dry_run_measures_every_window_and_writes_absolutely_nothing() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _observed("2022-08-05", "2022-08-06", "2022-08-07", "2022-08-08"))
    session.answer(
        LANE_WINDOWS_MARKER,
        [
            _window_row(FIRMS_ARCHIVE_LANE, "2022-08-05T00:00:00+00:00", "2022-08-09T00:00:00+00:00"),
            _window_row(FIRMS_ARCHIVE_LANE, "2022-08-09T00:00:00+00:00", "2022-08-14T00:00:00+00:00"),
        ],
    )

    reconciliation = await _reconcile(session)

    assert reconciliation.covered.window_count == 1
    assert reconciliation.absent.window_count == 1
    assert reconciliation.would_settle == 1
    assert reconciliation.marked_succeeded_count == 0
    assert reconciliation.applied is False
    assert not session.emitted(MARK_MARKER)
    assert not session.emitted(ROLLUP_MARKER)
    assert session.commits == 0


async def test_apply_marks_only_the_fully_covered_windows_and_then_recomputes_the_run_counters() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _observed("2022-08-05", "2022-08-06", "2022-08-07", "2022-08-08"))
    session.answer(
        LANE_WINDOWS_MARKER,
        [
            _window_row(FIRMS_ARCHIVE_LANE, "2022-08-05T00:00:00+00:00", "2022-08-09T00:00:00+00:00"),
            _window_row(FIRMS_ARCHIVE_LANE, "2022-08-09T00:00:00+00:00", "2022-08-14T00:00:00+00:00"),
        ],
    )
    session.answer(MARK_MARKER, [{"shard_key": "firms-archive:2022-08-05..2022-08-09"}])
    session.answer(
        ROLLUP_MARKER,
        [{"status": "partial", "total_work_items": 2, "succeeded_work_items": 1, "failed_work_items": 0}],
    )

    reconciliation = await _reconcile(session, apply_changes=True)

    assert reconciliation.marked_succeeded_count == 1
    marked = json.loads(str(session.parameters_for(MARK_MARKER)["marked"]))
    assert [entry["shard_key"] for entry in marked] == ["firms-archive:2022-08-05..2022-08-09"]
    # The rollup runs AFTER the mark and is the counter authority: an incremental bump across hundreds of
    # windows would trip ck_job_run_work_item_counts_within_total, which is IMMEDIATE.
    assert session.markers().index(ROLLUP_MARKER) > session.markers().index(MARK_MARKER)


async def test_a_partially_covered_window_is_never_marked_however_close_it_came() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _observed("2022-08-05", "2022-08-06", "2022-08-07"))
    session.answer(
        LANE_WINDOWS_MARKER,
        [_window_row(FIRMS_ARCHIVE_LANE, "2022-08-05T00:00:00+00:00", "2022-08-09T00:00:00+00:00")],
    )

    reconciliation = await _reconcile(session, apply_changes=True)

    assert reconciliation.partial.window_count == 1
    assert reconciliation.covered.window_count == 0
    assert reconciliation.marked_succeeded_count == 0
    assert not session.emitted(MARK_MARKER)


async def test_a_window_another_worker_holds_under_a_live_lease_is_reported_and_left_alone() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _observed("2022-08-05", "2022-08-06", "2022-08-07", "2022-08-08"))
    session.answer(
        LANE_WINDOWS_MARKER,
        [_window_row(FIRMS_ARCHIVE_LANE, "2022-08-05T00:00:00+00:00", "2022-08-09T00:00:00+00:00", status="running")],
    )

    reconciliation = await _reconcile(session, apply_changes=True)

    assert reconciliation.held_shard_keys == ("firms-archive:2022-08-05..2022-08-09",)
    assert reconciliation.covered.window_count == 0
    assert not session.emitted(MARK_MARKER)


async def test_a_dead_lettered_window_whose_days_all_landed_is_reported_but_never_converted_to_a_success() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _observed("2022-08-05", "2022-08-06", "2022-08-07", "2022-08-08"))
    session.answer(
        LANE_WINDOWS_MARKER,
        [
            _window_row(
                FIRMS_ARCHIVE_LANE,
                "2022-08-05T00:00:00+00:00",
                "2022-08-09T00:00:00+00:00",
                status="dead_letter",
            )
        ],
    )

    reconciliation = await _reconcile(session, apply_changes=True)

    assert reconciliation.dead_lettered_shard_keys == ("firms-archive:2022-08-05..2022-08-09",)
    assert reconciliation.dead_lettered_covered == ("firms-archive:2022-08-05..2022-08-09",)
    assert reconciliation.covered.window_count == 0
    assert reconciliation.marked_succeeded_count == 0
    assert not session.emitted(MARK_MARKER)


async def test_an_already_succeeded_window_is_counted_as_settled_rather_than_measured_again() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _observed("2022-08-05"))
    session.answer(
        LANE_WINDOWS_MARKER,
        [
            _window_row(
                FIRMS_ARCHIVE_LANE,
                "2022-08-05T00:00:00+00:00",
                "2022-08-09T00:00:00+00:00",
                status="succeeded",
            )
        ],
    )

    reconciliation = await _reconcile(session)

    assert reconciliation.settled_window_count == 1
    assert reconciliation.planned_window_count == 1
    assert reconciliation.covered.window_count == 0
    assert reconciliation.partial.window_count == 0
    assert reconciliation.absent.window_count == 0


@pytest.mark.parametrize("status", ["queued", "retry_wait", "deferred"])
async def test_every_claimable_unowned_state_is_reconcilable(status: str) -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _observed("2022-08-05", "2022-08-06", "2022-08-07", "2022-08-08"))
    session.answer(
        LANE_WINDOWS_MARKER,
        [_window_row(FIRMS_ARCHIVE_LANE, "2022-08-05T00:00:00+00:00", "2022-08-09T00:00:00+00:00", status=status)],
    )

    reconciliation = await _reconcile(session)

    assert reconciliation.covered.window_count == 1


async def test_the_reconciliation_reads_the_run_its_lane_key_names_and_reports_it_back() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _observed("2022-08-05"))
    session.answer(LANE_WINDOWS_MARKER, [])

    reconciliation = await _reconcile(session, STREAMFLOW_ARCHIVE_LANE)

    assert session.parameters_for(LANE_WINDOWS_MARKER)["logical_run_key"] == archive_lane_run_key(
        STREAMFLOW_ARCHIVE_LANE
    )
    assert reconciliation.run_key == archive_lane_run_key(STREAMFLOW_ARCHIVE_LANE)
    assert reconciliation.job_run_id is None
    assert reconciliation.observed_day_count == 1


async def test_the_observed_day_scan_is_bounded_to_the_lanes_own_layer_and_to_served_rows_only() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _observed("2022-08-05"))
    session.answer(LANE_WINDOWS_MARKER, [])

    await _reconcile(session)

    parameters = session.parameters_for(OBSERVED_DAYS_MARKER)
    assert parameters["layer_id"] == LAYER_ID
    assert parameters["published_status"] == "published"
    sql = next(sql for sql, _ in session.statements if RecordingSession.marker_of(sql) == OBSERVED_DAYS_MARKER)
    # Both filters are mirrored from validation.py on purpose: a row missing either is drawn on the map but
    # invisible to the time axis, so counting it as coverage would settle a window the slider cannot reach.
    assert "features.status = :published_status" in sql
    assert "features.geometry_id IS NOT NULL" in sql


# --- Refusals ---------------------------------------------------------------------------------------


async def test_an_observed_day_scan_that_hits_its_cap_is_refused_rather_than_truncated() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, [{"observed_day": date(2000, 11, 1)}] * MAX_OBSERVED_DAY_ROWS)

    with pytest.raises(ReconciliationScanTooLargeError, match="observed days"):
        await _reconcile(session)


async def test_a_window_scan_that_hits_its_cap_is_refused_rather_than_truncated() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _observed("2022-08-05"))
    row = _window_row(FIRMS_ARCHIVE_LANE, "2022-08-05T00:00:00+00:00", "2022-08-09T00:00:00+00:00")
    session.answer(LANE_WINDOWS_MARKER, [row] * MAX_LANE_WINDOW_ROWS)

    with pytest.raises(ReconciliationScanTooLargeError, match="windows"):
        await _reconcile(session)


async def test_a_stored_payload_that_names_no_walkable_window_is_refused_by_the_shard_it_belongs_to() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _observed("2022-08-05"))
    session.answer(
        LANE_WINDOWS_MARKER,
        [
            {
                "job_run_id": JOB_RUN_ID,
                "shard_key": "firms-archive:broken",
                "status": "queued",
                "payload": {"lane": "firms-archive"},
            }
        ],
    )

    with pytest.raises(ReconciliationError, match="firms-archive:broken"):
        await _reconcile(session)


async def test_a_stored_payload_that_is_not_a_json_object_is_refused_rather_than_coerced() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _observed("2022-08-05"))
    session.answer(
        LANE_WINDOWS_MARKER,
        [{"job_run_id": JOB_RUN_ID, "shard_key": "firms-archive:odd", "status": "queued", "payload": "not-an-object"}],
    )

    with pytest.raises(ReconciliationError, match="not a JSON object"):
        await _reconcile(session)


# --- The marker the settled window carries -----------------------------------------------------------


def test_the_marker_records_what_was_measured_rather_than_asserting_an_attempt_that_never_ran() -> None:
    coverage = window_coverage(
        "firms-archive:2022-08-05..2022-08-09",
        "queued",
        _window("2022-08-05T00:00:00+00:00", "2022-08-09T00:00:00+00:00"),
        frozenset(date(2022, 8, day) for day in (5, 6, 7, 8)),
    )

    marker = reconciliation_marker(coverage, layer_reference="fire-detections")

    assert marker == {
        "layer": "fire-detections",
        "expected_days": 4,
        "observed_days": 4,
        "first_day": "2022-08-05",
        "last_day": "2022-08-08",
        "previous_status": "queued",
        "tool": "jobs-reconcile-lane",
    }
    # No worker id, no fencing token, no attempt number: nothing that would let a later query mistake this
    # for work a worker performed.
    assert "worker_id" not in marker
    assert "fencing_token" not in marker


async def test_the_mark_statement_refuses_a_shard_that_moved_between_the_read_and_the_write() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _observed("2022-08-05", "2022-08-06", "2022-08-07", "2022-08-08"))
    session.answer(
        LANE_WINDOWS_MARKER,
        [_window_row(FIRMS_ARCHIVE_LANE, "2022-08-05T00:00:00+00:00", "2022-08-09T00:00:00+00:00")],
    )
    session.answer(MARK_MARKER, [])
    session.answer(
        ROLLUP_MARKER,
        [{"status": "queued", "total_work_items": 1, "succeeded_work_items": 0, "failed_work_items": 0}],
    )

    reconciliation = await _reconcile(session, apply_changes=True)

    sql = next(sql for sql, _ in session.statements if RecordingSession.marker_of(sql) == MARK_MARKER)
    # The status and lease predicates are repeated in SQL and not only in Python: between the read above
    # and this write a cron tick can claim the very window being settled, and these are what make that race
    # a no-op instead of a terminal status written behind a live lease.
    assert "item.status IN ('queued', 'retry_wait', 'deferred')" in sql
    assert "item.lease_owner IS NULL" in sql
    assert "completed_at = now()" in sql
    # A shard that moved is simply absent from RETURNING, so the count reported is what actually landed.
    assert reconciliation.marked_succeeded_count == 0


async def test_the_marker_is_merged_into_the_payload_under_its_own_key_so_the_walk_still_reads_the_window() -> None:
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, _observed("2022-08-05", "2022-08-06", "2022-08-07", "2022-08-08"))
    session.answer(
        LANE_WINDOWS_MARKER,
        [_window_row(FIRMS_ARCHIVE_LANE, "2022-08-05T00:00:00+00:00", "2022-08-09T00:00:00+00:00")],
    )
    session.answer(MARK_MARKER, [{"shard_key": "firms-archive:2022-08-05..2022-08-09"}])
    session.answer(
        ROLLUP_MARKER,
        [{"status": "partial", "total_work_items": 1, "succeeded_work_items": 1, "failed_work_items": 0}],
    )

    await _reconcile(session, apply_changes=True)

    parameters = session.parameters_for(MARK_MARKER)
    assert parameters["marker_key"] == RECONCILIATION_MARKER_KEY
    sql = next(sql for sql, _ in session.statements if RecordingSession.marker_of(sql) == MARK_MARKER)
    assert "payload = item.payload || jsonb_build_object(" in sql
    # `now()` and not a Python clock: every timestamp in this ledger comes from the database, so two
    # processes with skewed clocks cannot disagree about when a window was settled.
    assert "jsonb_build_object('reconciled_at', now())" in sql


async def test_the_window_a_reconciliation_measures_is_the_one_the_planner_stored_on_the_payload() -> None:
    # The reconciler reads the window off the STORED payload, never re-derived from the shard key, so the
    # planner's spelling and the reconciler's reading cannot drift. This drives the real planner output
    # through the real read path rather than through a hand-written row.
    lane_window = lane_windows(FIRMS_ARCHIVE_LANE, datetime(2000, 11, 11, tzinfo=UTC))[-1]
    session = RecordingSession()
    session.answer(OBSERVED_DAYS_MARKER, [])
    session.answer(
        LANE_WINDOWS_MARKER,
        [
            {
                "job_run_id": JOB_RUN_ID,
                "shard_key": archive_window_shard_key(FIRMS_ARCHIVE_LANE, lane_window),
                "status": "queued",
                "payload": archive_window_payload(FIRMS_ARCHIVE_LANE, lane_window),
            }
        ],
    )

    reconciliation = await _reconcile(session)

    assert reconciliation.absent.window_count == 1
    measured = reconciliation.absent.windows[0]
    assert measured.first_day == lane_window.window.start.date()
    assert measured.expected_days == FIRMS_ARCHIVE_LANE.window_days
