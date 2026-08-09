"""The backfill console reports the ledger honestly and never raises on a bad read."""

# These tests stub AsyncSession, so they prove shape, derivation and rendering only --
# never that the SQL runs. Real-database coverage of the three ops queries belongs in the
# agri-sweep-db suite, alongside the other job-ledger protocol tests.

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

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
_EXPECTED_WALK_COMPLETION_PERCENT = 25.0
_EXPECTED_WALK_OUTSTANDING_CELLS = 1176
_EXPECTED_WALK_ETA_HOURS = 1176.0


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
def _clear_streams_cache() -> None:
    """Drop the data-loads cache between tests.

    It is a module global that deliberately outlives a request, so without this a test
    would read whatever the previous test left behind and pass for the wrong reason.
    """
    ops_route._STREAMS_CACHE.clear()


class _Session:
    """Answer each ops query by matching a fragment unique to its statement."""

    def __init__(
        self,
        *,
        lanes: list[dict[str, Any]],
        failures: list[dict[str, Any]],
        trend: list[dict[str, Any]],
        streams: list[dict[str, Any]] | None = None,
        walks: list[dict[str, Any]] | None = None,
    ) -> None:
        self.lanes = lanes
        self.failures = failures
        self.trend = trend
        self.streams = streams if streams is not None else []
        self.walks = walks if walks is not None else []
        self.parameters: list[dict[str, Any]] = []

    async def execute(self, statement: object, parameters: dict[str, Any]) -> _Result:
        self.parameters.append(parameters)
        rendered = str(statement)
        if "WITH item_rollup AS" in rendered:
            return _Result(self.lanes)
        if "WITH historical_release AS" in rendered:
            return _Result(self.walks)
        if "LEFT JOIN LATERAL" in rendered:
            return _Result(self.failures)
        if "WITH daily_dead_letters AS" in rendered:
            return _Result(self.trend)
        if "'warehouse signal' as kind" in rendered:
            return _Result(self.streams)
        raise AssertionError(f"unexpected statement: {rendered[:120]}")


class _FailingSession:
    async def execute(self, _statement: object, _parameters: dict[str, Any]) -> _Result:
        raise PermissionError("permission denied for table job_work_item")


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
        "grid_name": "sentinel2-ndvi-0p25deg",
        "target_cells": _WALK_TARGET_CELLS,
    }
    row.update(overrides)
    return row


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
