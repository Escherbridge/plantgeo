"""Conformance flows for the five lane natures (FR-3), CA20, CA17 and compare mode, each driven through `run_turn`.

A grid `refuse` lane, a point `write_and_recheck` lane, a `release_series`, a `static_lookup`
watermark, and the real precedence transform. The upstream and the bucket are the only fakes.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

import pytest

from agri_data_service.pipeline.lanes.transforms.precedence import PRECEDENCE_SOURCE_COLUMN
from agri_data_service.pipeline.lanes.transforms.precedence import STRATEGY as PRECEDENCE
from agri_data_service.pipeline.runner.contract import SourceUnavailableError
from agri_data_service.pipeline.runner.exits import EXIT_CONFIGURATION_ERROR, EXIT_UPSTREAM_UNAVAILABLE
from agri_data_service.pipeline.runner.fetch import FetchRetryPolicy
from agri_data_service.pipeline.runner.writer import CompareModeWriteError
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
    release_lane,
    run,
    spec_for,
    static_lane,
    unwritten_by_day,
)
from tests.runner.fixtures.grid_refuse import CELLS, GRID_STREAM, GridRefuseStrategy, GridWorld
from tests.runner.fixtures.point_recheck import POINT_STREAM, PointRecheckStrategy, PointWorld
from tests.runner.fixtures.release_series import RELEASE_STREAM, ReleaseSeriesStrategy, ReleaseWorld
from tests.runner.fixtures.static_watermark import STATIC_STREAM, StaticWatermarkStrategy, ZoneWorld

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.pipeline.runner.receipts import DayReceipt

EDGE = TODAY - timedelta(days=5)
PER_DAY = GridRefuseStrategy(span_days=1)
SETTLED, PROVISIONAL, DERIVED = "fixture-era5-value", "fixture-ifs-value", "fixture-value"
#: Owed `availability/pending/` claims seeded for the CA20 flow.
OWED_CLAIMS = 3


def _seed_grid(store: MemoryLaneStore, world: GridWorld, days: Sequence[date], stream: str = GRID_STREAM) -> None:
    """Publish `days` upstream and in the bucket, exactly as the grid strategy would have written them."""
    world.publish(days)
    for day in days:
        store.publish(stream, day, grid_table(day, {cell: world.values[(day, cell)] for cell in CELLS}))


# --- grid refuse --------------------------------------------------------------------------------


async def test_a_grid_refuse_lane_writes_valued_days_governs_a_proven_absence_and_refuses_a_partial_day() -> None:
    """Settled: a full day writes, a short day is `refused_partial`, an empty day before a published one is absent."""
    store, world = MemoryLaneStore(), GridWorld()
    lane = grid_lane(days={"absence_recheck_days": 20})
    empty, full, short = EDGE - timedelta(days=18), EDGE - timedelta(days=3), EDGE - timedelta(days=2)
    window = days_between(EDGE - timedelta(days=19), EDGE)
    _seed_grid(store, world, [day for day in window if day not in {empty, full, short}])
    world.publish([full, short])
    world.values[(short, "c4")] = None

    exit_code, report = await run(spec_for(lane), ports_for(PER_DAY, store, upstream=ScriptedUpstream(world.answer)))

    assert exit_code == 0
    assert store.status(GRID_STREAM, full) == "data"
    assert store.status(GRID_STREAM, empty) == "absent"
    assert store.status(GRID_STREAM, short) == "missing"
    assert unwritten_by_day(report)[short.isoformat()]["reason"] == "refused_partial"
    assert (report["days_written"], report["days_absent"]) == (1, 1)


# --- point write_and_recheck --------------------------------------------------------------------


async def test_a_point_recheck_lane_writes_what_reported_and_records_its_units() -> None:
    """Provisional semantics: a day with two of three stations is written, and its receipt keeps the unit counts."""
    yesterday = TODAY - timedelta(days=1)
    world = PointWorld(readings={(yesterday, "s1"): 1.0, (yesterday, "s2"): 2.0})
    store = MemoryLaneStore()

    exit_code, report = await run(
        spec_for(point_lane()), ports_for(PointRecheckStrategy(), store, upstream=ScriptedUpstream(world.answer))
    )

    receipt: DayReceipt = store.receipts[(POINT_STREAM, yesterday)]
    assert exit_code == 0
    assert (receipt.present_units, receipt.expected_units) == (2, 3)
    assert store.tables[(POINT_STREAM, yesterday)].column("station_id").to_pylist() == ["s1", "s2"]
    assert report["cap_basis"] == "requests"


# --- release series -----------------------------------------------------------------------------


async def test_a_release_series_asks_only_for_release_days_and_a_404_is_unsettled_never_absent() -> None:
    """Weekly Tuesdays: two published releases write; the unpublished one waits; no other day is owed."""
    tuesdays = [date(2026, 9, 1), date(2026, 9, 8), date(2026, 9, 15)]
    world = ReleaseWorld(releases={tuesdays[0]: ["a1", "a2"], tuesdays[1]: ["a1"]})
    store = MemoryLaneStore()
    upstream = ScriptedUpstream(world.answer)

    exit_code, report = await run(
        spec_for(release_lane()), ports_for(ReleaseSeriesStrategy(), store, upstream=upstream)
    )

    assert exit_code == 0
    assert sorted(date.fromisoformat(send.parameters["release"]) for send in upstream.sends) == tuesdays
    assert [store.status(RELEASE_STREAM, day) for day in tuesdays] == ["data", "data", "missing"]
    assert set(unwritten_by_day(report)) == {tuesdays[2].isoformat()}
    assert unwritten_by_day(report)[tuesdays[2].isoformat()]["reason"] == "unsettled"
    assert not any(line.startswith("absent:") for line in store.journal)


# --- static lookup ------------------------------------------------------------------------------


async def test_a_static_lookup_writes_one_snapshot_at_its_watermark_and_nothing_while_it_is_current() -> None:
    """The watermark decides: a snapshot on the first turn, only the watermark read on the next, a new one on a move."""
    world = ZoneWorld(watermark=date(2026, 9, 12), zones={"z1": "ready", "z2": "go"})
    store = MemoryLaneStore()
    strategy = StaticWatermarkStrategy()

    first_exit, first = await run(
        spec_for(static_lane()), ports_for(strategy, store, upstream=ScriptedUpstream(world.answer))
    )
    idle_upstream = ScriptedUpstream(world.answer)
    _, idle = await run(spec_for(static_lane()), ports_for(strategy, store, upstream=idle_upstream))
    world.watermark, world.zones = date(2026, 9, 18), {"z1": "set", "z2": "go"}
    _, moved = await run(spec_for(static_lane()), ports_for(strategy, store, upstream=ScriptedUpstream(world.answer)))

    assert first_exit == 0
    assert first["edge"] == "2026-09-12"
    assert [send.probe for send in idle_upstream.sends] == [True]
    assert idle["days_unchanged"] == 1
    assert moved["days_written"] == 1
    assert store.newest_data_day(STATIC_STREAM) == date(2026, 9, 18)
    assert store.tables[(STATIC_STREAM, date(2026, 9, 18))].column("level").to_pylist() == ["set", "go"]


# --- CA17: republish the served snapshot ----------------------------------------------------------


async def test_republish_current_rewrites_the_served_snapshot_when_the_refetch_is_digest_equal() -> None:
    """CA17: an unchanged source is rewritten through the normal writer and availability path, on the served day."""
    world = ZoneWorld(watermark=date(2026, 9, 12), zones={"z1": "ready"})
    store = MemoryLaneStore()
    strategy = StaticWatermarkStrategy()
    await run(spec_for(static_lane()), ports_for(strategy, store, upstream=ScriptedUpstream(world.answer)))
    store.journal.clear()

    exit_code, report = await run(
        spec_for(static_lane(), republish_current=True),
        ports_for(strategy, store, upstream=ScriptedUpstream(world.answer)),
    )

    assert exit_code == 0
    assert report["republish"] == {"republished": "2026-09-12", "streams": [STATIC_STREAM]}
    assert store.writes() == [f"write:{STATIC_STREAM}:2026-09-12:availability=True"]
    assert store.receipts[(STATIC_STREAM, date(2026, 9, 12))].outcome == "republished"


async def test_republish_current_refuses_and_writes_nothing_when_the_source_has_changed() -> None:
    """CA17: a refetch that differs from the served snapshot is refused (exit 78); the served day is untouched."""
    world = ZoneWorld(watermark=date(2026, 9, 12), zones={"z1": "ready"})
    store = MemoryLaneStore()
    strategy = StaticWatermarkStrategy()
    await run(spec_for(static_lane()), ports_for(strategy, store, upstream=ScriptedUpstream(world.answer)))
    served = store.tables[(STATIC_STREAM, date(2026, 9, 12))]
    store.journal.clear()
    world.zones = {"z1": "go"}

    exit_code, report = await run(
        spec_for(static_lane(), republish_current=True),
        ports_for(strategy, store, upstream=ScriptedUpstream(world.answer)),
    )

    assert exit_code == EXIT_CONFIGURATION_ERROR
    assert report["republish"]["refused"] == "digest_mismatch"
    assert store.writes() == []
    assert store.tables[(STATIC_STREAM, date(2026, 9, 12))] is served


# --- CA20: owed availability claims first -------------------------------------------------------


async def test_a_config_forward_turn_retries_owed_availability_claims_before_anything_else() -> None:
    """CA20 (the legacy `_retry_owed_availability` contract): the claim retry is the turn's first write-side act."""
    store, world = MemoryLaneStore(), GridWorld()
    store.owed_claims[GRID_STREAM] = OWED_CLAIMS
    world.publish([EDGE])

    exit_code, report = await run(
        spec_for(grid_lane()), ports_for(PER_DAY, store, upstream=ScriptedUpstream(world.answer))
    )

    assert exit_code == 0
    assert store.journal[0] == "retry_owed_availability"
    assert store.journal.index("retry_owed_availability") < store.journal.index(
        f"write:{GRID_STREAM}:{EDGE.isoformat()}:availability=True"
    )
    assert report["availability_retried"] == OWED_CLAIMS


