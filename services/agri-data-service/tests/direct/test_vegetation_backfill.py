"""Every backfill turn must leave a durable mark on every day it spends, or the walk never advances.

Every "Postgres" and every lane-day publication here is a fake -- this test never opens a database
connection and never writes a bucket. What it pins is the ARITHMETIC of the walk, which is what
decides whether a turn makes progress or re-takes the same days forever.
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.pipeline.direct.vegetation import backfill
from agri_data_service.pipeline.direct.vegetation.backfill import (
    VEGETATION_BACKFILL_DURABLE_OUTCOMES,
    VEGETATION_BACKFILL_MAX_UNRESOLVED_DAYS,
    VegetationBackfillConfig,
    _incomplete_days_ascending,
    _report,
    _walk_backlog,
    backfill_ceiling,
    backfill_floor,
    parse_args,
)
from agri_data_service.pipeline.direct.vegetation.forward import VEGETATION_DIRECT_ALL_TIERS
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.availability_extension import AvailabilityExtensionTally

if TYPE_CHECKING:
    from collections.abc import Callable

FIRST_DAY = date(2022, 8, 5)


def _backlog(count: int) -> tuple[date, ...]:
    return tuple(FIRST_DAY + timedelta(days=offset) for offset in range(count))


def _config(*, max_days: int) -> VegetationBackfillConfig:
    return VegetationBackfillConfig(max_days=max_days, time_budget_seconds=900.0, run_id="backfill-test")


async def _walk(*, backlog: tuple[date, ...], max_days: int) -> list[dict[str, object]]:
    """Run one walk with every lane-day publication stubbed, so only the budget arithmetic is exercised.

    The session, store and availability storage are never touched by the stub, so they are passed as
    `None`: threading real ones in would test the fakes rather than the walk.
    """
    return await _walk_backlog(
        None,
        None,
        lane=None,
        backlog=backlog,
        run_id="backfill-test",
        config=_config(max_days=max_days),
        deadline=float("inf"),
        availability_storage=None,
        availability=AvailabilityExtensionTally(),
    )


def _stub_publication(monkeypatch: pytest.MonkeyPatch, outcome_for: Callable[[date], str]) -> list[date]:
    """Replace the one lane-day publication with a stub, recording every day the walk attempted."""
    attempted: list[date] = []

    async def _fake_publish(session: Any, store: Any, **kwargs: Any) -> dict[str, object]:  # noqa: ARG001
        day = kwargs["day"]
        attempted.append(day)
        return {"day": day.isoformat(), "outcome": outcome_for(day)}

    monkeypatch.setattr(backfill, "_publish_from_postgres", _fake_publish)
    return attempted


# --- The walk advances -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_day_that_settles_nothing_does_not_spend_the_turns_day_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DO NOT DELETE. A `raised`/`blocked` day at the floor used to hide every day behind it forever:
    it consumed one of `--max-days`, wrote nothing durable, and was still first next turn."""
    backlog = _backlog(5)
    stuck = set(backlog[:3])
    attempted = _stub_publication(monkeypatch, lambda day: "raised" if day in stuck else "absent")

    published = await _walk(backlog=backlog, max_days=2)

    assert attempted == list(backlog)  # it stepped over all three stuck days
    durable = [entry for entry in published if entry["outcome"] in VEGETATION_BACKFILL_DURABLE_OUTCOMES]
    assert len(durable) == 2


@pytest.mark.asyncio
async def test_durable_days_do_spend_the_budget_so_a_turn_stays_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backlog = _backlog(10)
    attempted = _stub_publication(monkeypatch, lambda _day: "absent")

    published = await _walk(backlog=backlog, max_days=3)

    assert attempted == list(backlog[:3])
    assert len(published) == 3


