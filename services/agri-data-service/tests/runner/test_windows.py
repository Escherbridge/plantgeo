"""Which days a turn asks about (D3, S19): the probe gate, the walk behind it, provisional tails, gap-fill, dirty days.

Turns run through `run_turn` against the memory bucket and a scripted upstream; the pure window
functions are table-driven where the branching is the subject.
"""

from __future__ import annotations

from datetime import date, timedelta

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.pipeline.runner.contract import SourceThrottledError, SourceUnavailableError
from agri_data_service.pipeline.runner.cooldown import ProviderCooldowns
from agri_data_service.pipeline.runner.windows import (
    GapFillDisabledError,
    plan_gap_fill,
    transform_dirty_days,
)
from tests.parquet.availability_documents import MemoryAvailabilityStorage
from tests.runner.fakes import (
    NOW,
    TODAY,
    ManualClock,
    MemoryLaneStore,
    ScriptedUpstream,
    days_between,
    grid_lane,
    point_lane,
    ports_for,
    run,
    spec_for,
    unwritten_by_day,
)
from tests.runner.fixtures.grid_refuse import GRID_STREAM, GridRefuseStrategy, GridWorld
from tests.runner.fixtures.point_recheck import POINT_STREAM, PointRecheckStrategy, PointWorld

#: The grid lane's forward window with lag 5 and a 14-day recheck: 2026-09-02 .. 2026-09-15.
EDGE = TODAY - timedelta(days=5)
WINDOW_FIRST = EDGE - timedelta(days=13)
PER_DAY = GridRefuseStrategy(span_days=1)
#: Two probe cells over at most 14 days: two weighted calls (spec §6.3).
PROBE_WEIGHT = 2.0


def _published_through(store: MemoryLaneStore, world: GridWorld, last: date, *, first: date = WINDOW_FIRST) -> None:
    """Seed the bucket with every window day through `last`, exactly as the world answers them."""
    world.publish(days_between(first, last))
    for day in days_between(first, last):
        store.publish(GRID_STREAM, day, _grid_rows(world, day))


def _grid_rows(world: GridWorld, day: date) -> pa.Table:
    cells = sorted(
        cell for (valued_day, cell), value in world.values.items() if valued_day == day and value is not None
    )
    return pa.table(
        {
            "cell_id": pa.array(cells, pa.string()),
            "observed_day": pa.array([day] * len(cells), pa.date32()),
            "value": pa.array([world.values[(day, cell)] for cell in cells], pa.float64()),
        }
    )


def _sent_days(upstream: ScriptedUpstream) -> set[date]:
    return {date.fromisoformat(send.parameters["start_date"]) for send in upstream.fan_out_sends}


async def test_a_forward_fire_fans_out_only_when_the_probe_shows_a_new_settled_day() -> None:
    """S19: the probe shows 09-14 valued and 09-15 not, so only 09-14 fans out; 09-15 waits, ahead of the edge."""
    store, world = MemoryLaneStore(), GridWorld()
    _published_through(store, world, EDGE - timedelta(days=2))
    world.publish([EDGE - timedelta(days=1)])
    upstream = ScriptedUpstream(world.answer)

    exit_code, report = await run(spec_for(grid_lane()), ports_for(PER_DAY, store, upstream=upstream))

    assert exit_code == 0
    assert _sent_days(upstream) == {EDGE - timedelta(days=1)}
    assert store.status(GRID_STREAM, EDGE - timedelta(days=1)) == "data"
    assert store.status(GRID_STREAM, EDGE) == "missing"
    held = unwritten_by_day(report)[EDGE.isoformat()]
    assert (held["reason"], held["detail"], held["behind_edge"]) == ("unsettled", "newer_than_probed_edge", False)
    assert report["edge"] == (EDGE - timedelta(days=1)).isoformat()
    assert report["edge_source"] == "probe"
    assert report["days_unwritten"] == 0
    assert report["outcome"] == "completed"


async def test_a_forward_fire_with_an_unmoved_edge_spends_only_the_probe() -> None:
    """S19: nothing new is valued, so the turn sends exactly one (probe) request and writes nothing."""
    store, world = MemoryLaneStore(), GridWorld()
    _published_through(store, world, EDGE - timedelta(days=2))
    upstream = ScriptedUpstream(world.answer)

    exit_code, report = await run(spec_for(grid_lane()), ports_for(PER_DAY, store, upstream=upstream))

    assert exit_code == 0
    assert [send.probe for send in upstream.sends] == [True]
    assert report["requests"] == 1
    assert report["weighted_calls"] == report["probe_weighted_calls"] == PROBE_WEIGHT
    assert store.writes() == []
    owed = unwritten_by_day(report)
    assert {day: entry["behind_edge"] for day, entry in owed.items()} == {
        (EDGE - timedelta(days=1)).isoformat(): False,
        EDGE.isoformat(): False,
    }
    assert report["probe"]["status"] == "ok"


