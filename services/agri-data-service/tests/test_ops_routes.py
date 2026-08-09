"""The backfill console reports the ledger honestly and never raises on a bad read."""

# These tests stub AsyncSession, so they prove shape, derivation and rendering only --
# never that the SQL runs. Real-database coverage of the three ops queries belongs in the
# agri-sweep-db suite, alongside the other job-ledger protocol tests.

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
import structlog.testing
from psycopg2.errors import UndefinedTable
from sqlalchemy.exc import ProgrammingError

from agri_data_service.routes import ops as ops_route

_HTTP_OK = 200
_RUN_ID = UUID("11111111-2222-3333-4444-555555555555")
# 40 of 100 windows done; 24 succeeded in a 24-hour window is 1/hour, so 60 outstanding is 60h.
_EXPECTED_COMPLETION_PERCENT = 40.0
_EXPECTED_ETA_HOURS = 60.0
_EXPECTED_TREND_CUMULATIVE = 3
# 392 of a 1,568-cell lattice done; 24 cells covered in a 24-hour window is 1 cell/hour, so
# the 1,176 cells still outstanding are 1,176 hours away.
_WALK_TARGET_CELLS = 1568
_WALK_CELLS_DONE = 392
_WALK_OBSERVED_VALUES = 2149140
_EXPECTED_WALK_COMPLETION_PERCENT = 25.0
_EXPECTED_WALK_OUTSTANDING_CELLS = 1176
_EXPECTED_WALK_ETA_HOURS = 1176.0
# The two archive lanes the ledger no longer hears from, and the stream each one fills.
_FIRMS_LANE = "agri.ingest.archive_walk.firms-archive"
_STREAMFLOW_LANE = "agri.ingest.archive_walk.streamflow-archive"
_EVIDENCE_ROWS_IN_WINDOW = 595178
# Two queued windows beside two dead-lettered ones: `outstanding_items` says 4, owed says 2.
_EXPECTED_OWED_ITEMS = 2
# 2026-08-08 03:45 UTC to 2026-08-09 14:00 UTC, the production gap the flag was built for.
_EXPECTED_QUIET_HOURS = 34.25
# The radiation walk that landed, beside the same-labelled release set that landed nothing.
_REAL_RADIATION_VALUES = 580414
# The superseded 4-cell soil-wetness pilot, against the 397-cell lattice it is measured on.
_PILOT_CELLS_DONE = 4
_PILOT_TARGET_CELLS = 397
_PILOT_OBSERVED_VALUES = 17544
_EXPECTED_PILOT_COMPLETION_PERCENT = 1.0075566750629723
# Lanes, walks, lane evidence, failures, dead-letter trend, data loads, forecast state, platform
# activity and sources on a cold read; only the three cheap ledger statements once the six
# scanning ones are cached.
_COLD_SNAPSHOT_STATEMENTS = 9
_WARM_SNAPSHOT_STATEMENTS = 3
# The production forecast plane 2026-08-09: one bootstrap method, 1,676 iterations, and 473 scored
# actuals that belong to only 118 distinct iterations. The two readings differ by a factor of four,
# which is the whole reason the panel divides parents rather than rows.
_ITERATION_ROWS = 1676
_ACTUAL_ROWS = 473
_ITERATIONS_SCORED = 118
_EXPECTED_SCORED_PERCENT = 100.0 * _ITERATIONS_SCORED / _ITERATION_ROWS
_ROW_COUNT_IF_SCORED_BY_ACTUAL_ROWS = 100.0 * _ACTUAL_ROWS / _ITERATION_ROWS
# The source whose rows land in the GOVERNED FORECAST-INPUT plane and nowhere else. An audit
# scoped to agri.signal_observation calls it stone dead; it holds 184,409 rows.
_FORECAST_PLANE_SOURCE = "sentinel2-ndvi-l2a"
# The radiation lane's eight releases that ran clean and wrote no row in ANY of the three planes.
_ZERO_LANDING_RELEASES = 8
# Forecast state, platform activity and sources: the reads that are savepoint-isolated per panel.
_HEALTH_PANELS = 3
# A published panel error is a class pair. Anything near this length is a message, not a class.
_PANEL_ERROR_SANE_LENGTH = 120


class _Mappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def all(self) -> list[dict[str, Any]]:
        return self.rows


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> _Mappings:
        return _Mappings(self.rows)


@pytest.fixture(autouse=True)
def _clear_query_cache() -> None:
    """Drop the per-statement result cache between tests.

    It is a module global that deliberately outlives a request, so without this a test
    would read whatever the previous test left behind and pass for the wrong reason.
    """
    ops_route._QUERY_CACHE.clear()


# A fragment unique to each ops statement, and the fixture attribute that answers it. The
# evidence statement is matched on a column alias rather than on its LATERAL, because the failure
# statement carries a LATERAL of its own.
_STATEMENT_FRAGMENTS: tuple[tuple[str, str], ...] = (
    ("WITH item_rollup AS", "lanes"),
    ("WITH historical_release AS", "walks"),
    ("AS oldest_day_written_recently", "lane_evidence"),
    ("LEFT JOIN LATERAL", "failures"),
    ("WITH daily_dead_letters AS", "trend"),
    ("'warehouse signal' as kind", "streams"),
    ("WITH pipeline_stage (", "forecast_stages"),
    ("'registered accounts' as metric", "platform_activity"),
    ("from agri.data_source as source", "sources"),
)


