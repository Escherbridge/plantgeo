"""`watersheds-direct-forward` through real runner turns: the shipped TOML, the real S14 strategy.

Only the NHDPlus_HR upstream is faked, and it is faked at `pipeline.lanes.watersheds.source`'s own
`fetch_watersheds` seam (never a shim: `pipeline/lanes/watersheds/AGENTS.md` "Monkeypatch the
module here, never its shim") -- this strategy never sends through the turn's governed
`ProviderClient` (Wave O's "existing source module's upstream_client" alternative; see
`nhdplus.py`'s module docstring), so `ScriptedUpstream`'s `get()` is wired to raise if it is ever
called, proving that bypass rather than merely asserting it in prose.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.foundation.lane_config import load_lane_configs
from agri_data_service.foundation.region import load_region
from agri_data_service.ingest.policy import PACIFIC_NORTHWEST_COVERAGE_BBOX
from agri_data_service.pipeline.lanes.watersheds import source as source_module
from agri_data_service.pipeline.lanes.watersheds.nhdplus import DIRECT_FEATURE_ID_PREFIX, ENDPOINT, STRATEGY
from agri_data_service.pipeline.runner.contract import ProbeWindow
from agri_data_service.pipeline.runner.resolve import resolve_strategy
from tests.lane_config.builders import REAL_LANES_DIRECTORY
from tests.runner.fakes import NOW, ManualClock, MemoryLaneStore, ScriptedUpstream, ports_for, run, spec_for

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.foundation.lane_config.models import LaneConfig

LANE_ID = "watersheds-direct-forward"
STREAM = "watersheds"
BBOX = PACIFIC_NORTHWEST_COVERAGE_BBOX
VALID_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-119.0, 45.0], [-119.0, 46.0], [-120.0, 46.0], [-120.0, 45.0]]],
}
#: The Sandy River `loaddate` from `ingest/watersheds.py`'s own module docstring, 2013-01-18.
OLDER_LOADDATE_MS = 1358492970000
NEWER_LOADDATE_MS = 1400000000000
#: `probe_edge`'s window is informational only here (`clip=False`, so a watermark outside it is
#: still trusted); any 14-day window exercises the real call shape.
PROBE_WINDOW = ProbeWindow(first=date(2026, 9, 6), last=date(2026, 9, 20))


def _feature(
    huc12: str, *, loaddate: object, name: str | None = "Test Creek", **overrides: object
) -> dict[str, object]:
    properties: dict[str, object] = {
        "huc12": huc12,
        "name": name,
        "areasqkm": 12.5,
        "tohuc": "170900011200",
        "states": "OR",
        "hutype": "S",
        "loaddate": loaddate,
    }
    properties.update(overrides)
    return {"properties": properties, "geometry": VALID_SQUARE}


def _stub_fetch(features: Sequence[dict[str, object]]) -> tuple[Any, list[str]]:
    """A `fetch_watersheds` replacement that always answers `features`, recording every bbox it was sent."""
    calls: list[str] = []

    async def fetch_watersheds(client: Any, bbox: str) -> Sequence[dict[str, object]]:  # noqa: ARG001
        calls.append(bbox)
        return features

    return fetch_watersheds, calls


def _unused_answer(endpoint: str, parameters: dict[str, str], probe: bool) -> bytes:  # noqa: ARG001
    raise AssertionError(
        f"watersheds.nhdplus must never send through the governed provider client (endpoint={endpoint!r})"
    )


def _shipped_lane() -> LaneConfig:
    return load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw")).lanes[LANE_ID]


def _config_lane() -> LaneConfig:
    """The shipped lane as G6 will flip it: `executor = "config"`, so a real (non-compare) turn may run."""
    return _shipped_lane().model_copy(update={"executor": "config"})


async def _turn(
    lane: LaneConfig, store: MemoryLaneStore, *, now: datetime = NOW, compare: bool = False
) -> tuple[int, dict[str, object]]:
    spec = spec_for(lane, mode="forward", compare=compare)
    ports = ports_for(
        STRATEGY, store, upstream=ScriptedUpstream(answer=_unused_answer), clock=ManualClock(now), compare=compare
    )
    return await run(spec, ports)


def _rows(store: MemoryLaneStore, day: date) -> list[dict[str, object]]:
    return store.tables[(STREAM, day)].to_pylist()


# --- shipped config shape --------------------------------------------------------------------


def test_the_shipped_lane_is_a_static_lookup_with_no_calendar_floor_and_a_tight_logical_budget() -> None:
    lane = _shipped_lane()

    assert (lane.nature, lane.kind, lane.executor) == ("static_lookup", "ingest", "legacy")
    assert lane.days is None  # partial_day defaults to "refuse" from this (turn.py's own property)
    assert lane.source is not None
    assert (lane.source.provider, lane.source.endpoint, lane.source.lifecycle) == (
        "usgs-nhdplus",
        "wbd-hu12-query",
        "discontinued",
    )
    assert (lane.budget.forward_max_weighted_calls, lane.budget.gap_fill_max_weighted_calls) == (1, 1)
    assert lane.schedule.gap_fill_enabled is False
    assert [stream.slug for stream in lane.streams] == [STREAM]


def test_the_shipped_strategy_key_resolves_to_an_edge_probing_ingest_strategy() -> None:
    strategy = resolve_strategy(_shipped_lane())

    assert strategy is STRATEGY


# --- plan_requests -----------------------------------------------------------------------------


def test_plan_requests_plans_exactly_one_snapshot_unit_naming_the_resolved_bbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INGEST_BBOX", BBOX)
    lane, region = _shipped_lane(), load_region("pnw")
    day = date(2026, 8, 7)

    requests = STRATEGY.plan_requests([day], lane, region)

    assert len(requests) == 1
    request = requests[0]
    assert (request.endpoint, request.days, request.query()) == (ENDPOINT, (day,), {"bbox": BBOX})


def test_plan_requests_plans_nothing_when_ingest_bbox_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INGEST_BBOX", raising=False)

    requests = STRATEGY.plan_requests([date(2026, 8, 7)], _shipped_lane(), load_region("pnw"))

    assert requests == []


# --- probe_edge ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_edge_reports_the_max_loaddate_across_the_fetched_extent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGEST_BBOX", BBOX)
    fetch_watersheds, _calls = _stub_fetch(
        [_feature("170900011201", loaddate=OLDER_LOADDATE_MS), _feature("170900011202", loaddate=NEWER_LOADDATE_MS)]
    )
    monkeypatch.setattr(source_module, "fetch_watersheds", fetch_watersheds)

    edge = await STRATEGY.probe_edge(client=object(), window=PROBE_WINDOW)

    expected_day = datetime.fromtimestamp(NEWER_LOADDATE_MS / 1000, tz=UTC).date()
    assert (edge.status, edge.edge, edge.valued_days) == ("ok", expected_day, frozenset({expected_day}))


@pytest.mark.asyncio
async def test_probe_edge_reports_blind_valued_days_for_a_genuinely_empty_extent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INGEST_BBOX", BBOX)
    fetch_watersheds, _calls = _stub_fetch([])
    monkeypatch.setattr(source_module, "fetch_watersheds", fetch_watersheds)

    edge = await STRATEGY.probe_edge(client=object(), window=PROBE_WINDOW)

    assert (edge.status, edge.edge, edge.valued_days) == ("ok", None, frozenset())


@pytest.mark.asyncio
async def test_probe_edge_reports_invalid_when_every_basin_lacks_a_parseable_vintage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-empty population with no single parseable `loaddate` is a data fault, not an empty source."""
    monkeypatch.setenv("INGEST_BBOX", BBOX)
    fetch_watersheds, _calls = _stub_fetch([_feature("170900011201", loaddate=None)])
    monkeypatch.setattr(source_module, "fetch_watersheds", fetch_watersheds)

    edge = await STRATEGY.probe_edge(client=object(), window=PROBE_WINDOW)

    assert edge.status == "invalid"