@pytest.mark.parametrize(
    ("failure", "status", "reason"),
    [
        (SourceUnavailableError("upstream request failed with status 503"), "unavailable", "upstream_unavailable"),
        (SourceThrottledError("upstream request failed with status 429"), "deferred", "deferred_quota"),
    ],
)
async def test_an_unavailable_or_deferred_probe_gates_the_window_and_exits_zero(
    failure: Exception, status: str, reason: str
) -> None:
    """O-R3-1: the probe window is gated, an owed day older than it is still walked, and the turn exits 0."""
    store, world = MemoryLaneStore(), GridWorld()
    lane = grid_lane(days={"absence_recheck_days": 20})
    older = EDGE - timedelta(days=17)
    newest = EDGE - timedelta(days=1)
    world.publish([older, newest])
    _published_through(store, world, EDGE - timedelta(days=2), first=EDGE - timedelta(days=16))
    store.publish(GRID_STREAM, EDGE - timedelta(days=19), _grid_rows(world, older))
    store.publish(GRID_STREAM, EDGE - timedelta(days=18), _grid_rows(world, older))

    def answer(endpoint: str, parameters: dict[str, str], probe: bool) -> bytes:
        if probe:
            raise failure
        return world.answer(endpoint, parameters, probe)

    upstream = ScriptedUpstream(answer)

    exit_code, report = await run(spec_for(lane), ports_for(PER_DAY, store, upstream=upstream))

    assert exit_code == 0
    assert report["probe_status"] == status
    assert _sent_days(upstream) == {older}
    assert store.status(GRID_STREAM, older) == "data"
    owed = unwritten_by_day(report)
    assert owed[newest.isoformat()]["reason"] == reason
    assert owed[EDGE.isoformat()]["reason"] == reason
    assert report["outcome"] == "incomplete"


async def test_a_probe_told_to_wait_an_hour_stops_the_walk_and_the_next_fire_inside_the_hour() -> None:
    """Review M3 follow-up: a probe 429 whose Retry-After the ladder would never sleep opens the circuit and
    persists the wait, so neither the walk behind the probe window nor a fire 30 minutes later sends anything."""
    store, world = MemoryLaneStore(), GridWorld()
    lane = grid_lane(days={"absence_recheck_days": 20})
    older = EDGE - timedelta(days=17)
    world.publish([older])
    _published_through(store, world, EDGE - timedelta(days=2), first=EDGE - timedelta(days=16))
    store.publish(GRID_STREAM, EDGE - timedelta(days=19), _grid_rows(world, older))
    store.publish(GRID_STREAM, EDGE - timedelta(days=18), _grid_rows(world, older))

    def answer(endpoint: str, parameters: dict[str, str], probe: bool) -> bytes:
        if probe:
            raise SourceThrottledError("upstream request failed with status 429", retry_after_seconds=3600)
        return world.answer(endpoint, parameters, probe)

    upstream = ScriptedUpstream(answer)
    cooldowns = ProviderCooldowns(MemoryAvailabilityStorage())

    exit_code, report = await run(spec_for(lane), ports_for(PER_DAY, store, upstream=upstream, cooldowns=cooldowns))

    assert exit_code == 0
    assert [send.probe for send in upstream.sends] == [True]
    assert report["throttled_until"] == (NOW + timedelta(hours=1)).isoformat()
    assert unwritten_by_day(report)[older.isoformat()]["reason"] == "deferred_quota"
    later = ManualClock(NOW + timedelta(minutes=30))

    inside_exit, inside = await run(
        spec_for(lane), ports_for(PER_DAY, store, upstream=upstream, clock=later, cooldowns=cooldowns)
    )

    assert inside_exit == 0
    assert len(upstream.sends) == 1
    assert inside["throttled_until"] == report["throttled_until"]
    assert unwritten_by_day(inside)[older.isoformat()]["reason"] == "deferred_quota"


async def test_an_edge_far_behind_the_lag_fans_out_nothing_past_it_and_drains_the_backlog_oldest_first() -> None:
    """The shortwave livelock: days past a stuck edge cost nothing, and the cap is spent on the oldest owed days."""
    store, world = MemoryLaneStore(), GridWorld()
    lane = grid_lane(days={"absence_recheck_days": 20}, budget={"forward_max_weighted_calls": 14})
    stuck_edge = EDGE - timedelta(days=10)
    backlog = [EDGE - timedelta(days=18), EDGE - timedelta(days=16), EDGE - timedelta(days=12), stuck_edge]
    world.publish(days_between(EDGE - timedelta(days=19), stuck_edge))
    for day in days_between(EDGE - timedelta(days=19), stuck_edge):
        if day not in backlog:
            store.publish(GRID_STREAM, day, _grid_rows(world, day))
    upstream = ScriptedUpstream(world.answer)

    exit_code, report = await run(spec_for(lane), ports_for(PER_DAY, store, upstream=upstream))

    assert exit_code == 0
    assert _sent_days(upstream) == set(backlog[:3])
    assert max(_sent_days(upstream)) <= stuck_edge
    owed = unwritten_by_day(report)
    assert owed[stuck_edge.isoformat()]["reason"] == "deferred_budget"
    past_edge = days_between(stuck_edge + timedelta(days=1), EDGE)
    assert all(owed[day.isoformat()]["detail"] == "newer_than_probed_edge" for day in past_edge)
    assert report["weighted_calls"] <= lane.budget.forward_max_weighted_calls