class _Savepoint:
    """The stub's stand-in for SAVEPOINT, which the three panel reads each run inside."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_exception: object) -> bool:
        # False, so an exception raised inside the block propagates to `_optional_rows` to be
        # recorded. Suppressing it here would make every degradation test pass for the wrong reason.
        return False


class _Session:
    """Answer each ops query by matching a fragment unique to its statement."""

    def __init__(  # noqa: PLR0913 - one keyword per ops statement; collapsing them hides which query a test drives.
        self,
        *,
        lanes: list[dict[str, Any]],
        failures: list[dict[str, Any]],
        trend: list[dict[str, Any]],
        streams: list[dict[str, Any]] | None = None,
        walks: list[dict[str, Any]] | None = None,
        lane_evidence: list[dict[str, Any]] | None = None,
        forecast_stages: list[dict[str, Any]] | None = None,
        platform_activity: list[dict[str, Any]] | None = None,
        sources: list[dict[str, Any]] | None = None,
        failing_fragment: str | None = None,
        failing_error: Exception | None = None,
    ) -> None:
        self.lanes = lanes
        self.failures = failures
        self.trend = trend
        self.streams = streams if streams is not None else []
        self.walks = walks if walks is not None else []
        self.lane_evidence = lane_evidence if lane_evidence is not None else []
        self.forecast_stages = forecast_stages if forecast_stages is not None else []
        self.platform_activity = platform_activity if platform_activity is not None else []
        self.sources = sources if sources is not None else []
        # The statement this stub refuses to answer, so one panel can be failed on its own, and
        # what it raises -- a real SQLAlchemy StatementError is a different shape from a plain one.
        self.failing_fragment = failing_fragment
        self.failing_error = failing_error
        self.parameters: list[dict[str, Any]] = []
        self.session_settings: list[str] = []
        self.savepoints = 0

    def begin_nested(self) -> _Savepoint:
        self.savepoints += 1
        return _Savepoint()

    async def execute(self, statement: object, parameters: dict[str, Any] | None = None) -> _Result:
        rendered = str(statement)
        # A GUC pin is not a query: recording it beside the nine statements would shift every
        # index the ordering assertions below read, and it binds nothing to record anyway.
        if rendered.startswith("-- ops_statement_timeout"):
            self.session_settings.append(rendered)
            return _Result([])
        self.parameters.append(parameters if parameters is not None else {})
        if self.failing_fragment is not None and self.failing_fragment in rendered:
            raise self.failing_error or PermissionError("permission denied for table users")
        for fragment, attribute in _STATEMENT_FRAGMENTS:
            if fragment in rendered:
                rows: list[dict[str, Any]] = getattr(self, attribute)
                return _Result(rows)
        raise AssertionError(f"unexpected statement: {rendered[:120]}")


class _FailingSession:
    """A session whose very first statement fails, which is the whole-read failure path.

    The first statement `_load_snapshot` issues is the timeout pin, so this fails before any panel
    is reached and the error lands in `snapshot.error` rather than in `panel_errors`.
    """

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or PermissionError("permission denied for table job_work_item")

    async def execute(self, _statement: object, _parameters: dict[str, Any] | None = None) -> _Result:
        raise self.error


def _session_factory(session: object) -> Any:
    @asynccontextmanager
    async def factory() -> AsyncIterator[object]:
        yield session

    return factory


def _lane(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "definition_name": "firms-archive",
        "definition_version": "1",
        "definition_enabled": True,
        "definition_schedule": "5,35 * * * *",
        "job_run_id": _RUN_ID,
        "logical_run_key": "firms-archive:2000-11-01",
        "run_status": "running",
        "scheduled_for": datetime(2026, 8, 1, tzinfo=UTC),
        "run_started_at": datetime(2026, 8, 1, tzinfo=UTC),
        "run_completed_at": None,
        "total_work_items": 100,
        "succeeded_work_items": 40,
        "failed_work_items": 2,
        "total_items": 100,
        "queued_items": 55,
        "leased_items": 1,
        "running_items": 0,
        "retry_wait_items": 2,
        "deferred_items": 0,
        "succeeded_items": 40,
        "dead_letter_items": 2,
        "cancelled_items": 0,
        "outstanding_items": 60,
        "stalled_lease_items": 1,
        "succeeded_in_window": 24,
        "dead_lettered_in_window": 1,
        "open_attempts": 1,
        "recent_worker_count": 1,
        "oldest_outstanding_shard_key": "2000-11-02",
        "outstanding_progress_fraction": 0.25,
        "next_attempt_at": datetime(2026, 8, 7, 12, 30, tzinfo=UTC),
        "last_item_completed_at": datetime(2026, 8, 7, 11, 30, tzinfo=UTC),
        "last_attempt_started_at": datetime(2026, 8, 7, 11, 45, tzinfo=UTC),
        "last_checkpoint_at": datetime(2026, 8, 7, 11, 40, tzinfo=UTC),
        "last_recorded_activity_at": datetime(2026, 8, 7, 11, 45, tzinfo=UTC),
    }
    row.update(overrides)
    return row


def _failure() -> dict[str, Any]:
    return {
        "definition_name": "firms-archive",
        "logical_run_key": "firms-archive:2000-11-01",
        "shard_key": "2000-11-03",
        "item_status": "dead_letter",
        "attempt_count": 8,
        "max_attempts": 8,
        "last_error_class": "ConnectError",
        "last_error_summary": "getaddrinfo failed <script>",
        "completed_at": datetime(2026, 8, 7, 10, 0, tzinfo=UTC),
        "next_attempt_at": None,
        "attempt_number": 8,
        "attempt_worker_id": "cron-archive-firms",
        "attempt_status": "failed",
        "attempt_finished_at": datetime(2026, 8, 7, 10, 0, tzinfo=UTC),
        "attempt_failure_class": "ConnectError",
        "attempt_error_summary": "getaddrinfo failed <script>alert(1)</script>",
    }


def _trend() -> dict[str, Any]:
    return {
        "definition_name": "firms-archive",
        "day": datetime(2026, 8, 6, tzinfo=UTC),
        "dead_lettered": 3,
        "cumulative_dead_lettered": 3,
    }


def _stream() -> dict[str, Any]:
    """One data-load row shaped like ops_data_streams.sql returns it, dates included."""
    return {
        "kind": "warehouse signal",
        "stream": "soil_water_content_layer_1",
        "rows": 2172532,
        "coverage": 1470,
        "coverage_label": "cells",
        "from_day": date(2022, 4, 30),
        "to_day": date(2026, 4, 30),
        "observed_days": 1462,
        "span_days": 1462,
        "missing_days": 0,
        "largest_gap_days": 0,
        "median_step_days": 1.0,
        "stale_days": 100,
    }


def _walk(**overrides: Any) -> dict[str, Any]:
    """One walk row shaped like ops_historical_walks.sql returns it, parameter array included."""
    row: dict[str, Any] = {
        "data_source_key": "open-meteo-era5-land-archive",
        "walk_label": "open-meteo-era5-land-archive-daily-v1:20220802-20260802:sentinel2-ndvi-0p25deg",
        "parameters": ["vapour_pressure_deficit_max"],
        "observed_from": datetime(2022, 8, 2, tzinfo=UTC),
        "observed_to": datetime(2026, 8, 2, 23, 59, 59, tzinfo=UTC),
        "chunks_landed": 8,
        "chunks_in_window": 8,
        "started_at": datetime(2026, 8, 1, tzinfo=UTC),
        "last_chunk_at": datetime(2026, 8, 2, tzinfo=UTC),
        "cells_done": _WALK_CELLS_DONE,
        "cells_in_window": 24,
        "unresolved_cells": 0,
        "observed_value_count": _WALK_OBSERVED_VALUES,
        "grid_name": "sentinel2-ndvi-0p25deg",
        "target_cells": _WALK_TARGET_CELLS,
    }
    row.update(overrides)
    return row


def _lane_evidence(**overrides: Any) -> dict[str, Any]:
    """One landed-evidence row shaped like ops_lane_landed_evidence.sql returns it."""
    row: dict[str, Any] = {
        "stream": "fire-detections",
        "last_write_at": datetime(2026, 8, 9, 1, 58, tzinfo=UTC),
        "rows_in_window": _EVIDENCE_ROWS_IN_WINDOW,
        "oldest_day_written_recently": date(2018, 11, 2),
        "newest_day_written_recently": date(2024, 6, 5),
    }
    row.update(overrides)
    return row


def _forecast_stage(**overrides: Any) -> dict[str, Any]:
    """One stage row shaped like ops_forecast_state.sql returns it, breadth array included."""
    row: dict[str, Any] = {
        "stage_ordinal": 5,
        "plane": "iteration",
        "stage": "iterations",
        "relation": "agri.forecast_iteration",
        "row_count": _ITERATION_ROWS,
        "last_activity_at": datetime(2026, 8, 5, 20, 16, tzinfo=UTC),
        "frontier_at": datetime(2026, 8, 5, 20, 8, tzinfo=UTC),
        "frontier_label": "as-of",
        "breadth": ["ndvi_seasonal_anomaly_bootstrap_v1"],
        "breadth_label": "methods",
        "covered_count": None,
        "coverage_of_count": None,
        "coverage_label": None,
    }
    row.update(overrides)
    return row


def _platform_metric(**overrides: Any) -> dict[str, Any]:
    """One aggregate row shaped like ops_platform_activity.sql returns it."""
    row: dict[str, Any] = {
        "metric_ordinal": 1,
        "section": "accounts",
        "metric": "registered accounts",
        "relation": "public.users",
        "total_count": 1,
        "last_24h": 0,
        "last_7d": 1,
        "last_activity_at": datetime(2026, 8, 3, 15, 34, tzinfo=UTC),
        "window_basis": "created_at",
    }
    row.update(overrides)
    return row


def _source(**overrides: Any) -> dict[str, Any]:
    """One catalogued source shaped like ops_unarmed_sources.sql returns it."""
    row: dict[str, Any] = {
        "source_key": "nasa-power-daily",
        "source_name": "NASA POWER Daily",
        "is_active": True,
        "review_state": "approved",
        "release_count": 1600,
        "first_release_at": datetime(2026, 8, 5, 3, 9, tzinfo=UTC),
        "last_release_at": datetime(2026, 8, 9, 5, 45, tzinfo=UTC),
        "landed_release_count": 1600,
        "last_landed_release_at": datetime(2026, 8, 9, 5, 45, tzinfo=UTC),
        "zero_landing_releases": 0,
        "newest_zero_landing_release_at": None,
        "newest_observation_at": datetime(2026, 8, 6, tzinfo=UTC),
        "landing_planes": ["signal_observation"],
    }
    row.update(overrides)
    return row


def _snapshot(generated_at: datetime, **panels: Any) -> Any:
    """Build a snapshot carrying only the panel under test; the rest stay honestly empty."""
    return ops_route._Snapshot(
        generated_at=generated_at,
        throughput_window_hours=ops_route._DEFAULT_THROUGHPUT_WINDOW_HOURS,
        lanes=panels.get("lanes", []),
        walks=panels.get("walks", []),
        failures=panels.get("failures", []),
        dead_letter_trend=panels.get("dead_letter_trend", []),
        streams=panels.get("streams", []),
        forecast_stages=panels.get("forecast_stages", []),
        platform_activity=panels.get("platform_activity", []),
        sources=panels.get("sources", []),
        error=None,
    )


def _request(**query: str) -> Any:
    return SimpleNamespace(args=query)


@pytest.mark.asyncio
async def test_snapshot_json_derives_rate_and_eta_and_stays_json_serializable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stalled_lane = _lane(
        definition_name="streamflow-archive",
        logical_run_key="streamflow-archive:2000-11-01",
        succeeded_in_window=0,
        outstanding_items=5,
    )
    # The stream rows carry bare `date` objects, which is the whole point of including them
    # here: datetime subclasses date, so a serializer that handles only datetime passes every
    # other test in this file and still 500s the moment a data-load row reaches it.
    session = _Session(
        lanes=[_lane(), stalled_lane],
        failures=[_failure()],
        trend=[_trend()],
        streams=[_stream()],
    )
    monkeypatch.setattr(ops_route, "receiver_writer_session", _session_factory(session))

    response = await ops_route.backfill_snapshot_json(_request())
    payload = json.loads(response.body)

    assert response.status == _HTTP_OK
    assert payload["error"] is None
    assert payload["data_streams"][0]["from_day"] == "2022-04-30"
    assert payload["data_streams"][0]["to_day"] == "2026-04-30"
    assert payload["throughput_window_hours"] == ops_route._DEFAULT_THROUGHPUT_WINDOW_HOURS
    moving, stalled = payload["lanes"]
    assert moving["throughput_per_hour"] == 1.0
    assert moving["completion_percent"] == _EXPECTED_COMPLETION_PERCENT
    assert moving["eta_hours"] == _EXPECTED_ETA_HOURS
    assert "T" in moving["eta_ready_at"]
    # Nothing succeeded in the window, so the ETA is unknowable and must not be faked.
    assert stalled["throughput_per_hour"] == 0.0
    assert stalled["eta_hours"] is None
    assert stalled["eta_ready_at"] is None
    assert moving["job_run_id"] == str(_RUN_ID)
    assert moving["last_recorded_activity_at"] == "2026-08-07T11:45:00+00:00"
    assert payload["failures"][0]["shard_key"] == "2000-11-03"
    assert payload["dead_letter_trend"][0]["cumulative_dead_lettered"] == _EXPECTED_TREND_CUMULATIVE
    assert "not the last cron tick" in payload["activity_note"]
    assert session.parameters[0] == {"throughput_window_hours": ops_route._DEFAULT_THROUGHPUT_WINDOW_HOURS}


def test_every_ops_statement_binds_only_the_parameters_its_caller_supplies() -> None:
    """A colon glued to a word in a comment mints a phantom bind that fails at execution.

    The walk statement's header quotes two regular expressions and two version strings that
    really do contain colons, so this is the assertion that keeps that header honest.
    """
    assert set(ops_route._WALKS_SQL._bindparams) == {"throughput_window_hours"}
    assert set(ops_route._LANES_SQL._bindparams) == {"throughput_window_hours"}
    assert set(ops_route._LANE_EVIDENCE_SQL._bindparams) == {"throughput_window_hours", "stream_names"}
    # The three health panels bind nothing at all, and their headers quote clock times and type
    # casts that all contain colons -- which is exactly the shape that mints a phantom bind.
    assert set(ops_route._FORECAST_STATE_SQL._bindparams) == set()
    assert set(ops_route._PLATFORM_ACTIVITY_SQL._bindparams) == set()
    assert set(ops_route._UNARMED_SOURCES_SQL._bindparams) == set()


def test_each_ledger_lane_is_mapped_to_the_stream_it_fills_by_the_shared_catalog() -> None:
    """The lane-to-stream map is derived, so a renamed lane cannot silently lose its evidence.

    Spelling either name by hand here would defeat the point: a literal that stopped matching the
    ledger would produce no evidence row, and no evidence reads exactly like a lane nobody mapped.
    """
    assert ops_route._LANE_STREAMS[_FIRMS_LANE] == "fire-detections"
    assert ops_route._LANE_STREAMS[_STREAMFLOW_LANE] == "water-gauges"


def test_walk_record_derives_completion_cell_rate_and_eta() -> None:
    generated_at = datetime(2026, 8, 9, 2, tzinfo=UTC)

    record = ops_route._walk_record(_walk(), generated_at, ops_route._DEFAULT_THROUGHPUT_WINDOW_HOURS)

    assert record["completion_percent"] == _EXPECTED_WALK_COMPLETION_PERCENT
    assert record["cells_per_hour"] == 1.0
    assert record["outstanding_cells"] == _EXPECTED_WALK_OUTSTANDING_CELLS
    assert record["eta_hours"] == _EXPECTED_WALK_ETA_HOURS
    assert record["eta_ready_at"] == generated_at + timedelta(hours=_EXPECTED_WALK_ETA_HOURS)


def test_walk_record_keeps_an_unknown_denominator_and_a_stalled_rate_honest() -> None:
    generated_at = datetime(2026, 8, 9, 2, tzinfo=UTC)

    # No resolvable lattice, so there is no denominator to divide by and none is invented.
    unknown_grid = ops_route._walk_record(
        _walk(grid_name=None, target_cells=None),
        generated_at,
        ops_route._DEFAULT_THROUGHPUT_WINDOW_HOURS,
    )
    # A known denominator but nothing covered in the window; the remaining work is real and
    # the rate is zero, which makes the ETA unknowable rather than infinite.
    stalled = ops_route._walk_record(
        _walk(cells_in_window=0),
        generated_at,
        ops_route._DEFAULT_THROUGHPUT_WINDOW_HOURS,
    )

    assert unknown_grid["target_cells"] is None
    assert unknown_grid["outstanding_cells"] is None
    assert unknown_grid["completion_percent"] is None
    assert unknown_grid["eta_hours"] is None
    assert unknown_grid["eta_ready_at"] is None
    assert stalled["cells_per_hour"] == 0.0
    assert stalled["eta_hours"] is None
    assert stalled["eta_ready_at"] is None
    # The missing denominator renders as an em dash beside the cells actually done.
    cells_cell = f'{_WALK_CELLS_DONE:,}<span class="dim">/{ops_route._EMPTY}</span>'
    assert cells_cell in ops_route._render_walk_row(unknown_grid, generated_at)


def test_walk_state_separates_complete_active_and_idle() -> None:
    generated_at = datetime(2026, 8, 9, 2, tzinfo=UTC)
    window_hours = ops_route._DEFAULT_THROUGHPUT_WINDOW_HOURS

    complete = ops_route._walk_record(_walk(cells_done=_WALK_TARGET_CELLS), generated_at, window_hours)
    active = ops_route._walk_record(
        _walk(last_chunk_at=generated_at - timedelta(minutes=2)),
        generated_at,
        window_hours,
    )
    idle = ops_route._walk_record(_walk(), generated_at, window_hours)

    assert (complete["state"], active["state"], idle["state"]) == ("complete", "active", "idle")
    assert 'pill ok">complete' in ops_route._render_walk_row(complete, generated_at)
    assert 'pill info">active' in ops_route._render_walk_row(active, generated_at)
    assert 'pill warn">idle' in ops_route._render_walk_row(idle, generated_at)


def test_a_walk_whose_chunks_carried_no_values_never_reads_as_complete() -> None:
    """The production pair: same label, same 397 of 397, same 8 chunks, one of them empty.

    The empty release set is immutable provenance and stays on the page, so the only thing that
    can separate the two rows is what they actually landed.
    """
    generated_at = datetime(2026, 8, 9, 2, tzinfo=UTC)
    window_hours = ops_route._DEFAULT_THROUGHPUT_WINDOW_HOURS
    label = "open-meteo-era5-land-archive-daily-v1:20220802-20260802:nasa-power-0.5-degree"
    landed = _walk(
        data_source_key="open-meteo-era5-archive",
        walk_label=label,
        cells_done=_WALK_TARGET_CELLS,
        observed_value_count=_REAL_RADIATION_VALUES,
    )
    empty = _walk(
        data_source_key="open-meteo-era5-land-archive",
        walk_label=label,
        cells_done=_WALK_TARGET_CELLS,
        observed_value_count=0,
    )

    landed_record = ops_route._walk_record(landed, generated_at, window_hours)
    empty_record = ops_route._walk_record(empty, generated_at, window_hours)
    landed_row = ops_route._render_walk_row(landed_record, generated_at)
    empty_row = ops_route._render_walk_row(empty_record, generated_at)

    assert landed_record["state"] == "complete"
    assert empty_record["state"] == "no_data"
    assert 'pill bad">no_data' in empty_row
    assert f'<td class="n">{_REAL_RADIATION_VALUES:,}</td>' in landed_row
    assert '<td class="n bad">0</td>' in empty_row
    # Both walks share a label, so the source key is the only thing that names which is which.
    assert "open-meteo-era5-archive</span>" in landed_row
    assert "open-meteo-era5-land-archive</span>" in empty_row


def test_a_deliberate_subset_walk_still_reads_as_a_low_but_data_bearing_walk() -> None:
    """The two superseded 4-cell soil-wetness pilots.

    Low completion against the full lattice is the documented, deliberate behaviour of the walk
    query for any plan targeting a subset, and it is NOT changed here. What the rows column adds
    is the one thing that was genuinely missing: a pilot that really did land data no longer
    reads like the chunks-complete-but-empty release set two rows below it.
    """
    generated_at = datetime(2026, 8, 9, 2, tzinfo=UTC)

    pilot = ops_route._walk_record(
        _walk(
            data_source_key="nasa-power-daily",
            walk_label="nasa-power-daily-v1:20220805-20260805",
            parameters=["GWETPROF", "GWETROOT", "GWETTOP"],
            cells_done=_PILOT_CELLS_DONE,
            target_cells=_PILOT_TARGET_CELLS,
            chunks_landed=_PILOT_CELLS_DONE,
            observed_value_count=_PILOT_OBSERVED_VALUES,
        ),
        generated_at,
        ops_route._DEFAULT_THROUGHPUT_WINDOW_HOURS,
    )

    assert pilot["state"] == "idle"
    assert pilot["completion_percent"] == pytest.approx(_EXPECTED_PILOT_COMPLETION_PERCENT)
    assert f'<td class="n">{_PILOT_OBSERVED_VALUES:,}</td>' in ops_route._render_walk_row(pilot, generated_at)


def test_a_quiet_ledger_is_flagged_only_while_the_run_still_owes_work() -> None:
    """The frozen archive-walk lanes are the case; a finished campaign's silence is not a fault."""
    generated_at = datetime(2026, 8, 9, 14, tzinfo=UTC)
    window_hours = ops_route._DEFAULT_THROUGHPUT_WINDOW_HOURS
    # The production shape: 93 of 1,882 windows settled, nothing recorded since 2026-08-08 03:45.
    frozen = _lane(
        definition_name=_FIRMS_LANE,
        last_recorded_activity_at=datetime(2026, 8, 8, 3, 45, tzinfo=UTC),
        succeeded_in_window=0,
    )

    stale = ops_route._lane_record(frozen, generated_at, window_hours)
    fresh = ops_route._lane_record(
        _lane(definition_name=_FIRMS_LANE, last_recorded_activity_at=generated_at - timedelta(hours=1)),
        generated_at,
        window_hours,
    )
    finished = ops_route._lane_record(
        _lane(
            definition_name=_FIRMS_LANE,
            outstanding_items=0,
            last_recorded_activity_at=datetime(2026, 8, 8, 3, 45, tzinfo=UTC),
        ),
        generated_at,
        window_hours,
    )
    # No ledger timestamp at all and no run start either: there is nothing to measure silence
    # against, so nothing is claimed.
    unmeasurable = ops_route._lane_record(
        _lane(definition_name=_FIRMS_LANE, last_recorded_activity_at=None, run_started_at=None),
        generated_at,
        window_hours,
    )

    assert stale["ledger_stale"] is True
    assert stale["ledger_quiet_hours"] == pytest.approx(_EXPECTED_QUIET_HOURS)
    assert fresh["ledger_stale"] is False
    assert finished["ledger_stale"] is False
    assert unmeasurable["ledger_stale"] is False
    assert unmeasurable["ledger_quiet_hours"] is None


