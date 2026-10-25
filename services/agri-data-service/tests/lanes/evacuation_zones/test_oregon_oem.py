"""evacuation-zones through the config-lane runner: the oregon_oem strategy driven by real turns.

No network: `source.py::fetch_evacuation_zones_snapshot` is monkeypatched on `oregon_oem`'s own
module name, the same seam `tests/direct/test_evacuation_zones_direct_watermark.py` patches on
`watermark.py`'s. Two stores are in play, as production never has to be: `strategy_store`
(`ObjectStore(RecordingBackend())`, read by `oregon_oem.py::_capture` through `store_factory`) is
what this strategy calls "published"; `MemoryLaneStore` (`tests/runner/fakes.py`) is what the runner
itself reads through `LaneReader.newest_data_day`/`census` to decide "current". Production points
both at the same bucket; a test keeps them in step by hand wherever a scenario needs both to agree.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, Final

import pytest

from agri_data_service.foundation.lane_config import load_lane_configs
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.region import load_region
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.lanes.evacuation_zones import oregon_oem
from agri_data_service.pipeline.lanes.evacuation_zones.oregon_oem import (
    STRATEGY,
    OregonOemEvacuationZonesStrategy,
)
from agri_data_service.pipeline.lanes.evacuation_zones.products import (
    EVACUATION_ZONES_DIRECT_KIND,
    EVACUATION_ZONES_STREAM,
)
from agri_data_service.pipeline.lanes.evacuation_zones.rows import evacuation_zones_table, split_into_parts
from agri_data_service.pipeline.lanes.evacuation_zones.source import EvacuationZonesSource
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.runner.contract import EdgeProbingStrategy, IngestStrategy
from agri_data_service.pipeline.runner.resolve import resolve_strategy
from tests.lane_config.builders import REAL_LANES_DIRECTORY
from tests.parquet.test_objectstore_writer import RecordingBackend
from tests.runner.fakes import NOW, TODAY, ManualClock, MemoryLaneStore, ScriptedUpstream, ports_for, run, spec_for

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import pyarrow as pa  # type: ignore[import-untyped]

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.pipeline.runner.turn import TurnPorts

LANE_ID: Final = "evacuation-zones-direct-forward"
STREAM: Final = EVACUATION_ZONES_STREAM
BBOX: Final = "-125,42,-111,49"
SQUARE: Final[dict[str, Any]] = {
    "type": "Polygon",
    "coordinates": [[[-123.0, 44.0], [-122.9, 44.0], [-122.9, 44.1], [-123.0, 44.1], [-123.0, 44.0]]],
}


def zone(global_id: str) -> dict[str, Any]:
    """One parsed Oregon OEM area, in the shape `parse_evacuation_zone_collection` produces -- a pure
    function of `global_id`, so two captures of "the same area" always digest identically."""
    return {
        "globalId": global_id,
        "evacuationAreaName": "Milepost 97",
        "fireName": "Milepost 97 Fire",
        "county": "Douglas",
        "hazardType": "Wildfire",
        "editorName": "OEM Sync",
        "evacuationLevel": 3,
        "evacuationLevelLabel": "Go Now",
        "severity": "critical",
        "structuresWithin": 12.0,
        "addressesWithin": 30.0,
        "populationWithin": 74.0,
        "createdAt": datetime(2025, 4, 14, tzinfo=UTC),
        "createdDate": "2025-04-14T00:00:00Z",
        "lastEditedDate": "2026-09-20T17:29:00Z",
        "geometry": SQUARE,
    }


def captured(*global_ids: str, fetched_at: datetime = NOW) -> EvacuationZonesSource:
    return EvacuationZonesSource(bbox=BBOX, fetched_at=fetched_at, zones=tuple(zone(gid) for gid in global_ids))


def seed_published(store: ObjectStore, *, day: date, source: EvacuationZonesSource) -> pa.Table:
    """Write a complete part-plus-marker version -- the only state `read_published_snapshot` counts.

    Mirrors `tests/direct/test_evacuation_zones_direct_watermark.py::publish`, restated here because
    that helper lives in a sibling test package this file does not import from.
    """
    table = evacuation_zones_table(source, snapshot_day=day)
    parts = split_into_parts(table)
    for index, part in enumerate(parts):
        store.write_partition(
            part, layer=STREAM, kind=EVACUATION_ZONES_DIRECT_KIND, zoom=LANE_BASE_ZOOM_TIER, day=day, part_index=index
        )
    store.write_completion_marker(
        PartitionCompletion(
            part_count=len(parts), row_count=table.num_rows, completed_at=source.fetched_at, run_id="seed"
        ),
        layer=STREAM,
        kind=EVACUATION_ZONES_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=day,
    )
    return table


def _lane() -> LaneConfig:
    """The shipped TOML, with `executor = "config"` -- the shape p5-cutover gives this lane, never
    production truth before that owner go (plan Phase 5)."""
    shipped = load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw")).lanes[LANE_ID]
    return shipped.model_copy(update={"executor": "config"})


def _never_called(endpoint: str, parameters: dict[str, str], probe: bool) -> bytes:  # noqa: ARG001 - the ScriptedUpstream shape
    raise AssertionError("oregon_oem must never call client.get() for its real fetch (see its module docstring)")


def _ports(strategy: OregonOemEvacuationZonesStrategy, store: MemoryLaneStore) -> TurnPorts:
    return ports_for(strategy, store, upstream=ScriptedUpstream(_never_called), clock=ManualClock(NOW))


# --- the shipped TOML resolves to this strategy --------------------------------------------------


def test_the_shipped_lane_resolves_to_this_strategy_and_requires_probe_edge() -> None:
    lane = load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw")).lanes[LANE_ID]

    strategy = resolve_strategy(lane, requires_probe_edge=True)

    assert strategy is STRATEGY
    assert isinstance(strategy, IngestStrategy)
    assert isinstance(strategy, EdgeProbingStrategy)


# --- the quiet-day flow ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_first_ever_quiet_day_is_a_governed_absence_naming_the_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DO NOT DELETE. A quiet statewide capture is a real, valued answer, never a failed read."""
    monkeypatch.setenv("INGEST_BBOX", BBOX)
    monkeypatch.setattr(oregon_oem, "fetch_evacuation_zones_snapshot", _fetch_returning(captured(fetched_at=NOW)))
    strategy = OregonOemEvacuationZonesStrategy(store_factory=lambda: ObjectStore(RecordingBackend()))
    store = MemoryLaneStore()

    exit_code, report = await run(spec_for(_lane(), mode="forward"), _ports(strategy, store))

    assert exit_code == 0
    assert report["days_absent"] == 1
    assert report["days_written"] == 0
    assert store.status(STREAM, TODAY) == "absent"
    absence_line = next(line for line in store.journal if line.startswith("absent:"))
    assert "Oregon OEM published no evacuation areas" in absence_line


