"""Day selection for the forward/backfill walkers: real gaps before rechecks, newest or oldest first.

Pure-function coverage of `_pending_days` -- no network, no object store, no lane-day lock. The
locked publish-and-verify machinery (`_publish_release_day_with_retries`,
`_publish_locked_release_day_with_retries`) is exercised end to end by
`test_burn_severity_direct_adapter.py` through `fill_one_lane_day` instead, matching
`pipeline/direct/drought/forward.py`'s own test split.
"""

from __future__ import annotations

from datetime import date

import pytest

from agri_data_service.pipeline.direct.burn_severity.forward import (
    BURN_SEVERITY_DIRECT_ALL_TIERS,
    DirectBurnSeverityError,
    _pending_days,
)
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER

DAY_A = date(2020, 11, 24)
DAY_B = date(2021, 9, 27)
DAY_C = date(2022, 4, 28)
DAYS = (DAY_A, DAY_B, DAY_C)
_DERIVED_TIER = next(tier for tier in BURN_SEVERITY_DIRECT_ALL_TIERS if tier != LANE_BASE_ZOOM_TIER)


def _statuses(overrides: dict[tuple[int, date], str] | None = None) -> dict[int, dict[date, str]]:
    base: dict[int, dict[date, str]] = {tier: {} for tier in BURN_SEVERITY_DIRECT_ALL_TIERS}
    for (tier, day), status in (overrides or {}).items():
        base[tier][day] = status
    return base


def _fully_written(days: tuple[date, ...]) -> dict[int, dict[date, str]]:
    return {tier: dict.fromkeys(days, "data") for tier in BURN_SEVERITY_DIRECT_ALL_TIERS}


def test_a_fully_written_set_owes_nothing() -> None:
    assert _pending_days(_fully_written(DAYS), DAYS, newest_first=True) == ()


def test_days_missing_from_the_census_are_pending_newest_first() -> None:
    """An empty census answers `missing` for every tier of every day (`.get(day, "missing")`)."""
    result = _pending_days(_statuses(), DAYS, newest_first=True)

    assert result == (DAY_C, DAY_B, DAY_A)


def test_the_same_gaps_are_pending_oldest_first_for_a_backfill_turn() -> None:
    result = _pending_days(_statuses(), DAYS, newest_first=False)

    assert result == (DAY_A, DAY_B, DAY_C)


def test_a_governed_absence_at_every_tier_is_a_recheck_never_a_permanent_skip() -> None:
    overrides = {(tier, DAY_A): "data" for tier in BURN_SEVERITY_DIRECT_ALL_TIERS}
    overrides.update({(tier, DAY_C): "data" for tier in BURN_SEVERITY_DIRECT_ALL_TIERS})
    overrides.update({(tier, DAY_B): "absent" for tier in BURN_SEVERITY_DIRECT_ALL_TIERS})

    result = _pending_days(_statuses(overrides), DAYS, newest_first=True)

    assert result == (DAY_B,), "a fully-absent day is a recheck candidate, not silently dropped"


def test_a_fully_written_day_is_excluded_even_when_a_sibling_day_is_absent() -> None:
    overrides = {(tier, DAY_A): "data" for tier in BURN_SEVERITY_DIRECT_ALL_TIERS}
    overrides.update({(tier, DAY_B): "absent" for tier in BURN_SEVERITY_DIRECT_ALL_TIERS})
    overrides.update({(tier, DAY_C): "data" for tier in BURN_SEVERITY_DIRECT_ALL_TIERS})

    result = _pending_days(_statuses(overrides), DAYS, newest_first=True)

    assert DAY_A not in result
    assert DAY_C not in result


def test_an_incomplete_derived_rung_is_pending_even_when_the_base_is_data() -> None:
    overrides = {(tier, DAY_A): "data" for tier in BURN_SEVERITY_DIRECT_ALL_TIERS}
    overrides[(_DERIVED_TIER, DAY_A)] = "incomplete"
    overrides.update({(tier, DAY_B): "data" for tier in BURN_SEVERITY_DIRECT_ALL_TIERS})
    overrides.update({(tier, DAY_C): "data" for tier in BURN_SEVERITY_DIRECT_ALL_TIERS})

    result = _pending_days(_statuses(overrides), (DAY_A, DAY_B, DAY_C), newest_first=True)

    assert result == (DAY_A,)


def test_an_absent_base_with_a_derived_data_rung_is_a_conflict_that_raises() -> None:
    """A base absence must never coexist with a derived rung that carries real parts."""
    overrides = {(LANE_BASE_ZOOM_TIER, DAY_A): "absent", (_DERIVED_TIER, DAY_A): "data"}

    with pytest.raises(DirectBurnSeverityError, match="carries derived parts"):
        _pending_days(_statuses(overrides), (DAY_A,), newest_first=True)


def test_a_conflict_status_anywhere_raises() -> None:
    overrides = {(_DERIVED_TIER, DAY_A): "conflict"}

    with pytest.raises(DirectBurnSeverityError, match="data/absence conflict"):
        _pending_days(_statuses(overrides), (DAY_A,), newest_first=True)


def test_an_empty_day_set_returns_no_pending_days() -> None:
    assert _pending_days(_statuses(), (), newest_first=True) == ()