def test_a_settled_run_is_never_stale_however_long_its_ledger_stays_silent() -> None:
    """A dead letter is settled work, so it must not keep a finished campaign owing forever.

    `outstanding_items` is `status NOT IN ('succeeded', 'cancelled')` and therefore counts
    dead_letter, while the ledger's own finalizer (sql/jobs/refresh_job_run_rollup.sql) folds
    dead_letter into `failed` and closes the run at succeeded + failed = total. Keying the flag
    off outstanding_items alone put a permanent red pill on every campaign that ever spent a
    window's retry budget -- the always-on warning this panel was built to remove.
    """
    generated_at = datetime(2026, 8, 9, 14, tzinfo=UTC)
    window_hours = ops_route._DEFAULT_THROUGHPUT_WINDOW_HOURS
    silent_since = datetime(2026, 8, 8, 3, 45, tzinfo=UTC)
    # 98 of 100 windows succeeded and the other 2 spent their retry budget: nothing is claimable,
    # yet outstanding_items is 2 and stays 2 for the life of the ledger.
    settled = {
        "succeeded_items": 98,
        "dead_letter_items": 2,
        "outstanding_items": 2,
        "queued_items": 0,
        "leased_items": 0,
        "running_items": 0,
        "retry_wait_items": 0,
        "deferred_items": 0,
        "last_recorded_activity_at": silent_since,
        "succeeded_in_window": 0,
    }

    # Both marks the ledger writes for a settled run, each on its own, plus the case the rollup
    # has not caught up on at all -- an abandoned driver never calls the finalizer, so a fully
    # dead-lettered run can sit at 'running' with no completion time forever.
    by_status = ops_route._lane_record(
        _lane(definition_name=_FIRMS_LANE, run_status="partial", **settled),
        generated_at,
        window_hours,
    )
    by_completion = ops_route._lane_record(
        _lane(definition_name=_FIRMS_LANE, run_completed_at=silent_since, **settled),
        generated_at,
        window_hours,
    )
    unfinalized = ops_route._lane_record(
        _lane(definition_name=_FIRMS_LANE, run_status="running", run_completed_at=None, **settled),
        generated_at,
        window_hours,
    )
    # The control: the same silence, but two windows are still queued, so the flag must fire.
    still_owing = ops_route._lane_record(
        _lane(
            definition_name=_FIRMS_LANE,
            **{**settled, "queued_items": 2, "outstanding_items": 4, "succeeded_items": 96},
        ),
        generated_at,
        window_hours,
    )

    assert by_status["ledger_stale"] is False
    assert by_completion["ledger_stale"] is False
    assert unfinalized["ledger_stale"] is False
    assert unfinalized["owed_items"] == 0
    assert still_owing["ledger_stale"] is True
    assert still_owing["owed_items"] == _EXPECTED_OWED_ITEMS
    # The pill and the whole landed-evidence line go with it -- nothing about a settled run reads
    # as a live problem.
    assert "ledger stale" not in ops_route._render_lane_row(by_status, generated_at)
    assert "ledger quiet" not in ops_route._render_lane_row(unfinalized, generated_at)


