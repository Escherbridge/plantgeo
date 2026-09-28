"""Days a turn cannot write as asked, driven through `run_turn`: ladder-owed days (H1), contended lane-days (M4),
served recheck days a checkpoint must not stand in for (M3), and a transform that answers a subset of its outputs (M5).

The upstream and the bucket are the only fakes (`tests/runner/fakes.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

from agri_data_service.foundation.lane_config.models import LaneConfig
from agri_data_service.pipeline.parquet.source_checkpoint import SourceResponseCheckpoints
from agri_data_service.pipeline.runner.contract import Derivation
from tests.lane_config.builders import transform_lane
from tests.parquet.availability_documents import MemoryAvailabilityStorage
from tests.runner.fakes import (
    TODAY,
    MemoryLaneStore,
    ScriptedUpstream,
    days_between,
    grid_lane,
    grid_table,
    point_lane,
    ports_for,
    precedence_lanes,
    run,
    spec_for,
    unwritten_by_day,
)
from tests.runner.fixtures.grid_refuse import CELLS, GRID_STREAM, GridRefuseStrategy, GridWorld
from tests.runner.fixtures.point_recheck import POINT_STREAM, STATIONS, PointRecheckStrategy, PointWorld

if TYPE_CHECKING:
    from collections.abc import Mapping

    import pyarrow as pa  # type: ignore[import-untyped]

    from agri_data_service.pipeline.runner.contract import DayContext

EDGE = TODAY - timedelta(days=5)
PER_DAY = GridRefuseStrategy(span_days=1)
#: The grid lane's forward window: 14 days ending at the lag-derived edge.
WINDOW = days_between(EDGE - timedelta(days=13), EDGE)


def _published(world: GridWorld, days: list[date]) -> MemoryLaneStore:
    """A bucket serving `days` exactly as the grid strategy writes them, and an upstream that answers them."""
    store = MemoryLaneStore()
    world.publish(days)
    for day in days:
        store.publish(GRID_STREAM, day, grid_table(day, {cell: world.values[(day, cell)] for cell in CELLS}))
    return store


# --- H1: a published base under missing coarse rungs --------------------------------------------


async def test_a_ladder_owed_day_is_derived_from_its_base_without_asking_upstream() -> None:
    """The base rung is served, so the day is repaired in the turn; nothing is sent and nothing is left owed."""
    world = GridWorld()
    store = _published(world, WINDOW)
    coarse_blind = WINDOW[6]
    store.ladder_incomplete.add((GRID_STREAM, coarse_blind))
    upstream = ScriptedUpstream(world.answer)

    exit_code, report = await run(spec_for(grid_lane()), ports_for(PER_DAY, store, upstream=upstream))

    assert exit_code == 0
    assert upstream.sends == []
    assert store.journal == ["retry_owed_availability", f"repair:{GRID_STREAM}:{coarse_blind.isoformat()}"]
    assert store.status(GRID_STREAM, coarse_blind) == "data"
    assert (report["days_ladder_owed"], report["ladder_repairs"]) == (1, 1)
    assert report["unwritten"] == []


async def test_a_ladder_repair_that_fails_or_is_contended_is_reported_behind_the_edge() -> None:
    """A failed derivation is `ladder_owed` and a held lane-day `contended`: both count toward the alarm."""
    world = GridWorld()
    store = _published(world, WINDOW)
    unrepairable, held = WINDOW[3], WINDOW[8]
    store.ladder_incomplete |= {(GRID_STREAM, unrepairable), (GRID_STREAM, held)}
    store.unrepairable.add((GRID_STREAM, unrepairable))
    store.contended.add((GRID_STREAM, held))

    exit_code, report = await run(
        spec_for(grid_lane()), ports_for(PER_DAY, store, upstream=ScriptedUpstream(world.answer))
    )

    unwritten = unwritten_by_day(report)
    assert exit_code == 0
    assert (unwritten[unrepairable.isoformat()]["reason"], unwritten[held.isoformat()]["reason"]) == (
        "ladder_owed",
        "contended",
    )
    assert all(entry["behind_edge"] and entry["stream"] == GRID_STREAM for entry in unwritten.values())
    assert report["days_unwritten"] == len(unwritten)
    assert store.status(GRID_STREAM, unrepairable) == "incomplete"


# --- M4: a contended lane-day never stops the turn ----------------------------------------------


async def test_a_contended_lane_day_is_left_for_the_next_turn_and_the_rest_still_write() -> None:
    """Another run holds one missing day's lock: that day is `contended`, the other missing day is written, exit 0."""
    world = GridWorld()
    held, free = WINDOW[2], WINDOW[9]
    store = _published(world, [day for day in WINDOW if day not in {held, free}])
    world.publish([held, free])
    store.contended.add((GRID_STREAM, held))

    exit_code, report = await run(
        spec_for(grid_lane()), ports_for(PER_DAY, store, upstream=ScriptedUpstream(world.answer))
    )

    assert exit_code == 0
    assert store.writes() == [f"write:{GRID_STREAM}:{free.isoformat()}:availability=True"]
    assert unwritten_by_day(report)[held.isoformat()]["reason"] == "contended"
    assert store.status(GRID_STREAM, held) == "missing"


