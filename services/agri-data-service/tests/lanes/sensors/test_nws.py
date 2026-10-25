"""`sensors-direct-forward` through real runner turns: the shipped TOML, the real S14 strategy.

Two layers, matching `tests/lanes/watersheds/test_nhdplus.py`'s own split:

- `source.fetch_sensors_day_snapshot`'s own rolling-retention arithmetic is pinned DIRECTLY, with a
  real clock argument and `source_module.fetch_station_roster` wired to raise if the retention check
  ever lets a call through -- proving "no due day past retention even asks NWS the roster"
  (`pipeline/lanes/sensors/source.py`'s module docstring) rather than merely asserting it in prose.
- Full turns go through `run_turn`, with `nws_module.fetch_sensors_day_snapshot` monkeypatched (never
  `source.py`'s, since the flow tests exercise the STRATEGY's wiring -- settle/rows/the runner's own
  S11 rules -- not the retention arithmetic the unit tests above already pin): a normal in-retention
  day, the 6-day retention edge, and a malformed-upstream refusal, one scenario per turn.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.foundation.lane_config import load_lane_configs
from agri_data_service.foundation.region import load_region
from agri_data_service.ingest.http import UpstreamPayloadError
from agri_data_service.ingest.policy import format_javascript_number
from agri_data_service.pipeline.lanes.sensors import nws as nws_module
from agri_data_service.pipeline.lanes.sensors import source as source_module
from agri_data_service.pipeline.lanes.sensors.nws import STATIONS_ENDPOINT, STRATEGY
from agri_data_service.pipeline.lanes.sensors.source import SensorsDaySnapshot, fetch_sensors_day_snapshot
from tests.lane_config.builders import REAL_LANES_DIRECTORY
from tests.runner.fakes import ManualClock, MemoryLaneStore, ScriptedUpstream, ports_for, run, spec_for, unwritten_by_day

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.foundation.lane_config.models import LaneConfig

LANE_ID = "sensors-direct-forward"
STREAM = "sensors"
#: Matches the environment's own "today" so the shipped lane's forward window is easy to eyeball:
#: edge = min(today - publication_lag_days, today - 1) = 2026-10-23; window = [2026-10-18, 2026-10-23].
NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
WINDOW_FIRST = date(2026, 10, 18)
WINDOW_LAST = date(2026, 10, 23)


def _unused_roster(*_args: object, **_kwargs: object) -> Any:
    raise AssertionError("fetch_sensors_day_snapshot must never ask NWS's roster for a day past its own retention")


def _unused_answer(endpoint: str, parameters: dict[str, str], probe: bool) -> bytes:  # noqa: ARG001
    raise AssertionError(f"sensors.nws must never send through the governed provider client (endpoint={endpoint!r})")


def _shipped_lane() -> LaneConfig:
    return load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw")).lanes[LANE_ID]


def _config_lane() -> LaneConfig:
    """The shipped lane as G6 will flip it: `executor = "config"`, so a real (non-compare) turn may run."""
    return _shipped_lane().model_copy(update={"executor": "config"})


def _fake_fetch(answers: dict[date, Any]) -> Any:
    """A `fetch_sensors_day_snapshot` replacement keyed by day; every other window day is empty-but-answered."""

    async def fake(day: date, bbox: str) -> SensorsDaySnapshot:  # noqa: ARG001 - the real signature
        answer = answers.get(day)
        if callable(answer) and not isinstance(answer, SensorsDaySnapshot):
            return answer()
        if isinstance(answer, SensorsDaySnapshot):
            return answer
        return SensorsDaySnapshot(
            day=day, fetched_at=NOW, in_retention=True, roster_size=3, stations_answered=3, stations_unavailable=0
        )

    return fake


async def _turn(
    lane: LaneConfig, store: MemoryLaneStore, answers: dict[date, Any], monkeypatch: pytest.MonkeyPatch
) -> tuple[int, dict[str, object]]:
    spec = spec_for(lane, mode="forward")
    ports = ports_for(STRATEGY, store, upstream=ScriptedUpstream(answer=_unused_answer), clock=ManualClock(NOW))
    monkeypatch.setattr(nws_module, "fetch_sensors_day_snapshot", _fake_fetch(answers))
    return await run(spec, ports)


def _rows(store: MemoryLaneStore, day: date) -> list[dict[str, object]]:
    table = store.tables.get((STREAM, day))
    return [] if table is None else table.to_pylist()


# --- shipped config shape --------------------------------------------------------------------


def test_the_shipped_lane_is_a_daily_series_write_and_recheck_lane_with_no_gap_fill_and_no_volatile_columns() -> None:
    lane = _shipped_lane()

    assert (lane.nature, lane.kind, lane.executor, lane.strategy) == ("daily_series", "ingest", "legacy", "sensors.nws")
    assert lane.source is not None
    assert (lane.source.provider, lane.source.endpoint, lane.source.history.capability) == ("nws", "stations", "none")
    assert lane.days is not None
    assert (lane.days.publication_lag_days, lane.days.absence_recheck_days, lane.days.partial_day) == (1, 6, "write_and_recheck")
    assert lane.schedule.gap_fill_enabled is False
    assert [stream.slug for stream in lane.streams] == [STREAM]
    assert lane.streams[0].volatile_columns == ()


def test_the_shipped_strategy_key_resolves_through_s14() -> None:
    from agri_data_service.pipeline.runner.resolve import resolve_strategy

    assert resolve_strategy(_shipped_lane()) is STRATEGY


# --- plan_requests -----------------------------------------------------------------------------


def test_plan_requests_plans_one_sorted_unit_per_day_naming_the_regions_envelope_and_the_declared_endpoint() -> None:
    lane, region = _shipped_lane(), load_region("pnw")
    envelope = region.default_camera_envelope
    expected_bbox = ",".join(
        format_javascript_number(value) for value in (envelope.west, envelope.south, envelope.east, envelope.north)
    )
    days = [date(2026, 10, 20), date(2026, 10, 18)]

    requests = STRATEGY.plan_requests(days, lane, region)

    assert [(request.unit, request.endpoint, request.days, request.query()) for request in requests] == [
        (f"sensors:{date(2026, 10, 18).isoformat()}", STATIONS_ENDPOINT, (date(2026, 10, 18),), {"bbox": expected_bbox}),
        (f"sensors:{date(2026, 10, 20).isoformat()}", STATIONS_ENDPOINT, (date(2026, 10, 20),), {"bbox": expected_bbox}),
    ]
    assert requests[0].support_digest == requests[1].support_digest  # same envelope, same digest


# --- retention arithmetic, pinned directly (no run_turn, no roster call past the window) -------


@pytest.mark.asyncio
async def test_a_day_past_the_six_day_rolling_window_is_reported_without_asking_the_roster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source_module, "fetch_station_roster", _unused_roster)
    now = datetime(2026, 10, 24, 3, tzinfo=UTC)
    seven_days_ago = (now - timedelta(days=7)).date()

    snapshot = await fetch_sensors_day_snapshot(seven_days_ago, "1,2,3,4", now=now)

    assert (snapshot.in_retention, snapshot.rows, snapshot.roster_size) == (False, (), 0)


@pytest.mark.asyncio
async def test_the_oldest_day_the_window_can_still_overlap_is_in_retention(monkeypatch: pytest.MonkeyPatch) -> None:
    """The boundary case: a day whose [start, end) only PARTLY overlaps `[now - 6 days, now)` still counts."""

    class _EmptyRoster:
        stations: Sequence[object] = ()

    async def empty_roster(*_args: object, **_kwargs: object) -> _EmptyRoster:
        return _EmptyRoster()

    monkeypatch.setattr(source_module, "fetch_station_roster", empty_roster)
    now = datetime(2026, 10, 24, 3, tzinfo=UTC)
    six_days_ago = (now - timedelta(days=6)).date()

    snapshot = await fetch_sensors_day_snapshot(six_days_ago, "1,2,3,4", now=now)

    assert snapshot.in_retention is True


# --- full turns, through run_turn ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_normal_in_retention_day_writes_the_rows_the_sweep_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {
        "sensor_id": "KBOI", "station_name": "Boise Air Terminal", "network": "ASOS",
        "observed_day": WINDOW_LAST, "observed_at": datetime(2026, 10, 23, 18, tzinfo=UTC),
        "measurement_name": "temperature", "value": 15.0, "unit_code": "wmoUnit:degC", "quality_control": "V",
        "feature_id": "direct:KBOI:2026-10-23T18:00:00+00:00", "data_available_at": None,
        "station_longitude": -116.22, "station_latitude": 43.56,
    }
    # A fully-answered roster of one station (expected == present): isolates this test to row CONTENT
    # and wiring, never the S11 partial-day rule (a separate concern `nws.py`'s module docstring, H2,
    # already flags as an open item for this strategy's roster-as-unit-identity design).
    snapshot = SensorsDaySnapshot(
        day=WINDOW_LAST, fetched_at=NOW, in_retention=True, roster_size=1, stations_answered=1,
        stations_unavailable=0, rows=(row,), roster_station_ids=frozenset({"KBOI"}),
        present_station_ids=frozenset({"KBOI"}),
    )
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_config_lane(), store, {WINDOW_LAST: snapshot}, monkeypatch)

    assert exit_code == 0, payload
    written = _rows(store, WINDOW_LAST)
    assert len(written) == 1
    assert written[0]["sensor_id"] == "KBOI"
    assert written[0]["feature_id"] == "direct:KBOI:2026-10-23T18:00:00+00:00"
    assert any(line.startswith(f"write:{STREAM}:{WINDOW_LAST.isoformat()}") for line in store.writes())


@pytest.mark.asyncio
async def test_a_day_past_retention_is_reported_retention_exceeded_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = SensorsDaySnapshot(
        day=WINDOW_FIRST, fetched_at=NOW, in_retention=False, roster_size=0, stations_answered=0, stations_unavailable=0
    )
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_config_lane(), store, {WINDOW_FIRST: snapshot}, monkeypatch)

    assert exit_code == 0, payload
    unwritten = unwritten_by_day(payload)
    assert unwritten[WINDOW_FIRST.isoformat()]["outcome"] == "retention_exceeded"
    assert _rows(store, WINDOW_FIRST) == []
    assert store.writes() == []


@pytest.mark.asyncio
async def test_a_malformed_upstream_response_refuses_the_whole_day_as_a_lean_strategy_error_never_a_partial_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = date(2026, 10, 22)

    def raise_malformed() -> SensorsDaySnapshot:
        raise UpstreamPayloadError("NOAA NWS returned an invalid station page")

    store = MemoryLaneStore()

    exit_code, payload = await _turn(_config_lane(), store, {target: raise_malformed}, monkeypatch)

    assert exit_code == 0, payload
    unwritten = unwritten_by_day(payload)
    assert unwritten[target.isoformat()]["outcome"] == "strategy_error"
    assert _rows(store, target) == []
    assert store.writes() == []