def test_only_a_lanes_newest_run_can_be_flagged_or_wear_the_landed_line() -> None:
    """Lowering a lane's floor mints a second run and leaves the first in the ledger forever.

    The evidence line is keyed by STREAM, not by run, so a superseded run allowed to wear it would
    print the current run's live activity beside a campaign that stopped weeks ago.
    """
    generated_at = datetime(2026, 8, 9, 14, tzinfo=UTC)
    silent_since = datetime(2026, 8, 8, 3, 45, tzinfo=UTC)
    superseded_id = UUID("99999999-8888-7777-6666-555555555555")
    rows = [
        _lane(
            definition_name=_FIRMS_LANE,
            scheduled_for=datetime(2026, 8, 5, tzinfo=UTC),
            last_recorded_activity_at=silent_since,
        ),
        _lane(
            definition_name=_FIRMS_LANE,
            job_run_id=superseded_id,
            logical_run_key="firms-archive:2000-11-01/superseded",
            scheduled_for=datetime(2026, 7, 1, tzinfo=UTC),
            last_recorded_activity_at=datetime(2026, 7, 2, tzinfo=UTC),
        ),
    ]

    current_ids = ops_route._current_run_ids(rows)
    current = ops_route._lane_record(
        rows[0],
        generated_at,
        ops_route._DEFAULT_THROUGHPUT_WINDOW_HOURS,
        lane_evidence={"fire-detections": _lane_evidence()},
        is_current_run=rows[0]["job_run_id"] in current_ids,
    )
    superseded = ops_route._lane_record(
        rows[1],
        generated_at,
        ops_route._DEFAULT_THROUGHPUT_WINDOW_HOURS,
        lane_evidence={"fire-detections": _lane_evidence()},
        is_current_run=rows[1]["job_run_id"] in current_ids,
    )

    assert current_ids == frozenset({_RUN_ID})
    assert current["ledger_stale"] is True
    assert superseded["ledger_stale"] is False
    rendered = ops_route._render_lane_row(superseded, generated_at)
    assert "ledger stale" not in rendered
    assert f"{_EVIDENCE_ROWS_IN_WINDOW:,} rows" not in rendered


def test_a_stale_lane_reports_what_landed_and_stops_its_counters_reading_as_live() -> None:
    generated_at = datetime(2026, 8, 9, 14, tzinfo=UTC)
    record = ops_route._lane_record(
        _lane(
            definition_name=_FIRMS_LANE,
            last_recorded_activity_at=datetime(2026, 8, 8, 3, 45, tzinfo=UTC),
            succeeded_in_window=0,
        ),
        generated_at,
        ops_route._DEFAULT_THROUGHPUT_WINDOW_HOURS,
        lane_evidence={"fire-detections": _lane_evidence()},
    )

    rendered = ops_route._render_lane_row(record, generated_at)

    assert record["landed_stream"] == "fire-detections"
    assert record["landed_rows_in_window"] == _EVIDENCE_ROWS_IN_WINDOW
    assert 'pill warn">ledger stale' in rendered
    # The ledger's own last word survives, but nothing about it reads as current any more.
    assert f'<td class="n dim">{40:,}</td>' in rendered
    assert '<td class="n dim">40.0%</td>' in rendered
    # What is actually happening, in the store. Both spans are labelled, because they are NOT the
    # same span: the count rides the rate window and the day pair is a fixed hour.
    assert "ledger quiet 34h15m" in rendered
    assert (
        f'<span class="ok">{_EVIDENCE_ROWS_IN_WINDOW:,} rows</span> landed stream-wide in fire-detections (last 24h)'
    ) in rendered
    assert '<span class="ok">writing (last hour) 2018-11-02 &rarr; 2024-06-05</span>' in rendered


def test_a_stale_lane_with_nothing_landing_says_so_rather_than_implying_health() -> None:
    generated_at = datetime(2026, 8, 9, 14, tzinfo=UTC)
    quiet_ledger = _lane(
        definition_name=_FIRMS_LANE,
        last_recorded_activity_at=datetime(2026, 8, 8, 3, 45, tzinfo=UTC),
        succeeded_in_window=0,
    )

    silent = ops_route._render_lane_row(
        ops_route._lane_record(
            quiet_ledger,
            generated_at,
            ops_route._DEFAULT_THROUGHPUT_WINDOW_HOURS,
            lane_evidence={
                "fire-detections": _lane_evidence(
                    rows_in_window=0,
                    oldest_day_written_recently=None,
                    newest_day_written_recently=None,
                )
            },
        ),
        generated_at,
    )
    # A lane the catalog maps to nothing must not borrow another lane's reassurance.
    unmapped = ops_route._render_lane_row(
        ops_route._lane_record(
            _lane(last_recorded_activity_at=datetime(2026, 8, 8, 3, 45, tzinfo=UTC)),
            generated_at,
            ops_route._DEFAULT_THROUGHPUT_WINDOW_HOURS,
            lane_evidence={"fire-detections": _lane_evidence()},
        ),
        generated_at,
    )

    assert '<span class="warn">no rows</span> landed stream-wide in fire-detections (last 24h)' in silent
    assert "writing" not in silent
    assert "no stream is mapped to this lane" in unmapped