async def test_a_provisional_window_ends_yesterday_utc_and_never_asks_for_today() -> None:
    """O1: a `write_and_recheck` lane with no lag still stops at yesterday UTC."""
    store = MemoryLaneStore()
    world = PointWorld(readings={(day, "s1"): 1.0 for day in days_between(TODAY - timedelta(days=5), TODAY)})
    upstream = ScriptedUpstream(world.answer)

    exit_code, report = await run(spec_for(point_lane()), ports_for(PointRecheckStrategy(), store, upstream=upstream))

    yesterday = TODAY - timedelta(days=1)
    assert exit_code == 0
    assert report["window_last"] == yesterday.isoformat()
    assert {send.parameters["end"] for send in upstream.sends} == {yesterday.isoformat()}
    assert (POINT_STREAM, TODAY) not in store.statuses


async def test_a_gap_fill_turn_is_refused_unless_the_lane_enables_it() -> None:
    """S12: gap-fill ships disabled; the turn refuses before any census or send (exit 78 via the command)."""
    store = MemoryLaneStore()
    upstream = ScriptedUpstream(GridWorld().answer)

    with pytest.raises(GapFillDisabledError):
        await run(spec_for(grid_lane(), mode="gap-fill"), ports_for(PER_DAY, store, upstream=upstream))

    assert upstream.sends == []


async def test_an_enabled_gap_fill_turn_fills_the_holes_before_the_forward_window_oldest_first() -> None:
    """D3: the same code path over the older window; the forward window's own days are not its business."""
    store, world = MemoryLaneStore(), GridWorld()
    floor = WINDOW_FIRST - timedelta(days=6)
    lane = grid_lane(
        days={"floor": floor},
        schedule={"gap_fill_enabled": True, "gap_fill_enabled_at_gate": "G6"},
        budget={"gap_fill_max_weighted_calls": 8},
    )
    holes = days_between(floor, WINDOW_FIRST - timedelta(days=1))
    world.publish(holes)
    upstream = ScriptedUpstream(world.answer)

    exit_code, report = await run(spec_for(lane, mode="gap-fill"), ports_for(PER_DAY, store, upstream=upstream))

    assert exit_code == 0
    assert sorted(_sent_days(upstream)) == holes[:2]
    assert all(store.status(GRID_STREAM, day) == "data" for day in holes[:2])
    assert {entry["reason"] for entry in unwritten_by_day(report).values()} == {"deferred_budget"}
    assert report["cap"] == lane.budget.gap_fill_max_weighted_calls


@pytest.mark.parametrize(
    ("capability", "earliest", "owed", "fill", "retention_exceeded"),
    [
        ("archive", date(2026, 1, 3), [date(2026, 1, 1), date(2026, 1, 4)], [date(2026, 1, 4)], [date(2026, 1, 1)]),
        ("none", None, [date(2026, 1, 4)], [], [date(2026, 1, 4)]),
    ],
)
def test_gap_fill_holes_the_source_cannot_serve_are_retention_exceeded(
    capability: str, earliest: date | None, owed: list[date], fill: list[date], retention_exceeded: list[date]
) -> None:
    """A hole older than the source's history, or on a source with none, is never asked for."""
    lane = grid_lane(
        schedule={"gap_fill_enabled": True, "gap_fill_enabled_at_gate": "G6"},
        source={"history": {"capability": capability, "earliest": earliest}},
    )

    plan = plan_gap_fill(lane, owed=owed, max_days=1)

    assert list(plan.fill) == fill
    assert list(plan.retention_exceeded) == retention_exceeded


@pytest.mark.parametrize(
    ("inputs", "receipt", "dirty"),
    [
        ({"a": "1"}, {}, True),
        ({"a": "1"}, {"a": "1"}, False),
        ({"a": "2"}, {"a": "1"}, True),
        ({"a": "1", "b": "1"}, {"a": "1"}, True),
        ({}, {"a": "1"}, False),
    ],
)
def test_a_transform_day_is_dirty_when_its_input_digests_differ_from_its_receipt(
    inputs: dict[str, str], receipt: dict[str, str], dirty: bool
) -> None:
    """D4 + S11: rebuild on a new or changed input; a day no input has published is never derived."""
    day = date(2026, 9, 1)

    result = transform_dirty_days([day], input_digests={day: inputs}, receipt_digests={day: receipt})

    assert result == ((day,) if dirty else ())
