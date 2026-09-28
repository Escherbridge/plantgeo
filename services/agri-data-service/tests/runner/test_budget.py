"""Per-turn, per-mode caps (S19): what a turn may spend, measured on the sends the upstream actually received."""

from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urlencode

import pytest

from agri_data_service.foundation.observability.usage import open_meteo_weight_for_url
from agri_data_service.pipeline.runner.budget import TurnBudget, mode_cap, select_affordable_days
from tests.runner.fakes import (
    TODAY,
    MemoryLaneStore,
    ScriptedUpstream,
    Send,
    days_between,
    grid_lane,
    ports_for,
    providers,
    run,
    spec_for,
    unwritten_by_day,
)
from tests.runner.fixtures.grid_refuse import GRID_STREAM, GridRefuseStrategy, GridWorld

EDGE = TODAY - timedelta(days=5)
WINDOW_FIRST = EDGE - timedelta(days=13)
#: An admission `--weighted-budget` below the TOML cap: the probe (2) plus one day's two chunks (4).
ADMISSION_BUDGET = 6


def _weight_of(send: Send) -> float:
    """What Open-Meteo charges for one send, priced by the meter's own formula on the free-host URL."""
    endpoint = providers()["open-meteo"].endpoints[send.endpoint]
    return open_meteo_weight_for_url(f"https://{endpoint.host}{endpoint.path}?{urlencode(send.parameters)}")


@pytest.mark.parametrize(
    ("mode", "lane_overrides", "cap", "days_written"),
    [
        ("forward", {"budget": {"forward_max_weighted_calls": 20, "gap_fill_max_weighted_calls": 1600}}, 20, 4),
        (
            "gap-fill",
            {
                "budget": {"forward_max_weighted_calls": 1600, "gap_fill_max_weighted_calls": 9},
                "days": {"floor": WINDOW_FIRST - timedelta(days=6)},
                "schedule": {"gap_fill_enabled": True, "gap_fill_enabled_at_gate": "G6"},
            },
            9,
            2,
        ),
    ],
)
async def test_a_turn_never_exceeds_its_mode_cap(
    mode: str, lane_overrides: dict[str, object], cap: int, days_written: int
) -> None:
    """The mode's own cap binds (never the other mode's), the probe counts against it, and the rest defers."""
    store, world = MemoryLaneStore(), GridWorld()
    world.publish(days_between(WINDOW_FIRST - timedelta(days=6), EDGE))
    upstream = ScriptedUpstream(world.answer)
    lane = grid_lane(**lane_overrides)

    exit_code, report = await run(
        spec_for(lane, mode=mode),  # type: ignore[arg-type]
        ports_for(GridRefuseStrategy(span_days=1), store, upstream=upstream),
    )

    spent = sum(_weight_of(send) for send in upstream.sends)
    assert exit_code == 0
    assert spent <= cap
    assert report["weighted_calls"] == spent
    assert report["cap"] == cap
    assert len({day for (_, day), status in store.statuses.items() if status == "data"}) == days_written
    assert {entry["reason"] for entry in unwritten_by_day(report).values()} == {"deferred_budget"}
    assert all(store.status(GRID_STREAM, day) == "missing" for day in _deferred(report))


def _deferred(report: dict[str, object]) -> list[date]:
    return [date.fromisoformat(day) for day in unwritten_by_day(report)]


async def test_an_admission_weighted_budget_lowers_the_cap_for_this_turn_only() -> None:
    """`--weighted-budget` below the TOML cap binds; above it, the TOML cap still does."""
    store, world = MemoryLaneStore(), GridWorld()
    world.publish(days_between(WINDOW_FIRST, EDGE))
    upstream = ScriptedUpstream(world.answer)

    exit_code, report = await run(
        spec_for(grid_lane(), weighted_budget=ADMISSION_BUDGET),
        ports_for(GridRefuseStrategy(span_days=1), store, upstream=upstream),
    )

    assert exit_code == 0
    assert sum(_weight_of(send) for send in upstream.sends) <= ADMISSION_BUDGET
    assert report["cap"] == ADMISSION_BUDGET
    assert report["weighted_budget"] == ADMISSION_BUDGET


@pytest.mark.parametrize(
    ("toml_cap", "weighted_budget", "expected"),
    [(1600, None, 1600), (1600, 500, 500), (1600, 5000, 1600), (1600, -3, 0)],
)
def test_the_weighted_budget_only_ever_lowers_the_mode_cap(
    toml_cap: int, weighted_budget: int | None, expected: int
) -> None:
    lane = grid_lane(budget={"forward_max_weighted_calls": toml_cap})

    assert mode_cap(lane.budget, "forward", weighted_budget=weighted_budget) == expected


def test_a_day_is_taken_whole_or_deferred_and_a_shared_unit_costs_nothing_twice() -> None:
    """Greedy oldest-first: a day that shares an already-taken unit is free; one that does not fit waits whole."""
    strategy = GridRefuseStrategy(span_days=2)
    days = [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 5)]
    requests = strategy.plan_requests(days, grid_lane(), None)  # type: ignore[arg-type]
    units_by_day = {day: [request for request in requests if day in request.days] for day in days}
    two_spans = 8.0
    budget = TurnBudget(cap=int(two_spans), basis="weighted_calls", provider=providers()["open-meteo"])

    selection = select_affordable_days(days, units_by_day, budget)

    assert selection.selected == (date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3))
    assert selection.deferred == (date(2026, 9, 5),)
    assert selection.reserved == two_spans
    assert {request.unit for request in selection.requests} == {
        request.unit for day in selection.selected for request in units_by_day[day]
    }