def test_a_healthy_lane_carries_no_stale_marker_and_no_landed_line() -> None:
    generated_at = datetime(2026, 8, 7, 12, tzinfo=UTC)

    rendered = ops_route._render_lane_row(
        ops_route._lane_record(
            _lane(definition_name=_FIRMS_LANE),
            generated_at,
            ops_route._DEFAULT_THROUGHPUT_WINDOW_HOURS,
            lane_evidence={"fire-detections": _lane_evidence()},
        ),
        generated_at,
    )

    assert "ledger stale" not in rendered
    assert "ledger quiet" not in rendered
    assert "fire-detections" not in rendered
    assert '<td class="n ok">40</td>' in rendered


def test_lane_row_says_how_long_ago_the_run_started() -> None:
    generated_at = datetime(2026, 8, 7, 12, tzinfo=UTC)
    window_hours = ops_route._DEFAULT_THROUGHPUT_WINDOW_HOURS

    started = ops_route._render_lane_row(ops_route._lane_record(_lane(), generated_at, window_hours), generated_at)
    never_started = ops_route._render_lane_row(
        ops_route._lane_record(_lane(run_started_at=None), generated_at, window_hours),
        generated_at,
    )

    assert '<span class="dim">started 6d12h ago</span>' in started
    assert f'<span class="dim">started {ops_route._EMPTY}</span>' in never_started


@pytest.mark.asyncio
async def test_snapshot_json_carries_the_historical_walks_the_ledger_cannot_see(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(lanes=[_lane()], failures=[], trend=[], walks=[_walk()])
    monkeypatch.setattr(ops_route, "receiver_writer_session", _session_factory(session))

    response = await ops_route.backfill_snapshot_json(_request())
    payload = json.loads(response.body)

    walk = payload["historical_walks"][0]
    assert walk["parameters"] == ["vapour_pressure_deficit_max"]
    assert walk["observed_from"] == "2022-08-02T00:00:00+00:00"
    assert walk["cells_done"] == _WALK_CELLS_DONE
    assert walk["target_cells"] == _WALK_TARGET_CELLS
    assert walk["completion_percent"] == _EXPECTED_WALK_COMPLETION_PERCENT
    assert walk["eta_hours"] == _EXPECTED_WALK_ETA_HOURS
    assert walk["state"] == "idle"
    assert "look identical from here" in payload["historical_walk_note"]
    # Both rate columns divide by the same trailing window, so both statements are bound with it.
    window = {"throughput_window_hours": ops_route._DEFAULT_THROUGHPUT_WINDOW_HOURS}
    assert session.parameters[:2] == [window, window]


@pytest.mark.asyncio
async def test_the_scanning_statements_are_cached_and_the_ledger_ones_are_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A five-second refresh must not re-run six full scans against production every tick."""
    session = _Session(
        lanes=[_lane()],
        failures=[],
        trend=[],
        walks=[_walk()],
        streams=[_stream()],
        lane_evidence=[_lane_evidence()],
        forecast_stages=[_forecast_stage()],
        platform_activity=[_platform_metric()],
        sources=[_source()],
    )
    monkeypatch.setattr(ops_route, "receiver_writer_session", _session_factory(session))

    await ops_route.backfill_snapshot_json(_request())
    await ops_route.backfill_snapshot_json(_request())

    assert len(session.parameters) == _COLD_SNAPSHOT_STATEMENTS + _WARM_SNAPSHOT_STATEMENTS
    # The evidence statement asks only about the streams the shared catalog maps to a lane.
    assert session.parameters[2]["stream_names"] == sorted({"fire-detections", "water-gauges"})


@pytest.mark.asyncio
async def test_every_snapshot_read_is_capped_by_a_transaction_local_statement_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/ops` is unauthenticated and three of its statements grow with the archive.

    The cap is pinned first, so every read in the transaction inherits it and an unbounded scan
    fails loudly into the page's error banner instead of holding a production connection open.
    """
    session = _Session(
        lanes=[_lane()],
        failures=[],
        trend=[],
        walks=[_walk()],
        streams=[_stream()],
        lane_evidence=[_lane_evidence()],
    )
    monkeypatch.setattr(ops_route, "receiver_writer_session", _session_factory(session))

    await ops_route.backfill_snapshot_json(_request())

    assert session.session_settings == [
        f"-- ops_statement_timeout\nSET LOCAL statement_timeout = '{ops_route._STATEMENT_TIMEOUT_SECONDS}s'"
    ]


@pytest.mark.asyncio
async def test_the_page_explains_a_stale_ledger_only_when_a_lane_is_actually_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen_lane = _lane(
        definition_name=_FIRMS_LANE,
        last_recorded_activity_at=datetime(2026, 8, 8, 3, 45, tzinfo=UTC),
        succeeded_in_window=0,
    )
    session = _Session(
        lanes=[frozen_lane],
        failures=[],
        trend=[],
        walks=[_walk()],
        lane_evidence=[_lane_evidence()],
    )
    monkeypatch.setattr(ops_route, "receiver_writer_session", _session_factory(session))

    stale_body = (await ops_route.backfill_dashboard(_request())).body.decode()
    ops_route._QUERY_CACHE.clear()
    # The handler stamps its own snapshot with the real clock, so "recently" has to be measured
    # against that clock rather than against the fixture's frozen 2026-08-07.
    session.lanes = [_lane(last_recorded_activity_at=datetime.now(UTC) - timedelta(minutes=5))]
    healthy_body = (await ops_route.backfill_dashboard(_request())).body.decode()

    assert "are marked ledger stale" in stale_body
    assert f'<span class="ok">{_EVIDENCE_ROWS_IN_WINDOW:,} rows</span>' in stale_body
    assert "are marked ledger stale" not in healthy_body


@pytest.mark.asyncio
async def test_snapshot_reports_a_failed_ledger_read_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_route, "receiver_writer_session", _session_factory(_FailingSession()))

    response = await ops_route.backfill_snapshot_json(_request())
    payload = json.loads(response.body)

    assert response.status == _HTTP_OK
    assert payload["lanes"] == []
    assert "PermissionError" in payload["error"]


@pytest.mark.asyncio
async def test_backfill_page_serves_html_with_the_stream_wired_and_error_text_escaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(lanes=[_lane()], failures=[_failure()], trend=[_trend()], walks=[_walk()])
    monkeypatch.setattr(ops_route, "receiver_writer_session", _session_factory(session))

    response = await ops_route.backfill_dashboard(_request(interval="99"))
    body = response.body.decode()

    assert response.status == _HTTP_OK
    assert "text/html" in response.content_type
    assert '<div id="ops-stream"' in body
    # 99 clamps to the 30-second ceiling, and the stream URL carries the clamped value.
    assert "/ops/backfill/stream?interval=30" in body
    assert ops_route._DATASTAR_URL in body
    assert all(region in body for region in ('id="ops-meta"', 'id="ops-lanes"', 'id="ops-walks"', 'id="ops-failures"'))
    assert "firms-archive" in body
    # The walks section renders between the lanes and the data loads, both clocks included.
    assert body.index('id="ops-lanes"') < body.index('id="ops-walks"') < body.index('id="ops-streams"')
    assert "vapour_pressure_deficit_max" in body
    assert '<span class="dim">started ' in body
    # Ledger error text is escaped, so a hostile error summary cannot inject markup.
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "<script>" not in body.split("</head>")[1]


def test_stream_frames_each_region_as_a_datastar_element_patch() -> None:
    event = ops_route._patch_elements_event("#ops-lanes", '<section id="ops-lanes">x</section>')

    assert event.startswith("event: datastar-patch-elements\n")
    assert "data: selector #ops-lanes\n" in event
    assert "data: mode outer\n" in event
    assert 'data: elements <section id="ops-lanes">x</section>' in event
    assert event.endswith("\n\n")


def test_datastar_bundle_is_vendored_next_to_the_service() -> None:
    assert ops_route._DATASTAR_PATH.is_file()
    assert "Datastar" in ops_route._DATASTAR_PATH.read_text(encoding="utf-8")[:64]


def test_a_stage_with_no_rows_has_never_executed_rather_than_merely_gone_quiet() -> None:
    """The production shape: one bootstrap method ran four days ago, the plane above it never did.

    Those are different findings and the panel exists because nothing else on the page can tell
    them apart -- the forecast plane enqueues no ledger item, so a stage that has never run and a
    stage nobody has looked at are indistinguishable without counting the rows.
    """
    generated_at = datetime(2026, 8, 9, 14, tzinfo=UTC)

    never = ops_route._forecast_stage_record(
        _forecast_stage(
            plane="governed",
            stage="backtest metrics",
            relation="agri.forecast_backtest_metric",
            row_count=0,
            last_activity_at=None,
            frontier_at=None,
            frontier_label="cutoff",
            breadth=None,
            breadth_label=None,
        ),
        generated_at,
    )
    ran_and_went_stale = ops_route._forecast_stage_record(_forecast_stage(), generated_at)
    live = ops_route._forecast_stage_record(
        _forecast_stage(last_activity_at=generated_at - timedelta(hours=1)),
        generated_at,
    )
    dormant = ops_route._forecast_stage_record(
        _forecast_stage(last_activity_at=generated_at - timedelta(days=30)),
        generated_at,
    )

    assert (never["state"], ran_and_went_stale["state"]) == ("never", "stale")
    assert (live["state"], dormant["state"]) == ("live", "dormant")
    never_row = ops_route._render_forecast_row(never, generated_at)
    stale_row = ops_route._render_forecast_row(ran_and_went_stale, generated_at)
    assert 'pill bad">never' in never_row
    # The zero itself is tinted, because it is the finding rather than a missing measurement.
    assert '<td class="n bad">0</td>' in never_row
    # A stage with no rows has no frontier to print and none is invented from its label.
    assert f"<td>{ops_route._EMPTY}</td>" in never_row
    assert 'pill warn">stale' in stale_row
    assert f'<td class="n">{_ITERATION_ROWS:,}</td>' in stale_row
    # Breadth is what says the plane is one method wide, and it is read from the data.
    assert "methods: ndvi_seasonal_anomaly_bootstrap_v1" in stale_row


