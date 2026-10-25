"""`sensors-direct-forward` through real runner turns: the shipped TOML, the real S14 strategy, the real sweep.

Full turns go through `run_turn` with `NwsSensorsStrategy(now=clock.now)` and `source.py`'s own
`upstream_client` bound to a scripted `httpx.MockTransport` -- the real roster walk, `limit=1` poll,
winner reduction, settle and S11 rewrite rules, faking only the NWS edge. The rolling-retention
floor is also pinned directly against `source.fetch_sensors_day_snapshot`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from agri_data_service.foundation.lane_config import load_lane_configs
from agri_data_service.foundation.region import load_region
from agri_data_service.ingest.http import upstream_client as real_upstream_client
from agri_data_service.ingest.policy import format_javascript_number
from agri_data_service.ingest.sensors import StationRoster
from agri_data_service.pipeline.lanes.sensors import source as source_module
from agri_data_service.pipeline.lanes.sensors.nws import STATIONS_ENDPOINT, STRATEGY, NwsSensorsStrategy
from agri_data_service.pipeline.lanes.sensors.source import fetch_sensors_day_snapshot
from agri_data_service.pipeline.runner.resolve import resolve_strategy
from tests.lane_config.builders import REAL_LANES_DIRECTORY
from tests.runner.fakes import (
    ManualClock,
    MemoryLaneStore,
    ScriptedUpstream,
    ports_for,
    run,
    spec_for,
    unwritten_by_day,
)

if TYPE_CHECKING:
    from agri_data_service.foundation.lane_config.models import LaneConfig

LANE_ID = "sensors-direct-forward"
STREAM = "sensors"
#: edge = today - 1 = 2026-10-23; window = [2026-10-19, 2026-10-23] (absence_recheck_days = 5).
NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
WINDOW = tuple(date(2026, 10, 19) + timedelta(days=offset) for offset in range(5))
TARGET = date(2026, 10, 23)
#: Two in-box ASOS stations (pnw's camera envelope), as (longitude, latitude).
STATIONS: dict[str, tuple[float, float]] = {"KBOI": (-116.22, 43.56), "KMYL": (-116.10, 44.89)}


@dataclass
class ScriptedNws:
    """The NWS edge: a roster page, per-(station, day) reports, stations whose poll fails, and every poll URL."""

    reports: dict[tuple[str, date], list[tuple[str, float]]] = field(default_factory=dict)
    failing: set[str] = field(default_factory=set)
    roster: object | None = None
    polled: list[httpx.URL] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stations":
            return httpx.Response(200, json=self.roster if self.roster is not None else _roster_page())
        station = request.url.path.split("/")[2]
        self.polled.append(request.url)
        if station in self.failing:
            return httpx.Response(503)
        day = date.fromisoformat(request.url.params["start"][:10])
        features = [_observation(station, stamp, value) for stamp, value in self.reports.get((station, day), [])]
        return httpx.Response(200, json={"type": "FeatureCollection", "features": features})


def _roster_page() -> dict[str, object]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
                "properties": {"stationIdentifier": station, "name": station, "provider": "ASOS"},
            }
            for station, (longitude, latitude) in STATIONS.items()
        ],
    }


def _observation(station: str, timestamp: str, temperature: float) -> dict[str, object]:
    return {
        "properties": {
            "stationId": station,
            "timestamp": timestamp,
            "temperature": {"value": temperature, "unitCode": "wmoUnit:degC", "qualityControl": "V"},
        }
    }


def _unused_roster(*_args: object, **_kwargs: object) -> Any:
    raise AssertionError("fetch_sensors_day_snapshot must never ask NWS's roster for a day past its own retention")


def _unused_answer(endpoint: str, parameters: dict[str, str], probe: bool) -> bytes:  # noqa: ARG001
    raise AssertionError(f"sensors.nws must never send through the governed provider client (endpoint={endpoint!r})")


def _shipped_lane() -> LaneConfig:
    return load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw")).lanes[LANE_ID]


def _config_lane() -> LaneConfig:
    """The shipped lane as G6 will flip it: `executor = "config"`, so a real (non-compare) turn may run."""
    return _shipped_lane().model_copy(update={"executor": "config"})


async def _turn(store: MemoryLaneStore, nws: ScriptedNws, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """One real forward turn against the scripted NWS edge, at the manual clock."""
    monkeypatch.setenv("SENSOR_STATION_STATES", "ID")
    monkeypatch.setattr(
        source_module,
        "upstream_client",
        lambda bounds: real_upstream_client(bounds, transport=httpx.MockTransport(nws.handle)),
    )
    clock = ManualClock(NOW)
    strategy = NwsSensorsStrategy(now=clock.now)
    ports = ports_for(strategy, store, upstream=ScriptedUpstream(answer=_unused_answer), clock=clock)
    exit_code, payload = await run(spec_for(_config_lane(), mode="forward"), ports)
    assert exit_code == 0, payload
    return payload


def _rows(store: MemoryLaneStore, day: date) -> list[dict[str, object]]:
    table = store.tables.get((STREAM, day))
    return [] if table is None else table.to_pylist()


# --- wiring --------------------------------------------------------------------------------------


def test_the_shipped_strategy_key_resolves_through_s14() -> None:
    assert resolve_strategy(_shipped_lane()) is STRATEGY


def test_plan_requests_plans_one_sorted_unit_per_day_naming_the_regions_envelope_and_the_declared_endpoint() -> None:
    lane, region = _shipped_lane(), load_region("pnw")
    envelope = region.default_camera_envelope
    expected_bbox = ",".join(
        format_javascript_number(value) for value in (envelope.west, envelope.south, envelope.east, envelope.north)
    )
    first, second = date(2026, 10, 18), date(2026, 10, 20)

    requests = STRATEGY.plan_requests([second, first], lane, region)

    assert [(request.unit, request.endpoint, request.days, request.query()) for request in requests] == [
        (f"sensors:{first.isoformat()}", STATIONS_ENDPOINT, (first,), {"bbox": expected_bbox}),
        (f"sensors:{second.isoformat()}", STATIONS_ENDPOINT, (second,), {"bbox": expected_bbox}),
    ]
    assert requests[0].support_digest == requests[1].support_digest  # same envelope, same digest


# --- retention arithmetic, pinned directly -------------------------------------------------------


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

    async def empty_roster(*_args: object, **_kwargs: object) -> StationRoster:
        return StationRoster(stations=[])

    monkeypatch.setattr(source_module, "fetch_station_roster", empty_roster)
    now = datetime(2026, 10, 24, 3, tzinfo=UTC)
    six_days_ago = (now - timedelta(days=6)).date()

    snapshot = await fetch_sensors_day_snapshot(six_days_ago, "1,2,3,4", now=now)

    assert snapshot.in_retention is True


# --- full turns, through run_turn and the real sweep ---------------------------------------------


@pytest.mark.asyncio
async def test_a_turn_polls_each_station_day_with_limit_one_and_writes_the_newest_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Older first, two features: an upstream that ignored `limit` still cannot make the older report win.
    nws = ScriptedNws(
        reports={("KBOI", TARGET): [("2026-10-23T21:53:00+00:00", 14.0), ("2026-10-23T22:53:00+00:00", 15.0)]}
    )
    store = MemoryLaneStore()

    await _turn(store, nws, monkeypatch)

    assert len(nws.polled) == len(STATIONS) * len(WINDOW)
    assert {url.params["limit"] for url in nws.polled} == {"1"}
    written = _rows(store, TARGET)
    assert [(row["sensor_id"], row["measurement_name"], row["value"]) for row in written] == [
        ("KBOI", "temperature", 15.0)
    ]
    assert written[0]["feature_id"] == "direct:KBOI:2026-10-23T22:53:00+00:00"
    writes = store.writes()
    assert writes
    assert all(line.startswith(f"write:{STREAM}:{TARGET.isoformat()}") for line in writes)


@pytest.mark.asyncio
async def test_a_station_whose_poll_fails_never_erases_what_it_served_last_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nws = ScriptedNws(
        reports={
            ("KBOI", TARGET): [("2026-10-23T22:53:00+00:00", 15.0)],
            ("KMYL", TARGET): [("2026-10-23T22:56:00+00:00", 9.0)],
        }
    )
    store = MemoryLaneStore()
    await _turn(store, nws, monkeypatch)
    first = _rows(store, TARGET)
    assert {row["sensor_id"] for row in first} == {"KBOI", "KMYL"}

    nws.failing.add("KBOI")
    nws.reports[("KMYL", TARGET)].append(("2026-10-23T23:56:00+00:00", 8.0))
    second = await _turn(store, nws, monkeypatch)

    assert second["rewrite_reasons"] == {"fewer_units": 1}
    assert _rows(store, TARGET) == first


@pytest.mark.asyncio
async def test_a_later_report_from_the_same_answering_stations_rewrites_the_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # KMYL answers but has not reported on TARGET: present is the stations that ANSWERED, so the day
    # is complete and a newer KBOI report reaches the digest rule instead of freezing at 22:53.
    nws = ScriptedNws(reports={("KBOI", TARGET): [("2026-10-23T22:53:00+00:00", 15.0)]})
    store = MemoryLaneStore()
    await _turn(store, nws, monkeypatch)
    assert _rows(store, TARGET)[0]["feature_id"] == "direct:KBOI:2026-10-23T22:53:00+00:00"

    nws.reports[("KBOI", TARGET)].append(("2026-10-23T23:53:00+00:00", 13.0))
    second = await _turn(store, nws, monkeypatch)

    assert second["rewrite_reasons"] == {"digest_changed": 1}
    assert [(row["feature_id"], row["value"]) for row in _rows(store, TARGET)] == [
        ("direct:KBOI:2026-10-23T23:53:00+00:00", 13.0)
    ]


@pytest.mark.asyncio
async def test_a_malformed_roster_page_refuses_every_day_as_a_strategy_error_and_polls_no_station(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nws = ScriptedNws(roster={"type": "FeatureCollection"})  # no `features`: not a readable station page
    store = MemoryLaneStore()

    payload = await _turn(store, nws, monkeypatch)

    unwritten = unwritten_by_day(payload)
    assert {unwritten[day.isoformat()]["reason"] for day in WINDOW} == {"strategy_error"}
    assert nws.polled == []
    assert store.writes() == []