class _RetryRaisingStore(MemoryLaneStore):
    async def retry_owed_availability(self, streams: Sequence[str]) -> int:
        self.journal.append(f"retry_owed_availability:{','.join(streams)}")
        raise OSError("availability ledger unreadable")


async def test_an_owed_claim_retry_that_fails_never_blocks_the_turn_s_writes() -> None:
    """CA20's other half: a retry fault is logged, and the turn still writes what it owes."""
    store, world = _RetryRaisingStore(), GridWorld()
    world.publish([EDGE])

    exit_code, _ = await run(spec_for(grid_lane()), ports_for(PER_DAY, store, upstream=ScriptedUpstream(world.answer)))

    assert exit_code == 0
    assert store.status(GRID_STREAM, EDGE) == "data"


# --- compare mode -------------------------------------------------------------------------------


async def test_compare_builds_rows_and_diffs_them_against_the_bucket_without_writing() -> None:
    """FR-19: a legacy lane (before cut-over) is compared day by day; the bucket and checkpoints stay untouched."""
    store, world = MemoryLaneStore(), GridWorld()
    window = days_between(EDGE - timedelta(days=13), EDGE)
    _seed_grid(store, world, window)
    drifted = window[4]
    world.values[(drifted, "c2")] = 99.0
    lane = grid_lane(executor="legacy")

    exit_code, report = await run(
        spec_for(lane, compare=True), ports_for(PER_DAY, store, upstream=ScriptedUpstream(world.answer), compare=True)
    )

    assert exit_code == 0
    assert store.journal == []
    result = report["compare_result"][GRID_STREAM]
    assert (result["days_equal"], result["days_differ"]) == (len(window) - 1, 1)
    assert result["differing_days"] == [drifted.isoformat()]