def test_the_scored_share_counts_iterations_and_not_actual_rows() -> None:
    """473 actuals over 1,676 iterations reads 28%; the truth is 118 iterations, so 7%.

    One actual is recorded per predicted point and one iteration holds many points, so dividing
    rows by iterations overstates coverage fourfold. The panel divides parents for that reason and
    prints both terms under the share so the arithmetic is checkable from the page.
    """
    generated_at = datetime(2026, 8, 9, 14, tzinfo=UTC)

    scored = ops_route._forecast_stage_record(
        _forecast_stage(
            stage="actuals scored",
            relation="agri.forecast_iteration_actual",
            row_count=_ACTUAL_ROWS,
            breadth=None,
            breadth_label=None,
            covered_count=_ITERATIONS_SCORED,
            coverage_of_count=_ITERATION_ROWS,
            coverage_label="iterations scored",
        ),
        generated_at,
    )
    unscorable = ops_route._forecast_stage_record(_forecast_stage(), generated_at)

    assert scored["coverage_percent"] == pytest.approx(_EXPECTED_SCORED_PERCENT)
    assert scored["coverage_percent"] < _ROW_COUNT_IF_SCORED_BY_ACTUAL_ROWS
    # A stage with no ratio at all carries None, not a zero share of nothing.
    assert unscorable["coverage_percent"] is None
    rendered = ops_route._render_forecast_row(scored, generated_at)
    assert f'<span class="warn">{_EXPECTED_SCORED_PERCENT:.1f}%</span>' in rendered
    assert f"{_ITERATIONS_SCORED:,}/{_ITERATION_ROWS:,} iterations scored" in rendered
    assert f'<td class="n">{ops_route._EMPTY}</td>' in ops_route._render_forecast_row(unscorable, generated_at)


def test_the_forecast_panel_draws_a_band_per_plane_so_the_waterline_is_visible() -> None:
    """The finding is WHERE "has run" stops and "has never run" starts, not the nineteen rows.

    Flat, with the plane repeated in a column, that boundary can only be found by scanning every
    row. A band header per plane states each plane's tally outright, so a plane in which nothing
    has ever executed announces itself.
    """
    generated_at = datetime(2026, 8, 9, 14, tzinfo=UTC)
    stages = [
        ops_route._forecast_stage_record(_forecast_stage(), generated_at),
        ops_route._forecast_stage_record(
            _forecast_stage(
                stage="actuals scored",
                row_count=_ACTUAL_ROWS,
                covered_count=_ITERATIONS_SCORED,
                coverage_of_count=_ITERATION_ROWS,
                coverage_label="iterations scored",
            ),
            generated_at,
        ),
        ops_route._forecast_stage_record(
            _forecast_stage(plane="governed", stage="serving values", row_count=0, last_activity_at=None),
            generated_at,
        ),
        ops_route._forecast_stage_record(
            _forecast_stage(plane="governed", stage="publications", row_count=0, last_activity_at=None),
            generated_at,
        ),
    ]

    rendered = ops_route._render_forecast(_snapshot(generated_at, forecast_stages=stages))

    assert '<span class="label">never executed</span><span class="value">2</span>' in rendered
    assert "tile bad" in rendered
    assert f'<span class="value">{_EXPECTED_SCORED_PERCENT:.1f}%</span>' in rendered
    assert "has never had a prediction compared against what actually happened" in rendered
    # One band per plane, each carrying its own tally, and the governed band is the loud one.
    assert '<b>iteration</b> <span class="ok">2 of 2 stages have ever run</span>' in rendered
    assert '<b>governed</b> <span class="bad">0 of 2 stages have ever run</span>' in rendered
    assert rendered.index("<b>iteration</b>") < rendered.index("<b>governed</b>")
    assert rendered.count('<tr class="band">') == len({"iteration", "governed"})
    # The band spans the table, so the header row and the colspan must agree.
    assert f'colspan="{ops_route._FORECAST_TABLE_COLUMNS}"' in rendered
    assert rendered.count("<th>") + rendered.count('<th class="n">') == ops_route._FORECAST_TABLE_COLUMNS
    # The plane is no longer repeated on every row; the band above them says it once.
    assert '<td class="dim">iteration</td>' not in rendered


def test_a_partly_executed_plane_warns_rather_than_reading_as_healthy_or_dead() -> None:
    generated_at = datetime(2026, 8, 9, 14, tzinfo=UTC)
    stages = [
        ops_route._forecast_stage_record(_forecast_stage(plane="evidence"), generated_at),
        ops_route._forecast_stage_record(
            _forecast_stage(plane="evidence", stage="entity states", row_count=0, last_activity_at=None),
            generated_at,
        ),
    ]

    rendered = ops_route._render_forecast(_snapshot(generated_at, forecast_stages=stages))

    assert '<b>evidence</b> <span class="warn">1 of 2 stages have ever run</span>' in rendered


def test_a_plane_that_appears_twice_draws_twice_rather_than_being_silently_stitched() -> None:
    """Grouping is by contiguous RUN, not by key, so the page cannot reorder what the statement said.

    The statement returns its planes in contiguous blocks and the ordinal is what keeps them that
    way. If that ever stops being true, two bands is the honest rendering -- a dict-based grouping
    would quietly move rows out of pipeline order to make one band, and the page would no longer
    be a picture of the statement.
    """
    interleaved = [
        _forecast_stage(plane="evidence", stage="observations ingested"),
        _forecast_stage(plane="iteration", stage="iterations"),
        _forecast_stage(plane="evidence", stage="series registered"),
    ]

    grouped = ops_route._stages_by_plane(interleaved)

    assert [plane for plane, _ in grouped] == ["evidence", "iteration", "evidence"]
    assert [stage["stage"] for _, stages in grouped for stage in stages] == [
        "observations ingested",
        "iterations",
        "series registered",
    ]


def test_a_second_coverage_ratio_is_logged_rather_than_silently_hidden() -> None:
    """`_scored_stage` takes the first ratio row, which is only sound while there is exactly one.

    It logs instead of raising: this is a display path on a page whose value is staying up during
    an incident, so an ambiguous tile must never become a 500. The table still renders both rows.
    """
    generated_at = datetime(2026, 8, 9, 14, tzinfo=UTC)
    ratio = {"covered_count": 1, "coverage_of_count": 2, "coverage_label": "things"}
    snapshot = _snapshot(
        generated_at,
        forecast_stages=[
            ops_route._forecast_stage_record(_forecast_stage(stage="actuals scored", **ratio), generated_at),
            ops_route._forecast_stage_record(_forecast_stage(stage="a second ratio", **ratio), generated_at),
        ],
    )

    with structlog.testing.capture_logs() as captured:
        chosen = ops_route._scored_stage(snapshot)

    assert chosen is not None
    assert chosen["stage"] == "actuals scored"
    warned = [entry for entry in captured if entry["event"] == "ops_forecast_multiple_coverage_stages"]
    assert len(warned) == 1
    assert warned[0]["stages"] == ["actuals scored", "a second ratio"]
    # And the page still renders, with both rows on it.
    rendered = ops_route._render_forecast(snapshot)
    assert "a second ratio" in rendered


def test_a_source_that_lands_in_the_forecast_plane_is_never_reported_as_dead() -> None:
    """The mistake this panel had to be corrected for, pinned so it cannot come back.

    sentinel2-ndvi-l2a writes to agri.forecast_observation and NEVER to agri.signal_observation.
    An audit scoped to the signal plane calls it a source that has never landed a single row in its
    life; it holds 184,409 of them. Getting the definition of "landed" wrong turns a healthy lane
    into a red alert on the one page whose entire value is being trustworthy, so the panel names
    the planes a source lands in rather than reducing them to a boolean.
    """
    generated_at = datetime(2026, 8, 9, 14, tzinfo=UTC)

    forecast_plane_source = ops_route._source_record(
        _source(
            source_key=_FORECAST_PLANE_SOURCE,
            source_name="Sentinel-2 L2A NDVI (Copernicus)",
            release_count=1,
            landed_release_count=1,
            last_landed_release_at=datetime(2026, 8, 5, 19, 46, tzinfo=UTC),
            newest_observation_at=datetime(2026, 8, 4, tzinfo=UTC),
            landing_planes=["forecast_observation"],
        ),
        generated_at,
    )

    # It landed, so it is never `never`. Its promotion step is unarmed, which is a staleness
    # finding and reads as one -- four days since the last governed release, heading for unarmed.
    assert forecast_plane_source["has_ever_landed"] is True
    assert forecast_plane_source["state"] == "lagging"
    rendered = ops_route._render_source_row(forecast_plane_source, generated_at)
    assert "never" not in rendered
    assert "lands in forecast_observation" in rendered
    assert '<span class="ok">0</span>' in rendered  # nothing zero-landing about it
    assert "2026-08-04" in rendered