# --- M3: a served recheck day is re-asked, never replayed ---------------------------------------


async def test_a_recheck_lane_re_asks_its_served_days_so_a_revision_lands() -> None:
    """A checkpoint finishes an unserved day; a served `write_and_recheck` day is fetched again every turn."""
    yesterday = TODAY - timedelta(days=1)
    window = days_between(TODAY - timedelta(days=3), yesterday)
    world = PointWorld(readings={(day, station): 1.0 for day in window for station in STATIONS})
    store, checkpoints = MemoryLaneStore(), SourceResponseCheckpoints(MemoryAvailabilityStorage())
    strategy = PointRecheckStrategy()
    _, first = await run(
        spec_for(point_lane()),
        ports_for(strategy, store, upstream=ScriptedUpstream(world.answer), checkpoint_store=checkpoints),
    )
    world.readings[(yesterday, "s1")] = 9.0
    upstream = ScriptedUpstream(world.answer)

    _, second = await run(
        spec_for(point_lane()), ports_for(strategy, store, upstream=upstream, checkpoint_store=checkpoints)
    )

    assert first["checkpoints_retained"] == len(STATIONS)
    assert second["checkpoint_restores"] == 0
    assert len(upstream.fan_out_sends) == len(STATIONS)
    assert store.tables[(POINT_STREAM, yesterday)].column("value").to_pylist() == [9.0, 1.0, 1.0]


# --- M5: a transform that answers a subset of its outputs ---------------------------------------


@dataclass
class _FirstOutputOnly:
    """A transform whose second output's inputs never publish: it answers the first output alone, every day."""

    derived_days: int = 0

    def derive(self, day: date, inputs: Mapping[str, pa.Table | None], context: DayContext) -> Derivation:  # noqa: ARG002 - the Protocol's day
        self.derived_days += 1
        table = next(table for table in inputs.values() if table is not None)
        return Derivation(table={context.output_streams[0]: table})


async def test_a_transform_that_answers_a_subset_of_its_outputs_is_clean_on_the_next_turn() -> None:
    """The lane-level receipt closes the day: the unanswered output never makes it dirty again on its own."""
    _, inputs = precedence_lanes()
    streams = [{"slug": slug, "floor_basis": "write-outcomes fixture"} for slug in ("fixture-value", "fixture-extra")]
    transform = LaneConfig.model_validate(
        transform_lane(
            "fixture-two-outputs",
            inputs=list(inputs),
            executor="config",
            streams=streams,
            days={"publication_lag_days": 1, "absence_recheck_days": 5},
        )
    )
    store = MemoryLaneStore()
    window = days_between(TODAY - timedelta(days=5), TODAY - timedelta(days=1))
    for day in window:
        store.publish("fixture-era5-value", day, grid_table(day, dict.fromkeys(CELLS, 1.0)))
    strategy = _FirstOutputOnly()
    spec = spec_for(transform, mode="transform", input_lanes=inputs)
    _, first = await run(spec, ports_for(strategy, store))

    _, second = await run(spec, ports_for(strategy, store))

    assert first["days_written"] == len(window)
    assert all(store.status("fixture-extra", day) == "missing" for day in window)
    assert second["days_candidate"] == 0
    assert strategy.derived_days == len(window)