async def test_a_compare_turn_handed_a_writer_refuses_before_reading_anything() -> None:
    """`--compare` cannot write: a turn built with a writer is a configuration error."""
    store = MemoryLaneStore()
    upstream = ScriptedUpstream(GridWorld().answer)
    ports = ports_for(PER_DAY, store, upstream=upstream)

    with pytest.raises(CompareModeWriteError):
        await run(spec_for(grid_lane(), compare=True), ports)

    assert upstream.sends == []


# --- S4: upstream down --------------------------------------------------------------------------


async def test_a_turn_whose_every_unit_is_upstream_unavailable_exits_75() -> None:
    """The breaker's upstream class: the probe answered, every fan-out unit failed 5xx through its ladder."""
    store, world = MemoryLaneStore(), GridWorld()
    world.publish([EDGE])

    def answer(endpoint: str, parameters: dict[str, str], probe: bool) -> bytes:
        if probe:
            return world.answer(endpoint, parameters, probe)
        raise SourceUnavailableError("upstream request failed with status 503")

    ports = ports_for(
        PER_DAY, store, upstream=ScriptedUpstream(answer), fetch_policy=FetchRetryPolicy(server_error_attempts=2)
    )

    exit_code, report = await run(spec_for(grid_lane()), ports)

    assert exit_code == EXIT_UPSTREAM_UNAVAILABLE
    assert report["outcome"] == "upstream_unavailable"
    assert unwritten_by_day(report)[EDGE.isoformat()]["reason"] == "upstream_unavailable"