def test_an_active_source_that_never_landed_anything_is_the_loudest_state_shown() -> None:
    """No catalogued source is in this state today, and the panel must still be able to show it.

    This is the state a newly registered upstream sits in before its first successful load, and it
    is the one the page most needs to render loudly rather than quietly.
    """
    generated_at = datetime(2026, 8, 9, 14, tzinfo=UTC)

    never = ops_route._source_record(
        _source(
            source_key="upstream-never-loaded",
            source_name="An upstream whose first load has never succeeded",
            release_count=1,
            landed_release_count=0,
            last_landed_release_at=None,
            zero_landing_releases=1,
            newest_zero_landing_release_at=datetime(2026, 8, 5, 19, 46, tzinfo=UTC),
            newest_observation_at=None,
            landing_planes=[],
        ),
        generated_at,
    )
    fed = ops_route._source_record(_source(), generated_at)
    lagging = ops_route._source_record(
        _source(last_landed_release_at=generated_at - timedelta(days=4)),
        generated_at,
    )
    unarmed = ops_route._source_record(
        _source(last_landed_release_at=generated_at - timedelta(days=40)),
        generated_at,
    )
    # A source policy has withdrawn owes nobody a load, so its silence is never flagged.
    withdrawn = ops_route._source_record(
        _source(is_active=False, last_landed_release_at=generated_at - timedelta(days=40)),
        generated_at,
    )

    assert never["state"] == "never"
    assert never["has_ever_landed"] is False
    assert (fed["state"], lagging["state"], unarmed["state"]) == ("fed", "lagging", "unarmed")
    assert withdrawn["state"] == "inactive"
    rendered = ops_route._render_source_row(never, generated_at)
    assert 'pill bad">never' in rendered
    assert '<td class="n bad">0</td>' in rendered
    # Nothing has ever landed, so there is no observation day to print and none is invented.
    assert f'<td class="dim">{ops_route._EMPTY}</td>' in rendered
    # No plane line either: the state pill already says it, and a second empty statement beside
    # the first would only dilute it.
    assert "lands in" not in rendered


def test_a_zero_landing_release_is_counted_and_dated_rather_than_reading_as_healthy_work() -> None:
    """The radiation lane wrote 8 releases and no rows at 05:02, then landed the same window at 05:36.

    Both release sets are immutable provenance and stay in the warehouse, so the only thing that
    can stop the empty one reading as work is counting it -- and the age beside the count is what
    separates a closed chapter from a lane failing right now.
    """
    generated_at = datetime(2026, 8, 9, 14, tzinfo=UTC)

    failing_now = ops_route._source_record(
        _source(
            source_key="open-meteo-era5-land-archive",
            release_count=202,
            landed_release_count=202 - _ZERO_LANDING_RELEASES,
            zero_landing_releases=_ZERO_LANDING_RELEASES,
            newest_zero_landing_release_at=datetime(2026, 8, 9, 5, 3, tzinfo=UTC),
            last_landed_release_at=datetime(2026, 8, 9, 8, 6, tzinfo=UTC),
        ),
        generated_at,
    )
    clean = ops_route._source_record(_source(), generated_at)

    failing_row = ops_route._render_source_row(failing_now, generated_at)
    clean_row = ops_route._render_source_row(clean, generated_at)

    # The lane is landing rows right now AND leaking empty releases; both must be visible at once.
    assert failing_now["state"] == "fed"
    assert f'<span class="bad">{_ZERO_LANDING_RELEASES}</span>' in failing_row
    assert "newest 8h57m ago" in failing_row
    # Zero is the good case and must read as such rather than as an absence of measurement.
    assert '<span class="ok">0</span>' in clean_row
    assert "newest" not in clean_row


def test_the_platform_panel_reports_only_counts_and_says_what_it_cannot_measure() -> None:
    """Owner rule: /ops is unauthenticated, so this panel carries no identity of any kind.

    The sessions row is the one the honesty of the whole panel rests on: public.sessions records no
    creation time, so its trailing windows are unmeasurable and must render blank rather than as a
    zero that claims nobody signed in this week.
    """
    generated_at = datetime(2026, 8, 9, 14, tzinfo=UTC)
    metrics = [
        _platform_metric(),
        _platform_metric(
            metric_ordinal=5,
            section="access",
            metric="unexpired sessions",
            relation="public.sessions",
            total_count=0,
            last_24h=None,
            last_7d=None,
            last_activity_at=None,
            window_basis=None,
        ),
        _platform_metric(
            metric_ordinal=8,
            section="assistant",
            metric="ai messages",
            relation="public.ai_messages",
            total_count=0,
            last_24h=0,
            last_7d=0,
            last_activity_at=None,
        ),
    ]

    rendered = ops_route._render_activity(_snapshot(generated_at, platform_activity=metrics))

    assert "<b>unexpired sessions</b>" in rendered
    assert '<span class="warn">no creation stamp</span>' in rendered
    # Blank, not zero: an unmeasurable window is an absence of evidence.
    assert f'<td class="n">{ops_route._EMPTY}</td>' in rendered
    assert '<span class="dim">0</span>' in rendered
    assert '<span class="ok">1</span>' in rendered
    assert "RECORDS NO REQUEST LOG, ACCESS LOG OR AUDIT TABLE" in rendered


def test_the_platform_statement_reads_no_column_that_identifies_anyone() -> None:
    """The owner's scoping rule, asserted against the statement rather than against its output.

    Checking the rendered HTML cannot work: the panel's own note names the very columns it promises
    not to read, so a substring scan over the page would fail on the promise itself. The executable
    text is where the rule is either kept or broken, so the comment lines are stripped and what
    remains must name no identity column -- which is also what stops a future edit adding one.

    STRIPPING COMMENTS IS ONLY SAFE BECAUSE COMMENTS CANNOT REACH A READER, and that is a separate
    guarantee with its own test beside this one. It did not hold once: a failed panel published
    SQLAlchemy's StatementError verbatim, statement and header comments included, so this file's
    prose about which tables hold email addresses and coordinates was served to anonymous
    visitors -- a leak this test could not see by construction. The two tests are a pair; neither
    is sufficient alone.

    `email_verified` is deliberately not caught by the `email` probe below and must not be: the
    word-boundary match distinguishes an address column from a nullable verification TIMESTAMP,
    which carries no identity and is the column this panel is required to read.
    """
    statement = str(ops_route._PLATFORM_ACTIVITY_SQL)
    executable = "\n".join(line for line in statement.splitlines() if not line.lstrip().startswith("--"))
    # Quoted literals are blanked as well as comments: they are the panel's own LABELS, and one of
    # them legitimately reads 'accounts with a verified email'. A scan that could not tell a label
    # from a column reference would fail on the honest description of a count.
    columns_only = re.sub(r"'[^']*'", "''", executable)

    for column in (
        "email",
        "password",
        "name",
        "session_token",
        "key_hash",
        "geohash",
        "lat",
        "lon",
        "user_id",
        "team_id",
        "created_by",
        "slug",
        "dedupe_key",
        "content",
        "title",
    ):
        # Word boundaries, not substrings: `relation` contains "lat" and `metric_ordinal` does not
        # make a column reference. Only a whole identifier counts.
        assert not re.search(rf"\b{column}\b", columns_only), (
            f"{column} identifies someone and must not be read by an unauthenticated page"
        )


