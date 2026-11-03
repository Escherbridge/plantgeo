"""Recurring cost controls preserve quality debt, forward work and source-free repair."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from agri_data_service.foundation.lane_config.models import LaneRecurringCost
from agri_data_service.pipeline.runner.contract import Written
from agri_data_service.pipeline.runner.exits import TurnConfigurationError
from agri_data_service.pipeline.runner.rechecks import SourceRechecks, recheck_scope
from tests.runner.fakes import NOW, ManualClock, MemoryLaneStore, ScriptedUpstream, grid_lane, ports_for, run, spec_for
from tests.runner.fixtures.grid_refuse import CELLS, GRID_STREAM, GridRefuseStrategy, GridWorld

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.foundation.region.manifest import Region
    from agri_data_service.pipeline.runner.contract import DayContext, Settlement, SourceResponse

FIRST = date(2026, 8, 1)
LAST = date(2026, 8, 2)
INTERVAL = 7


class SupportGrid(GridRefuseStrategy):
    resolved = False

    def source_unit_ids(self, _lane: LaneConfig, _region: Region) -> frozenset[str]:
        return frozenset(CELLS)

    def settle(self, day: date, responses: Sequence[SourceResponse], context: DayContext) -> Settlement:
        result = super().settle(day, responses, context)
        if not isinstance(result, Written):
            return result
        present = frozenset(self.rows(day, responses)["cell_id"].to_pylist())
        return replace(
            result, expected_unit_ids=frozenset(CELLS), present_unit_ids=present, source_resolved=self.resolved
        )


class ResolvedGrid(SupportGrid):
    resolved = True


def _lane() -> LaneConfig:
    return grid_lane(
        days={"floor": FIRST.isoformat(), "partial_day": "write_and_recheck"},
        schedule={"gap_fill_enabled": True, "gap_fill_cron": "0 0 * * *", "gap_fill_enabled_at_gate": "G3"},
        coverage_target={"first_day": FIRST.isoformat(), "last_day": LAST.isoformat(), "minimum_fraction": 1.0},
        recurring_cost={"source_contract": "fixture-v1", "unresolved_recheck_days": INTERVAL},
    )


@pytest.mark.parametrize("field", ["correction_recheck_days", "correction_max_weighted_calls"])
def test_correction_cadence_and_cap_are_required_together(field: str) -> None:
    with pytest.raises(ValidationError, match="cadence and cap must be declared together"):
        LaneRecurringCost.model_validate({"source_contract": "fixture-v1", field: 7})


async def test_policy_refuses_missing_support_or_scheduling_port_before_provider_work() -> None:
    lane, store, world = _lane(), MemoryLaneStore(), GridWorld()
    upstream = ScriptedUpstream(world.answer)
    for strategy in (GridRefuseStrategy(), SupportGrid()):
        ports = ports_for(strategy, store, upstream=upstream)
        if isinstance(strategy, SupportGrid):
            ports.rechecks = None
        with pytest.raises(TurnConfigurationError, match="requires source support and a scheduling store"):
            await run(spec_for(lane, mode="gap-fill"), ports)
    assert not upstream.sends


async def test_unchanged_full_unresolved_advances_retry_without_rewriting_and_idles_until_due() -> None:
    lane, store, world = _lane(), MemoryLaneStore(), GridWorld()
    world.publish([FIRST, LAST])
    strategy = SupportGrid(span_days=1)
    spec = spec_for(lane, mode="gap-fill")
    upstream = ScriptedUpstream(world.answer)
    _, first = await run(spec, ports_for(strategy, store, upstream=upstream))
    assert first["coverage_target"]["state"] == "target_unmet"
    writes = list(store.writes())
    upstream.sends.clear()
    _, waiting = await run(spec, ports_for(strategy, store, upstream=upstream))
    assert not upstream.sends
    assert waiting["source_retry"]["waiting_days"] == len((FIRST, LAST))
    assert waiting["coverage_target"]["state"] == "target_unmet"
    assert waiting["outcome"] == "incomplete"
    later = NOW + timedelta(days=INTERVAL)
    _, retried = await run(spec, ports_for(strategy, store, upstream=upstream, clock=ManualClock(later)))
    assert upstream.sends
    assert store.writes() == writes
    assert retried["days_unchanged"] == len((FIRST, LAST))
    scope = recheck_scope(lane, spec.region, frozenset(CELLS))
    assert SourceRechecks(store.scheduling).checked((GRID_STREAM,), scope, FIRST, LAST) == {FIRST: later, LAST: later}
    upstream.sends.clear()
    await run(spec, ports_for(strategy, store, upstream=upstream, clock=ManualClock(later)))
    assert not upstream.sends


@pytest.mark.parametrize("failure", ["partial", "contended", "failed", "deferred"])
async def test_incomplete_or_unsuccessful_work_never_records_a_successful_check(failure: str) -> None:
    lane, store, world = _lane(), MemoryLaneStore(), GridWorld()
    world.publish([FIRST, LAST])
    if failure == "partial":
        for day in (FIRST, LAST):
            world.values[(day, CELLS[0])] = None
    elif failure == "contended":
        store.contended.update((GRID_STREAM, day) for day in (FIRST, LAST))
    elif failure == "deferred":
        lane = lane.model_copy(update={"budget": lane.budget.model_copy(update={"gap_fill_max_weighted_calls": 0})})

    def answer(endpoint: str, parameters: Mapping[str, str], probe: bool) -> bytes:
        if failure == "failed":
            raise TimeoutError("source unavailable")
        return world.answer(endpoint, parameters, probe)

    spec = spec_for(lane, mode="gap-fill")
    await run(spec, ports_for(SupportGrid(span_days=1), store, upstream=ScriptedUpstream(answer)))
    assert not store.scheduling.objects


async def test_waiting_unresolved_does_not_stop_source_free_repair() -> None:
    lane, store, world = _lane(), MemoryLaneStore(), GridWorld()
    world.publish([FIRST, LAST])
    spec = spec_for(lane, mode="gap-fill")
    await run(spec, ports_for(SupportGrid(span_days=1), store, upstream=ScriptedUpstream(world.answer)))
    store.receipts[(GRID_STREAM, LAST)] = replace(store.receipts[(GRID_STREAM, LAST)], source_resolved=True)
    store.ladder_incomplete.add((GRID_STREAM, LAST))
    upstream = ScriptedUpstream(world.answer)
    _, report = await run(spec, ports_for(SupportGrid(span_days=1), store, upstream=upstream))
    assert not upstream.sends
    assert report["ladder_repairs"] == 1
    assert report["source_retry"]["waiting_days"] == 1
    assert report["coverage_target"]["state"] == "target_unmet"


async def test_compare_never_updates_scheduling_even_with_policy_enabled() -> None:
    lane, store, world = _lane(), MemoryLaneStore(), GridWorld()
    world.publish([date(2026, 9, 15)])
    await run(
        spec_for(lane, compare=True),
        ports_for(SupportGrid(span_days=1), store, upstream=ScriptedUpstream(world.answer), compare=True),
    )
    assert not store.scheduling.objects


@pytest.mark.parametrize("cap", [0, 2, len(CELLS)])
async def test_corrections_choose_oldest_success_before_calendar_order_under_separate_cap(cap: int) -> None:
    lane = grid_lane(
        days={"floor": FIRST.isoformat(), "revision_window_days": 60, "revision_days_per_turn": 1},
        recurring_cost={
            "source_contract": "fixture-v1",
            "correction_recheck_days": 30,
            "correction_max_weighted_calls": cap,
        },
        budget={"forward_max_weighted_calls": 10},
    )
    store, world, strategy = MemoryLaneStore(), GridWorld(), ResolvedGrid(span_days=1)
    forward = date(2026, 9, 15)
    world.publish([FIRST, LAST, forward])
    # Seed genuine publications using the same source contract, then make LAST the older check.
    seed_lane = _lane()
    await run(spec_for(seed_lane, mode="gap-fill"), ports_for(strategy, store, upstream=ScriptedUpstream(world.answer)))
    scope = recheck_scope(lane, spec_for(lane).region, frozenset(CELLS))
    # The scheduling storage is independent of publications and completeness.
    store.scheduling = MemoryLaneStore().scheduling
    SourceRechecks(store.scheduling).confirm_many(
        GRID_STREAM,
        scope,
        {
            FIRST: NOW - timedelta(days=31),
            LAST: NOW - timedelta(days=40),
        },
        now=NOW,
    )
    upstream = ScriptedUpstream(world.answer)
    _, report = await run(spec_for(lane), ports_for(strategy, store, upstream=upstream))
    assert store.status(GRID_STREAM, forward) == "data"
    assert report["revision_first"] == LAST.isoformat()
    assert report["days_revised"] == 1
    assert report["requests"] <= lane.budget.forward_max_weighted_calls
    history_sends = [send for send in upstream.fan_out_sends if send.parameters["start_date"] == LAST.isoformat()]
    full_day_weight = len(CELLS)
    if cap < full_day_weight:
        assert not history_sends
    else:
        assert len(history_sends) == len(CELLS) // strategy.chunk_size
    upstream.sends.clear()
    _, second = await run(spec_for(lane), ports_for(strategy, store, upstream=upstream))
    assert second["revision_first"] == (FIRST if cap >= full_day_weight else LAST).isoformat()
