"""Historical targets idle on proven support and reopen on missing evidence."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING

import pyarrow as pa  # type: ignore[import-untyped]
import pytest
from pydantic import ValidationError

from agri_data_service.foundation.lane_config.models import LaneCoverageTarget
from agri_data_service.pipeline.runner.census import LaneCensus
from agri_data_service.pipeline.runner.coverage_target import assess_coverage
from agri_data_service.pipeline.runner.exits import EXIT_CONFIGURATION_ERROR, TurnConfigurationError, exit_code_for
from agri_data_service.pipeline.runner.windows import gap_fill_window
from tests.runner.fakes import MemoryLaneStore, ScriptedUpstream, grid_lane, ports_for, run, spec_for
from tests.runner.fixtures.grid_refuse import CELLS, GRID_STREAM, GridRefuseStrategy, GridWorld

if TYPE_CHECKING:
    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.foundation.region.manifest import Region

FIRST = date(2026, 8, 1)
LAST = date(2026, 8, 2)


class CoveredGrid(GridRefuseStrategy):
    """Fixture with the stable complete spatial support required by target idling."""

    def source_unit_ids(self, _lane: LaneConfig, _region: Region) -> frozenset[str]:
        return frozenset(CELLS)


def _lane(*, minimum_fraction: float = 1.0) -> LaneConfig:
    return grid_lane(
        days={"floor": FIRST.isoformat()},
        schedule={"gap_fill_enabled": True, "gap_fill_cron": "0 0 * * *", "gap_fill_enabled_at_gate": "G3"},
        coverage_target={
            "first_day": FIRST.isoformat(),
            "last_day": LAST.isoformat(),
            "minimum_fraction": minimum_fraction,
        },
    )


def _seed(store: MemoryLaneStore, day: date) -> None:
    store.publish(GRID_STREAM, day, pa.table({"cell_id": CELLS, "value": [1.0] * len(CELLS)}))
    receipt = store.receipts[(GRID_STREAM, day)]
    store.receipts[(GRID_STREAM, day)] = replace(
        receipt, expected_unit_ids=frozenset(CELLS), present_unit_ids=frozenset(CELLS)
    )


async def test_target_idles_then_reopens_after_a_day_disappears() -> None:
    store, world = MemoryLaneStore(), GridWorld()
    for day in (FIRST, LAST):
        _seed(store, day)
    upstream = ScriptedUpstream(world.answer)
    spec = spec_for(_lane(), mode="gap-fill")
    code, report = await run(spec, ports_for(CoveredGrid(span_days=1), store, upstream=upstream))
    assert code == 0
    assert report["coverage_target"]["state"] == "target_reached"
    assert report["coverage_target"]["remaining_days"] == 0
    assert not upstream.fan_out_sends

    del store.statuses[(GRID_STREAM, LAST)]
    world.publish([LAST])
    _, reopened = await run(spec, ports_for(CoveredGrid(span_days=1), store, upstream=upstream))
    assert reopened["coverage_target"]["state"] == "target_unmet"
    assert upstream.fan_out_sends


async def test_threshold_reports_intentionally_remaining_holes() -> None:
    store, world = MemoryLaneStore(), GridWorld()
    _seed(store, FIRST)
    upstream = ScriptedUpstream(world.answer)
    _, report = await run(
        spec_for(_lane(minimum_fraction=0.5), mode="gap-fill"), ports_for(CoveredGrid(), store, upstream=upstream)
    )
    assert report["coverage_target"]["state"] == "target_reached"
    assert report["coverage_target"]["remaining_days"] == 1
    assert not upstream.fan_out_sends


@pytest.mark.parametrize("repair_fails", [False, True])
async def test_target_reached_still_repairs_ladders_without_upstream_requests(repair_fails: bool) -> None:
    store, world = MemoryLaneStore(), GridWorld()
    for day in (FIRST, LAST):
        _seed(store, day)
    store.ladder_incomplete.add((GRID_STREAM, LAST))
    if repair_fails:
        store.unrepairable.add((GRID_STREAM, LAST))
    upstream = ScriptedUpstream(world.answer)
    code, report = await run(
        spec_for(_lane(minimum_fraction=0.5), mode="gap-fill"), ports_for(CoveredGrid(), store, upstream=upstream)
    )
    assert code == 0
    assert report["coverage_target"]["state"] == "target_reached"
    assert report["coverage_target"]["remaining_days"] == 1
    assert not upstream.fan_out_sends
    assert report["days_ladder_owed"] == 1
    if repair_fails:
        assert report["ladder_repairs"] == 0
        assert store.status(GRID_STREAM, LAST) == "incomplete"
        assert any(
            entry["reason"] == "ladder_owed" and entry["day"] == LAST.isoformat() for entry in report["unwritten"]
        )
    else:
        assert report["ladder_repairs"] == 1
        assert store.status(GRID_STREAM, LAST) == "data"
        assert report["days_unwritten"] == 0


@pytest.mark.parametrize("defect", ["ladder", "support", "unresolved", "pending"])
async def test_partial_or_unproven_day_never_satisfies_target(defect: str) -> None:
    store, world = MemoryLaneStore(), GridWorld()
    for day in (FIRST, LAST):
        _seed(store, day)
    if defect == "ladder":
        store.ladder_incomplete.add((GRID_STREAM, LAST))
    elif defect == "support":
        store.receipts[(GRID_STREAM, LAST)] = replace(
            store.receipts[(GRID_STREAM, LAST)], expected_unit_ids=None, present_unit_ids=None
        )
    elif defect == "unresolved":
        store.receipts[(GRID_STREAM, LAST)] = replace(store.receipts[(GRID_STREAM, LAST)], source_resolved=False)
    else:
        store.receipts[(GRID_STREAM, LAST)] = replace(store.receipts[(GRID_STREAM, LAST)], publication_state="pending")
    _, report = await run(
        spec_for(_lane(), mode="gap-fill"),
        ports_for(CoveredGrid(span_days=1), store, upstream=ScriptedUpstream(world.answer)),
    )
    assert report["coverage_target"]["state"] == "target_unmet"
    assert report["coverage_target"]["incomplete_days"] == 1


async def test_absence_is_not_data_target_success() -> None:
    store, world = MemoryLaneStore(), GridWorld()
    for day in (FIRST, LAST):
        store.statuses[(GRID_STREAM, day)] = "absent"
    upstream = ScriptedUpstream(world.answer)
    _, report = await run(spec_for(_lane(), mode="gap-fill"), ports_for(CoveredGrid(), store, upstream=upstream))
    assert report["coverage_target"]["state"] == "no_fillable_gaps"
    assert report["coverage_target"]["data_days"] == 0
    assert report["coverage_target"]["governed_absence_days"] == (LAST - FIRST).days + 1
    assert not upstream.fan_out_sends


async def test_strategy_without_support_contract_refuses_target() -> None:
    store, world = MemoryLaneStore(), GridWorld()
    upstream = ScriptedUpstream(world.answer)
    with pytest.raises(TurnConfigurationError, match="requires nonempty source support") as error:
        await run(spec_for(_lane(), mode="gap-fill"), ports_for(GridRefuseStrategy(), store, upstream=upstream))
    assert exit_code_for(error.value) == EXIT_CONFIGURATION_ERROR
    assert not upstream.fan_out_sends


async def test_forward_refresh_is_not_stopped_by_historical_target() -> None:
    store, world = MemoryLaneStore(), GridWorld()
    for day in (FIRST, LAST):
        _seed(store, day)
    world.publish([date(2026, 9, 15)])
    upstream = ScriptedUpstream(world.answer)
    _, report = await run(spec_for(_lane()), ports_for(CoveredGrid(span_days=1), store, upstream=upstream))
    assert "coverage_target" not in report
    assert upstream.fan_out_sends


async def test_future_fixed_end_does_not_claim_complete_target() -> None:
    store, world = MemoryLaneStore(), GridWorld()
    lane = _lane()
    target = LaneCoverageTarget(first_day=FIRST, last_day=date(2026, 10, 1), minimum_fraction=0.01)
    lane = lane.model_copy(update={"coverage_target": target})
    _seed(store, FIRST)
    upstream = ScriptedUpstream(world.answer)
    _, report = await run(
        spec_for(lane, mode="gap-fill"), ports_for(CoveredGrid(span_days=1), store, upstream=upstream)
    )
    assert report["coverage_target"]["state"] == "range_not_yet_historical"
    assert report["coverage_target"]["declared_last_day"] == "2026-10-01"


def test_target_denominator_includes_missing_days_and_every_stream() -> None:
    target = LaneCoverageTarget(first_day=FIRST, last_day=LAST)
    census = LaneCensus(first=FIRST, last=LAST, streams={"one": {FIRST: "data", LAST: "data"}, "two": {FIRST: "data"}})
    status = assess_coverage(target, census)
    assert status.required_days == (LAST - FIRST).days + 1
    assert status.data_days == 1
    assert not status.reached


@pytest.mark.parametrize("fraction", [0, -1, 1.01, float("nan"), float("inf")])
def test_invalid_fraction_is_rejected(fraction: float) -> None:
    with pytest.raises(ValidationError):
        LaneCoverageTarget(first_day=FIRST, minimum_fraction=fraction)


def test_invalid_range_and_legacy_executor_are_rejected() -> None:
    with pytest.raises(ValidationError):
        LaneCoverageTarget(first_day=LAST, last_day=FIRST)
    with pytest.raises(ValidationError):
        LaneCoverageTarget(first_day=date(1900, 1, 1), last_day=LAST)
    with pytest.raises(ValidationError):
        grid_lane(executor="legacy", coverage_target={"first_day": FIRST.isoformat()})


def test_target_window_is_bounded_and_does_not_change_forward_floor() -> None:
    lane = _lane()
    window = gap_fill_window(lane, today=date(2026, 9, 20))
    assert window is not None
    assert (window.first, window.last) == (FIRST, LAST)
    assert lane.days is not None
    assert lane.days.floor == FIRST