@pytest.mark.asyncio
async def test_a_failed_panel_publishes_the_error_class_and_never_the_statement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The leak this was written for: /ops is unauthenticated and StatementError carries the SQL.

    SQLAlchemy appends `[SQL: <the whole statement>]` to a StatementError's str(), so recording the
    message published 11,811 characters of internal SQL -- including this service's own header
    comments, which enumerate exactly which tables hold email addresses, names, slugs and
    coordinates -- to anyone who could reach the page. The triggering failure is the ordinary one:
    an agri-only database where `public.users` does not exist, where the panel fails on EVERY read
    and the leak is the page's default rendering.
    """
    leaky = ProgrammingError(
        statement=str(ops_route._PLATFORM_ACTIVITY_SQL),
        params={},
        orig=UndefinedTable('relation "public.users" does not exist'),
    )
    session = _Session(
        lanes=[_lane()],
        failures=[],
        trend=[],
        walks=[_walk()],
        streams=[_stream()],
        forecast_stages=[_forecast_stage()],
        sources=[_source()],
        failing_fragment="'registered accounts' as metric",
        failing_error=leaky,
    )
    monkeypatch.setattr(ops_route, "receiver_writer_session", _session_factory(session))

    payload = json.loads((await ops_route.backfill_snapshot_json(_request())).body)
    ops_route._QUERY_CACHE.clear()
    body = (await ops_route.backfill_dashboard(_request())).body.decode()
    published = payload["panel_errors"]["platform-activity"]

    # The class pair, and nothing else. It still tells an operator what broke.
    assert published == "ProgrammingError: UndefinedTable"
    assert len(published) <= _PANEL_ERROR_SANE_LENGTH
    # Nothing of the statement, its comments, or the schema they describe reaches either surface.
    assert "[SQL:" not in published
    for leaked in ("public.users", "email", "slug", "session_token", "watched_locations", "select"):
        assert leaked not in published
        assert leaked not in body.split('class="banner bad"')[1].split("</div>")[0]
    # The operator still learns which panel failed and that it failed, on both surfaces.
    assert "platform-activity read failed" in body
    assert "ProgrammingError" in body


def test_the_error_summary_reports_a_plain_exception_that_has_no_driver_error() -> None:
    """Not everything that fails is a DBAPI error; one with no `orig` reports its own class."""
    assert ops_route._panel_error_summary(PermissionError("permission denied")) == "PermissionError"
    assert ops_route._panel_error_summary(TimeoutError()) == "TimeoutError"


def test_the_error_summary_names_the_real_condition_under_a_dialect_wrapper() -> None:
    """`orig` is not always the condition, and when it is not the pair degenerates into a tautology.

    Measured against the live asyncpg driver, a missing relation arrives as a SQLAlchemy
    ProgrammingError whose `orig` is the DIALECT's own ProgrammingError -- so the published pair
    read "ProgrammingError: ProgrammingError", which says nothing the outer class alone does not.
    The condition sits one level further down, on that wrapper's `__cause__`.
    """
    condition = UndefinedTable('relation "public.users" does not exist')

    # A driver error that IS the condition: nothing to unwrap, so `orig` is published as-is.
    direct = ProgrammingError(statement="select 1", params={}, orig=condition)
    # The live shape. A distinct wrapper class is used deliberately -- with the real one the
    # assertion would pass whether or not the unwrapping happened at all.
    wrapped = ProgrammingError(statement="select 1", params={}, orig=RuntimeError("dialect wrapper"))
    wrapped.orig.__cause__ = condition

    assert ops_route._panel_error_summary(direct) == "ProgrammingError: UndefinedTable"
    assert ops_route._panel_error_summary(wrapped) == "ProgrammingError: UndefinedTable"
    assert "RuntimeError" not in ops_route._panel_error_summary(wrapped)


def test_the_error_summary_publishes_nothing_but_identifiers_however_hostile_the_exception() -> None:
    """The security property is structural, so it is asserted structurally rather than by example.

    Every expression in `_panel_error_summary` is `type(x).__name__`, which is always a Python
    identifier. This hands it an exception that puts statement text in `__str__`, `__repr__`,
    `.statement`, `.params` and `__cause__` -- every channel the message used to travel down -- and
    requires that the output still match an identifier pair and nothing else.
    """

    class _HostileError(Exception):
        statement = "select email, slug from public.users"
        params = ("secret",)

        def __str__(self) -> str:
            return "[SQL: select email from public.users]"

        def __repr__(self) -> str:
            return "[SQL: select email from public.users]"

    hostile = _HostileError()
    wrapping_hostile = ProgrammingError(
        statement=str(ops_route._PLATFORM_ACTIVITY_SQL),
        params={"secret": "value"},
        orig=_HostileError(),
    )
    wrapping_hostile.orig.__cause__ = _HostileError()

    for published in (
        ops_route._panel_error_summary(hostile),
        ops_route._panel_error_summary(wrapping_hostile),
    ):
        assert re.fullmatch(r"\w+(?:: \w+)?", published), published


@pytest.mark.asyncio
async def test_a_failed_whole_read_publishes_the_error_class_and_never_the_statement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The page banner is the same boundary as the panel banners, and was the last one left open.

    `snapshot.error` is rendered by `_render_meta` and mirrored into `payload["error"]`, and the
    statements that reach it carry their own clause-by-clause headers. One unreadable agri.job_*
    relation, or a statement timeout on the 46M-row lane read, is enough to get there.
    """
    leaky = ProgrammingError(
        statement=str(ops_route._LANES_SQL),
        params=(24,),
        orig=UndefinedTable('relation "agri.job_work_item" does not exist'),
    )
    monkeypatch.setattr(ops_route, "receiver_writer_session", _session_factory(_FailingSession(leaky)))

    payload = json.loads((await ops_route.backfill_snapshot_json(_request())).body)
    ops_route._QUERY_CACHE.clear()
    body = (await ops_route.backfill_dashboard(_request())).body.decode()

    assert payload["error"] == "ProgrammingError: UndefinedTable"
    assert len(payload["error"]) <= _PANEL_ERROR_SANE_LENGTH
    for leaked in ("[SQL:", "[parameters:", "WITH item_rollup", "job_work_item", "clause by clause"):
        assert leaked not in payload["error"]
        assert leaked not in body.split('class="banner bad"')[1].split("</div>")[0]
    # The banner still says the read failed, and still says what kind of failure it was.
    assert "ledger read failed" in body
    assert "ProgrammingError" in body
    # And the whole-read contract is otherwise unchanged: every panel is empty, none is blamed.
    assert payload["lanes"] == []
    assert payload["panel_errors"] == {}


@pytest.mark.asyncio
async def test_one_failing_panel_blanks_only_itself_and_says_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The platform tables belong to another application's migrations and need not exist here.

    A savepoint is what makes this survivable rather than merely desirable: a failed statement
    poisons its whole transaction, so without one the source panel after it would fail too and the
    page an operator opened to diagnose something else would go blank.
    """
    session = _Session(
        lanes=[_lane()],
        failures=[],
        trend=[],
        walks=[_walk()],
        streams=[_stream()],
        lane_evidence=[_lane_evidence()],
        forecast_stages=[_forecast_stage()],
        platform_activity=[_platform_metric()],
        sources=[_source()],
        failing_fragment="'registered accounts' as metric",
    )
    monkeypatch.setattr(ops_route, "receiver_writer_session", _session_factory(session))

    payload = json.loads((await ops_route.backfill_snapshot_json(_request())).body)
    savepoints_per_snapshot = session.savepoints
    ops_route._QUERY_CACHE.clear()
    body = (await ops_route.backfill_dashboard(_request())).body.decode()

    # The whole-read error contract is untouched: one panel failing is not a failed snapshot.
    assert payload["error"] is None
    assert payload["platform_activity"] == []
    assert "PermissionError" in payload["panel_errors"]["platform-activity"]
    # Every other panel, including the one whose statement runs AFTER the failure, is intact.
    assert payload["lanes"] != []
    assert payload["forecast_state"] != []
    assert payload["sources"] != []
    assert "platform-activity read failed" in body
    assert "firms-archive" in body
    assert "nasa-power-daily" in body
    # Each panel read is wrapped in its own savepoint, which is what kept the transaction usable.
    assert savepoints_per_snapshot == _HEALTH_PANELS


@pytest.mark.asyncio
async def test_the_json_snapshot_mirrors_every_rendered_panel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/backfill.json` is documented as a complete mirror, so a panel added to one goes in both."""
    session = _Session(
        lanes=[_lane()],
        failures=[],
        trend=[],
        walks=[_walk()],
        streams=[_stream()],
        forecast_stages=[
            _forecast_stage(
                stage="actuals scored",
                covered_count=_ITERATIONS_SCORED,
                coverage_of_count=_ITERATION_ROWS,
                coverage_label="iterations scored",
            )
        ],
        platform_activity=[_platform_metric()],
        sources=[_source(newest_observation_at=datetime(2026, 8, 6, tzinfo=UTC))],
    )
    monkeypatch.setattr(ops_route, "receiver_writer_session", _session_factory(session))

    payload = json.loads((await ops_route.backfill_snapshot_json(_request())).body)
    ops_route._QUERY_CACHE.clear()
    body = (await ops_route.backfill_dashboard(_request())).body.decode()

    assert payload["panel_errors"] == {}
    assert payload["forecast_state"][0]["coverage_percent"] == pytest.approx(_EXPECTED_SCORED_PERCENT)
    assert payload["forecast_state"][0]["last_activity_at"] == "2026-08-05T20:16:00+00:00"
    assert payload["forecast_state"][0]["breadth"] == ["ndvi_seasonal_anomaly_bootstrap_v1"]
    assert payload["platform_activity"][0]["metric"] == "registered accounts"
    # Not the state: the handler stamps its snapshot with the real clock, so a fixed fixture date
    # would silently change band as the wall clock moves past it.
    assert payload["sources"][0]["source_key"] == "nasa-power-daily"
    assert payload["sources"][0]["has_ever_landed"] is True
    assert payload["sources"][0]["landing_planes"] == ["signal_observation"]
    assert payload["sources"][0]["newest_observation_at"] == "2026-08-06T00:00:00+00:00"
    assert "unfalsifiable" in payload["forecast_state_note"]
    assert "aggregate counts and timestamps only" in payload["platform_activity_note"]
    assert "CANNOT SEE A SCHEDULER" in payload["source_note"]
    assert "LANDED MEANS THREE TABLES" in payload["source_note"]
    # Every region has a container on first paint, or the SSE patches have nothing to replace.
    for region in ('id="ops-unarmed"', 'id="ops-forecast"', 'id="ops-activity"'):
        assert region in body
    # Ingest first, then what is done with it, then who uses it, then the ledger's failures.
    assert body.index('id="ops-streams"') < body.index('id="ops-unarmed"')
    assert body.index('id="ops-unarmed"') < body.index('id="ops-forecast"') < body.index('id="ops-activity"')
    assert body.index('id="ops-activity"') < body.index('id="ops-failures"')