# --- full turns, through run_turn ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_first_forward_turn_writes_the_whole_extent_at_the_watermark_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INGEST_BBOX", BBOX)
    fetch_watersheds, calls = _stub_fetch(
        [_feature("170900011201", loaddate=OLDER_LOADDATE_MS), _feature("170900011202", loaddate=NEWER_LOADDATE_MS)]
    )
    monkeypatch.setattr(source_module, "fetch_watersheds", fetch_watersheds)
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_config_lane(), store)

    assert exit_code == 0, payload
    watermark_day = datetime.fromtimestamp(NEWER_LOADDATE_MS / 1000, tz=UTC).date()
    assert payload["edge"] == watermark_day.isoformat()
    rows = {row["huc12"]: row for row in _rows(store, watermark_day)}
    assert sorted(rows) == ["170900011201", "170900011202"]
    first = rows["170900011201"]
    assert (first["feature_id"], first["source"], first["release_day"]) == (
        f"{DIRECT_FEATURE_ID_PREFIX}:170900011201",
        "USGS NHDPlus HR WBDHU12",
        watermark_day,
    )
    assert isinstance(first["geom"], bytes)
    assert len(first["geom"]) > 0
    # Two full walks this turn: the probe reads the watermark, then the fan-out fetches the rows.
    assert calls == [BBOX, BBOX]