# --- precedence transform -----------------------------------------------------------------------


def _seed_inputs(store: MemoryLaneStore) -> list[date]:
    """Settled values for the older three days of the window, provisional values for all five."""
    window = days_between(TODAY - timedelta(days=5), TODAY - timedelta(days=1))
    for day in window[:3]:
        store.publish(SETTLED, day, grid_table(day, dict.fromkeys(CELLS, 1.0)))
    for day in window:
        store.publish(PROVISIONAL, day, grid_table(day, dict.fromkeys(CELLS, 2.0)))
    return window


async def test_the_precedence_transform_takes_settled_over_provisional_and_names_the_source_per_row() -> None:
    """S7: settled days come from the settled lane, the tail from the provisional one, each row saying which."""
    transform, inputs = precedence_lanes()
    store = MemoryLaneStore()
    window = _seed_inputs(store)

    exit_code, _ = await run(spec_for(transform, mode="transform", input_lanes=inputs), ports_for(PRECEDENCE, store))

    assert exit_code == 0
    sources = {day: set(store.tables[(DERIVED, day)].column(PRECEDENCE_SOURCE_COLUMN).to_pylist()) for day in window}
    assert sources == {
        **{day: {"fixture-era5-settled"} for day in window[:3]},
        **{day: {"fixture-ifs-provisional"} for day in window[3:]},
    }
    assert not any(line.startswith("prune:") for line in store.journal)


async def test_a_transform_rebuilds_only_the_day_whose_input_digest_changed() -> None:
    """S11 for transforms: an unchanged window rebuilds nothing; a revised settled day rebuilds that day alone."""
    transform, inputs = precedence_lanes()
    store = MemoryLaneStore()
    window = _seed_inputs(store)
    spec = spec_for(transform, mode="transform", input_lanes=inputs)
    await run(spec, ports_for(PRECEDENCE, store))
    store.journal.clear()

    _, unchanged = await run(spec, ports_for(PRECEDENCE, store))
    revised = window[1]
    store.publish(SETTLED, revised, grid_table(revised, dict.fromkeys(CELLS, 5.0)))
    _, rebuilt = await run(spec, ports_for(PRECEDENCE, store))

    assert unchanged["days_candidate"] == 0
    assert store.writes() == [f"write:{DERIVED}:{revised.isoformat()}:availability=True"]
    assert rebuilt["days_written"] == 1
    assert store.tables[(DERIVED, revised)].column("value").to_pylist() == [5.0] * len(CELLS)


async def test_pruning_retracts_superseded_provisional_days_once_and_keeps_their_digests_in_the_receipt() -> None:
    """§4.6: with `[pruning]` on, a provisional day the settled lane covers is pruned once, never rebuilt for it."""
    transform, inputs = precedence_lanes(pruning=True)
    store = MemoryLaneStore()
    window = _seed_inputs(store)
    spec = spec_for(transform, mode="transform", input_lanes=inputs)

    await run(spec, ports_for(PRECEDENCE, store))
    first_prunes = [line for line in store.journal if line.startswith("prune:")]
    store.journal.clear()
    _, second = await run(spec, ports_for(PRECEDENCE, store))

    assert first_prunes == [f"prune:{PROVISIONAL}:{day.isoformat()}" for day in window[:3]]
    assert all(store.status(PROVISIONAL, day) == "missing" for day in window[:3])
    assert all(store.status(PROVISIONAL, day) == "data" for day in window[3:])
    assert set(store.receipts[(DERIVED, window[0])].pruned_inputs) == {PROVISIONAL}
    assert second["days_candidate"] == 0
    assert store.journal == []