@pytest.mark.asyncio
async def test_a_governed_absence_counts_as_durable_because_it_removes_the_day_from_the_backlog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Postgres-empty day comes back `absent` from the registered adapter, and that IS the fix:
    the four-rung marker is what `_incomplete_days_ascending` skips on every later turn."""
    _stub_publication(monkeypatch, lambda _day: "absent")

    published = await _walk(backlog=_backlog(1), max_days=1)

    assert published[0]["outcome"] == "absent"
    assert "absent" in VEGETATION_BACKFILL_DURABLE_OUTCOMES
    assert "raised" not in VEGETATION_BACKFILL_DURABLE_OUTCOMES
    assert "incomplete_after_write" not in VEGETATION_BACKFILL_DURABLE_OUTCOMES


@pytest.mark.asyncio
async def test_a_systemic_failure_stops_the_turn_instead_of_walking_the_whole_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stepping over an unresolved day may never become retrying 1,500 of them on one wall clock."""
    backlog = _backlog(VEGETATION_BACKFILL_MAX_UNRESOLVED_DAYS + 20)
    attempted = _stub_publication(monkeypatch, lambda _day: "raised")

    published = await _walk(backlog=backlog, max_days=5)

    assert len(attempted) == VEGETATION_BACKFILL_MAX_UNRESOLVED_DAYS
    assert published[-1]["outcome"] == "unresolved_day_budget_exhausted"


# --- The census that makes a governed absence durable -------------------------------------------


def _statuses(day_status: dict[date, dict[int, str]]) -> dict[int, dict[date, str]]:
    return {tier: {day: rungs[tier] for day, rungs in day_status.items()} for tier in VEGETATION_DIRECT_ALL_TIERS}


def test_a_day_absent_at_every_rung_is_never_selected_again() -> None:
    """This is what makes the fix durable: the absence is a permanent answer, not a per-turn note."""
    absent = dict.fromkeys(VEGETATION_DIRECT_ALL_TIERS, "absent")

    selected = _incomplete_days_ascending(_statuses({FIRST_DAY: absent}))

    assert selected == ()


def test_a_day_missing_at_every_rung_is_selected_oldest_first() -> None:
    missing = dict.fromkeys(VEGETATION_DIRECT_ALL_TIERS, "missing")
    second = FIRST_DAY + timedelta(days=1)

    selected = _incomplete_days_ascending(_statuses({second: missing, FIRST_DAY: missing}))

    assert selected == (FIRST_DAY, second)


def test_a_day_complete_at_the_base_rung_alone_is_still_owed() -> None:
    partial = {tier: ("data" if tier == LANE_BASE_ZOOM_TIER else "missing") for tier in VEGETATION_DIRECT_ALL_TIERS}

    assert _incomplete_days_ascending(_statuses({FIRST_DAY: partial})) == (FIRST_DAY,)


# --- The operator surface -----------------------------------------------------------------------


def test_the_report_separates_the_days_it_settled_from_the_days_it_only_touched() -> None:
    """A turn that touched 30 days and settled none must not read as progress."""
    published: list[dict[str, object]] = [
        {"day": "2022-08-05", "outcome": "absent"},
        {"day": "2022-08-06", "outcome": "written"},
        {"day": "2022-08-07", "outcome": "raised"},
    ]

    report = _report(
        "backfill-test",
        floor=FIRST_DAY,
        ceiling=backfill_ceiling(),
        backlog=99,
        published=published,
        availability=AvailabilityExtensionTally(),
    )

    assert report["durable_days"] == 2
    assert report["unresolved_days"] == 1
    assert report["backlog_days"] == 99


def test_from_day_is_parsed_as_a_strict_iso_day_and_defaults_to_the_registered_floor() -> None:
    assert parse_args([]).from_day is None
    assert parse_args(["--from-day", "2024-03-01"]).from_day == date(2024, 3, 1)


def test_from_day_may_not_reach_past_the_ownership_boundary_into_the_forward_writers_range() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--from-day", (backfill_ceiling() + timedelta(days=1)).isoformat()])


def test_a_malformed_from_day_is_refused_at_the_boundary() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--from-day", "march the first"])


def test_the_registered_history_floor_is_the_backfill_floor() -> None:
    assert backfill_floor() == FIRST_DAY