@pytest.mark.asyncio
async def test_a_second_turn_at_the_same_watermark_writes_nothing_and_walks_the_source_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Current at the probe alone: only the first, stale turn pays for the second (fan-out) walk."""
    monkeypatch.setenv("INGEST_BBOX", BBOX)
    features = [_feature("170900011201", loaddate=OLDER_LOADDATE_MS)]
    fetch_watersheds, calls = _stub_fetch(features)
    monkeypatch.setattr(source_module, "fetch_watersheds", fetch_watersheds)
    store = MemoryLaneStore()
    lane = _config_lane()

    first_exit, first_payload = await _turn(lane, store)
    second_exit, second_payload = await _turn(lane, store)

    assert (first_exit, second_exit) == (0, 0), (first_payload, second_payload)
    assert second_payload["days_unchanged"] == 1
    assert second_payload["days_written"] == 0
    # First turn: probe + fetch (2 calls). Second turn: probe only, because it is already current.
    assert calls == [BBOX, BBOX, BBOX]


@pytest.mark.asyncio
async def test_an_identity_defect_is_counted_and_skipped_never_refusing_the_whole_population(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INGEST_BBOX", BBOX)
    features = [
        _feature("170900011201", loaddate=NEWER_LOADDATE_MS),
        {"properties": {"name": "no huc12 here"}, "geometry": VALID_SQUARE},
    ]
    fetch_watersheds, _calls = _stub_fetch(features)
    monkeypatch.setattr(source_module, "fetch_watersheds", fetch_watersheds)
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_config_lane(), store)

    assert exit_code == 0, payload
    watermark_day = datetime.fromtimestamp(NEWER_LOADDATE_MS / 1000, tz=UTC).date()
    assert [row["huc12"] for row in _rows(store, watermark_day)] == ["170900011201"]
    assert payload["rows_dropped_by_reason"] == {"rejected_feature": 1}


@pytest.mark.asyncio
async def test_an_unconfigured_bbox_completes_the_turn_without_writing_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graceful, not a crash: the legacy writer's own `BBOX_UNCONFIGURED` no-op, mirrored here."""
    monkeypatch.delenv("INGEST_BBOX", raising=False)
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_config_lane(), store)

    assert exit_code == 0, payload
    assert payload["days_written"] == 0
    assert store.tables == {}


@pytest.mark.asyncio
async def test_compare_mode_runs_the_dark_shipped_lane_despite_its_legacy_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G6's own read-only proof: `--compare` runs regardless of `executor`/`enabled` (turn.py's own carve-out)."""
    monkeypatch.setenv("INGEST_BBOX", BBOX)
    fetch_watersheds, _calls = _stub_fetch([_feature("170900011201", loaddate=NEWER_LOADDATE_MS)])
    monkeypatch.setattr(source_module, "fetch_watersheds", fetch_watersheds)
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_shipped_lane(), store, compare=True)

    assert exit_code == 0, payload
    assert store.tables == {}  # a compare turn is read-only
    compare_result = payload["compare_result"][STREAM]
    assert compare_result["days_unpublished"] == 1  # nothing published yet to compare against
