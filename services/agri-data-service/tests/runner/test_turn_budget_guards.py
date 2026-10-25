"""Two time-budget guards, driven through `run_turn`: a stalled unit's per-unit fetch cap (`fetch.py`) and the
write phase's own deadline mirroring `_repair_ladders`' guard (`turn.py`). The upstream and the bucket are the
only fakes (`tests/runner/fakes.py`); the per-unit cap is patched small so a stalled fetch costs the test a
fraction of a real second instead of the real 330 s ceiling.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

from agri_data_service.pipeline.runner import fetch as fetch_module
from tests.runner.fakes import (
    TODAY,
    ManualClock,
    MemoryLaneStore,
    ScriptedUpstream,
    days_between,
    grid_lane,
    grid_table,
    ports_for,
    run,
    spec_for,
    unwritten_by_day,
)
from tests.runner.fixtures.grid_refuse import CELLS, GRID_STREAM, GridRefuseStrategy, GridWorld

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]
    import pytest

    from agri_data_service.pipeline.runner.receipts import DayReceipt
    from agri_data_service.pipeline.runner.writer import WriteResult

EDGE = TODAY - timedelta(days=5)
#: The grid lane's forward window: 14 days ending at the lag-derived edge (matches `test_write_outcomes.py`).
WINDOW = days_between(EDGE - timedelta(days=13), EDGE)
#: One request per day (no cell chunking): a stalled day is a single unit, not a partial-chunk day.
ONE_UNIT_PER_DAY = GridRefuseStrategy(span_days=1, chunk_size=len(CELLS))
REAL_FETCH = GridRefuseStrategy.fetch


def _seed_published(store: MemoryLaneStore, world: GridWorld, days: list[date]) -> None:
    """Seed `days` as already published, exactly as a prior turn would have written them."""
    world.publish(days)
    for day in days:
        store.publish(GRID_STREAM, day, grid_table(day, {cell: world.values[(day, cell)] for cell in CELLS}))


# --- FIX 1: a stalled fetch is capped, not the whole turn's deadline ----------------------------


async def test_a_stalled_unit_is_capped_and_the_turn_still_completes_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One owed day's fetch never resolves; the per-unit cap cancels it and the turn still finishes, exit 0.

    Without FIX 1 the stalled `await self.strategy.fetch(...)` would only be caught between ladder attempts,
    so the ladder's three ordeals could ride the real provider timeout (~906 s) each; here the cap is patched
    to a fraction of a second, so the whole turn (fetch, settle, write) completes in well under a second while
    the other owed day is written normally.
    """
    monkeypatch.setattr(fetch_module, "PER_UNIT_FETCH_CAP_SECONDS", 0.01)
    world = GridWorld()
    stalled, free = WINDOW[2], WINDOW[9]
    store = MemoryLaneStore()
    _seed_published(store, world, [day for day in WINDOW if day not in {stalled, free}])
    world.publish([stalled, free])
    stalled_unit = f"{stalled.isoformat()}..{stalled.isoformat()}:{','.join(CELLS)}"

    async def fetch_with_one_stall(self: object, request: object, client: object) -> object:
        if request.unit == stalled_unit:
            await asyncio.sleep(99_999)
        return await REAL_FETCH(self, request, client)

    monkeypatch.setattr(GridRefuseStrategy, "fetch", fetch_with_one_stall)
    upstream = ScriptedUpstream(world.answer)

    started = asyncio.get_running_loop().time()
    exit_code, report = await run(spec_for(grid_lane()), ports_for(ONE_UNIT_PER_DAY, store, upstream=upstream))
    elapsed = asyncio.get_running_loop().time() - started

    assert exit_code == 0
    assert store.writes() == [f"write:{GRID_STREAM}:{free.isoformat()}:availability=True"]
    unwritten = unwritten_by_day(report)
    assert unwritten[stalled.isoformat()]["reason"] == "upstream_unavailable"
    assert report["units_fetched"] == 1
    assert report["units_failed"] == 1
    wall_clock_ceiling_seconds = 5.0  # capped ordeals, not real 300 s provider timeouts
    assert elapsed < wall_clock_ceiling_seconds


# --- FIX 2: the write phase has its own deadline, mirroring _repair_ladders ---------------------


@dataclass
class ClockAdvancingStore(MemoryLaneStore):
    """A writer that advances `clock` by `seconds_per_write` after each successful day write.

    Simulates a write phase slow enough to cross `write_deadline` mid-loop, without needing to fake a
    multi-hundred-second real clock: `clock.sleep` is the same fake-clock primitive the runner's own
    backoffs use (`tests/runner/fakes.py::ManualClock`).
    """

    clock: ManualClock | None = None
    seconds_per_write: float = 0.0

    async def write_day(
        self, stream: str, day: date, table: pa.Table, receipt: DayReceipt, *, availability: bool
    ) -> WriteResult:
        result = await super().write_day(stream, day, table, receipt, availability=availability)
        if self.clock is not None and self.seconds_per_write:
            await self.clock.sleep(self.seconds_per_write)
        return result


async def test_a_slow_write_phase_defers_the_remaining_settled_days_as_unwritten() -> None:
    """The first settled day's write pushes the clock past `write_deadline`; the rest stay `deferred_budget`.

    Both days were already fetched and settled before `_publish` runs (FIX 1 is unrelated here), so this
    isolates the write-phase guard: written + unwritten must account for every day the turn fanned out.
    """
    clock = ManualClock()
    store = ClockAdvancingStore(clock=clock, seconds_per_write=10.0)
    world = GridWorld()
    first_day, second_day = WINDOW[2], WINDOW[9]
    _seed_published(store, world, [day for day in WINDOW if day not in {first_day, second_day}])
    world.publish([first_day, second_day])
    upstream = ScriptedUpstream(world.answer)
    lane = grid_lane(budget={"turn_timeout_seconds": 5.0})

    exit_code, report = await run(spec_for(lane), ports_for(ONE_UNIT_PER_DAY, store, upstream=upstream, clock=clock))

    assert exit_code == 0
    assert store.writes() == [f"write:{GRID_STREAM}:{first_day.isoformat()}:availability=True"]
    unwritten = unwritten_by_day(report)
    assert unwritten[second_day.isoformat()]["reason"] == "deferred_budget"
    assert unwritten[second_day.isoformat()]["stream"] == GRID_STREAM
    owed_day_count = 2  # the two owed days this fixture plans
    assert report["days_fanned_out"] == owed_day_count
    assert report["days_written"] + report["days_unwritten"] == report["days_fanned_out"]
