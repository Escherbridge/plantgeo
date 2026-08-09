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
# Lanes, walks, lane evidence, failures, dead-letter trend and data loads on a cold read; only
# the three cheap ledger statements once the three scanning ones are cached.
_COLD_SNAPSHOT_STATEMENTS = 6
_WARM_SNAPSHOT_STATEMENTS = 3


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
)


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
    ) -> None:
        self.lanes = lanes
        self.failures = failures
        self.trend = trend
        self.streams = streams if streams is not None else []
        self.walks = walks if walks is not None else []
        self.lane_evidence = lane_evidence if lane_evidence is not None else []
        self.parameters: list[dict[str, Any]] = []
        self.session_settings: list[str] = []

    async def execute(self, statement: object, parameters: dict[str, Any] | None = None) -> _Result:
        rendered = str(statement)
        # A GUC pin is not a query: recording it beside the six statements would shift every
        # index the ordering assertions below read, and it binds nothing to record anyway.
        if rendered.startswith("-- ops_statement_timeout"):
            self.session_settings.append(rendered)
            return _Result([])
        self.parameters.append(parameters if parameters is not None else {})
        for fragment, attribute in _STATEMENT_FRAGMENTS:
            if fragment in rendered:
                rows: list[dict[str, Any]] = getattr(self, attribute)
                return _Result(rows)
        raise AssertionError(f"unexpected statement: {rendered[:120]}")


class _FailingSession:
    async def execute(self, _statement: object, _parameters: dict[str, Any] | None = None) -> _Result:
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
async def test_the_three_scanning_statements_are_cached_and_the_ledger_ones_are_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A five-second refresh must not re-run three full scans against production every tick."""
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