@pytest.mark.asyncio
async def test_a_first_ever_capture_with_zones_writes_a_new_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGEST_BBOX", BBOX)
    monkeypatch.setattr(oregon_oem, "fetch_evacuation_zones_snapshot", _fetch_returning(captured("{A}", "{B}")))
    strategy = OregonOemEvacuationZonesStrategy(store_factory=lambda: ObjectStore(RecordingBackend()))
    store = MemoryLaneStore()

    exit_code, report = await run(spec_for(_lane(), mode="forward"), _ports(strategy, store))

    assert exit_code == 0
    assert report["days_written"] == 1
    assert store.status(STREAM, TODAY) == "data"
    assert sorted(store.tables[(STREAM, TODAY)].column("global_id").to_pylist()) == ["{A}", "{B}"]


# --- the watermark-unchanged short-circuit ----------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unchanged_capture_short_circuits_before_a_second_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """DO NOT DELETE. Proves `probe_edge`'s cached capture is never re-asked a second time this turn.

    The published version was captured 10 days ago; today's identical zone set digests equal (content
    digest excludes the retrieval instant), so the watermark is STICKY at the published day and the
    runner's own `served >= watermark` check (`turn.py::_Turn._static_forward`) never reaches
    `plan_requests`/`fetch` at all -- the upstream fake's call count is the proof.
    """
    monkeypatch.setenv("INGEST_BBOX", BBOX)
    published_day = date(2026, 9, 10)
    seeded = captured("{A}", fetched_at=datetime(2026, 9, 10, 8, 0, tzinfo=UTC))
    strategy_store = ObjectStore(RecordingBackend())
    seed_published(strategy_store, day=published_day, source=seeded)
    store = MemoryLaneStore()
    store.publish(STREAM, published_day, evacuation_zones_table(seeded, snapshot_day=published_day))

    calls = 0

    async def fake_fetch(
        bbox: str, *, retry_attempts: int, retry_base_seconds: float, retry_max_seconds: float
    ) -> EvacuationZonesSource:
        nonlocal calls
        calls += 1
        return captured("{A}", fetched_at=NOW)

    monkeypatch.setattr(oregon_oem, "fetch_evacuation_zones_snapshot", fake_fetch)
    strategy = OregonOemEvacuationZonesStrategy(store_factory=lambda: strategy_store)

    exit_code, report = await run(spec_for(_lane(), mode="forward"), _ports(strategy, store))

    assert exit_code == 0
    assert calls == 1
    assert report["days_unchanged"] == 1
    assert report["days_written"] == 0
    assert store.writes() == []


# --- a documented residual: the day-granular "current" check masks a same-day change ----------------


@pytest.mark.asyncio
async def test_a_same_utc_day_stand_down_is_masked_until_the_day_rolls_over(monkeypatch: pytest.MonkeyPatch) -> None:
    """DO NOT DELETE -- documents a residual, not a strategy bug.

    `turn.py::_Turn._static_forward`'s fast path compares DAYS (`served >= watermark`), and this
    lane's watermark is a DERIVED day stamp (today's UTC capture date) rather than an upstream clock,
    so two DIFFERENT statewide states captured hours apart on the SAME UTC day both decide
    `watermark = today`. When an earlier turn today already wrote zones, `served == today == watermark`
    reads as "current" and `settle()`/`rows()` are never called again -- the quiet capture this turn
    actually saw is silently never checked. Oregon's own stand-down IS seen the next UTC day (a new
    `watermark` date no longer equals `served`), so no state is ever lost permanently and no absence
    is ever written over data (the ladder-absence refusal this package's `AGENTS.md` cites,
    `writer.py::_decide_unverified_rewrite`'s `"data_never_retracted_by_absence"`, is consequently
    unreachable for this lane via `_static_forward` -- there is no code path left to protect once the
    fast path intercepts the collision first). This is a generic-runner date-granularity limit for a
    CONTENT-watermarked `static_lookup`, not something `oregon_oem.py` can route around: `ProviderEdge
    .valued_days` is `frozenset[date]`, with no sub-day resolution to mark "changed again today." Named
    for the per-lane adversarial review and the owner before any `executor = "config"` cutover (CA7's
    sibling slices face the same question for any other same-day-volatile static_lookup).
    """
    monkeypatch.setenv("INGEST_BBOX", BBOX)
    morning = captured("{A}", fetched_at=datetime(2026, 9, 20, 8, 0, tzinfo=UTC))
    strategy_store = ObjectStore(RecordingBackend())
    seed_published(strategy_store, day=TODAY, source=morning)
    store = MemoryLaneStore()
    store.publish(STREAM, TODAY, evacuation_zones_table(morning, snapshot_day=TODAY))

    # The afternoon capture: the fire is contained and Oregon's feed is quiet, hours later, same UTC day.
    monkeypatch.setattr(oregon_oem, "fetch_evacuation_zones_snapshot", _fetch_returning(captured(fetched_at=NOW)))
    strategy = OregonOemEvacuationZonesStrategy(store_factory=lambda: strategy_store)

    exit_code, report = await run(spec_for(_lane(), mode="forward"), _ports(strategy, store))

    assert exit_code == 0
    # The fast path reads this as "nothing to do": the quiet afternoon capture is never settled.
    assert report["days_unchanged"] == 1
    assert report["days_absent"] == 0
    # The morning's zones are still served, unreplaced -- the residual named above, not a crash.
    assert store.status(STREAM, TODAY) == "data"
    assert store.tables[(STREAM, TODAY)].column("global_id").to_pylist() == ["{A}"]


def _fetch_returning(source: EvacuationZonesSource) -> Callable[..., Awaitable[EvacuationZonesSource]]:
    """An async `fetch_evacuation_zones_snapshot` stand-in that always answers with `source`."""

    async def fake(bbox: str, *, retry_attempts: int, retry_base_seconds: float, retry_max_seconds: float) -> EvacuationZonesSource:
        return source

    return fake
