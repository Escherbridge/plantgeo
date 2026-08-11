"""Pin the gap arithmetic. See `src/agri_data_service/execution/AGENTS.md` §coverage-contract."""

# ruff: noqa: PLR2004

from __future__ import annotations

from datetime import date, timedelta

import pytest

from agri_data_service.execution.coverage_contract import (
    LANE_COVERAGE_CONTRACTS,
    Cadence,
    DayState,
    LaneCoverageContract,
    SignalReconciliation,
    contiguous_ranges,
    contracts_for_source,
    reconcile_signal,
    required_days,
)

MONDAY = 0
TUESDAY = 1

# 2024-03-14 is a Thursday, so a weekly contract opening on it opens MID-WEEK: its first release cannot be
# its own start day, and the four days before that release are out of contract rather than missing.
MIDWEEK_OPEN_DAY = date(2024, 3, 14)
FIRST_TUESDAY_AFTER_MIDWEEK_OPEN = date(2024, 3, 19)

# A four-cell lattice keeps the floor small enough to read: at fraction 1.0 the floor IS this number, so
# "one short" and "empty" are one row apart instead of buried in 397.
SMALL_LATTICE_CELL_COUNT = 4

# The measured production lattice, 2026-08-11: `agri.spatial_cell.grid_name = 'nasa-power-0.5-degree'`.
NASA_POWER_LATTICE_CELL_COUNT = 397

# Hand-spelled from production 2026-08-11 -- deliberately NOT imported from any shared constant, because a
# contract and its checker reading the same symbol lets both drift together and still pass. `source_key`
# drifting to a value matching no row is the exact incident that made every reconciliation return empty.
REAL_DATA_SOURCE_KEYS = frozenset(
    {
        "nasa-power-daily",
        "open-meteo-era5-archive",
        "open-meteo-era5-land-archive",
        "sentinel2-ndvi-l2a",
    }
)
REAL_GRID_NAMES = frozenset({"sentinel2-ndvi-0p25deg", "nasa-power-0.5-degree"})

# NASA POWER requests eight parameters and `WD2M` is not among them (verified 2026-08-11), so wind is a
# scalar. Contracting a signal no producer writes would report every day of its horizon permanently missing
# and send a filler after data that has never existed.
UNINGESTED_SIGNAL_NAMES = frozenset({"wind_direction", "wind_bearing"})


def _daily_contract(*, earliest: date, minimum_cell_fraction: float = 1.0) -> LaneCoverageContract:
    """A one-signal daily lane on the real NASA POWER grid, so only the horizon and floor vary per test."""
    return LaneCoverageContract(
        source_key="nasa-power-daily",
        signal_names=("air_temperature_mean",),
        support_key="surface",
        grid_name="nasa-power-0.5-degree",
        earliest_required_day=earliest,
        cadence=Cadence.DAILY,
        minimum_cell_fraction=minimum_cell_fraction,
    )


def _weekly_contract(*, earliest: date, anchor_weekday: int = TUESDAY) -> LaneCoverageContract:
    """A weekly lane; USDM's Tuesday release is the default anchor."""
    return LaneCoverageContract(
        source_key="nasa-power-daily",
        signal_names=("drought_category",),
        support_key="surface",
        grid_name="nasa-power-0.5-degree",
        earliest_required_day=earliest,
        cadence=Cadence.WEEKLY,
        weekly_anchor_weekday=anchor_weekday,
    )


def _reconcile(
    contract: LaneCoverageContract,
    *,
    through_day: date,
    observed: dict[date, int],
    absent: tuple[date, ...] = (),
    expected_cell_count: int = SMALL_LATTICE_CELL_COUNT,
) -> SignalReconciliation:
    return reconcile_signal(
        contract,
        "air_temperature_mean",
        through_day,
        observed,
        absent,
        expected_cell_count,
    )


def _state_on(reconciliation: SignalReconciliation, day: date) -> DayState:
    return next(entry.state for entry in reconciliation.days if entry.day == day)


def test_weekly_required_days_land_on_the_anchor_weekday_when_the_contract_opens_midweek() -> None:
    contract = _weekly_contract(earliest=MIDWEEK_OPEN_DAY)

    days = required_days(contract, date(2024, 4, 9))

    assert days == (
        FIRST_TUESDAY_AFTER_MIDWEEK_OPEN,
        date(2024, 3, 26),
        date(2024, 4, 2),
        date(2024, 4, 9),
    )
    assert all(day.weekday() == TUESDAY for day in days)
    # The five days between the contract opening and its first release are out of contract, not missing.
    assert MIDWEEK_OPEN_DAY not in days


def test_weekly_anchor_is_configurable_and_keeps_an_open_day_already_on_the_anchor() -> None:
    monday_lane = _weekly_contract(earliest=MIDWEEK_OPEN_DAY, anchor_weekday=MONDAY)
    opens_on_anchor = _weekly_contract(earliest=FIRST_TUESDAY_AFTER_MIDWEEK_OPEN)

    monday_days = required_days(monday_lane, date(2024, 4, 1))
    anchored_days = required_days(opens_on_anchor, date(2024, 3, 26))

    assert monday_days == (date(2024, 3, 18), date(2024, 3, 25), date(2024, 4, 1))
    # Offset zero: a contract whose start IS the anchor owes its own opening day, it is not skipped a week.
    assert anchored_days == (FIRST_TUESDAY_AFTER_MIDWEEK_OPEN, date(2024, 3, 26))


def test_daily_required_days_span_the_window_inclusive_at_both_ends() -> None:
    contract = _daily_contract(earliest=date(2024, 3, 11))

    assert required_days(contract, date(2024, 3, 15)) == (
        date(2024, 3, 11),
        date(2024, 3, 12),
        date(2024, 3, 13),
        date(2024, 3, 14),
        date(2024, 3, 15),
    )
    assert required_days(contract, date(2024, 3, 11)) == (date(2024, 3, 11),)


@pytest.mark.parametrize("cadence", [Cadence.DAILY, Cadence.WEEKLY])
def test_a_through_day_before_the_horizon_owes_nothing_rather_than_a_negative_span(cadence: Cadence) -> None:
    earliest = date(2024, 3, 11)
    contract = _daily_contract(earliest=earliest) if cadence is Cadence.DAILY else _weekly_contract(earliest=earliest)

    assert required_days(contract, earliest - timedelta(days=1)) == ()
    assert required_days(contract, date(2019, 1, 1)) == ()


def test_reconciling_before_the_horizon_reports_zero_required_days_not_a_finished_lane() -> None:
    """A lane whose history has not opened is EMPTY, and `required_day_count` is the only honest tell."""
    contract = _daily_contract(earliest=date(2024, 3, 11))

    reconciliation = _reconcile(contract, through_day=date(2024, 3, 10), observed={})

    assert reconciliation.days == ()
    assert reconciliation.required_day_count == 0
    assert reconciliation.missing_days == ()
    # `is_complete` and `completeness_fraction` read 100% over nothing, by design -- so a caller that reports
    # completeness without also reporting the day count is claiming success having measured nothing.
    assert reconciliation.is_complete is True
    assert reconciliation.completeness_fraction == 1.0


def test_a_day_both_observed_and_marked_no_data_resolves_to_covered_exactly_once() -> None:
    contract = _daily_contract(earliest=date(2024, 3, 11))
    contested_day = date(2024, 3, 12)

    reconciliation = _reconcile(
        contract,
        through_day=date(2024, 3, 12),
        observed={date(2024, 3, 11): SMALL_LATTICE_CELL_COUNT, contested_day: SMALL_LATTICE_CELL_COUNT},
        absent=(contested_day,),
    )

    # An absence is a statement about a fetch; a later fetch that landed rows supersedes it.
    assert _state_on(reconciliation, contested_day) is DayState.COVERED
    assert reconciliation.absent_days == ()
    assert reconciliation.missing_days == ()
    assert reconciliation.thin_days == ()
    # Counted once, not once as observed and again as an absence.
    assert reconciliation.required_day_count == 2
    assert len(reconciliation.days) == len({entry.day for entry in reconciliation.days})


def test_is_complete_is_true_with_governed_absences_present() -> None:
    contract = _daily_contract(earliest=date(2024, 3, 11))
    unpublished_day = date(2024, 3, 12)

    reconciliation = _reconcile(
        contract,
        through_day=date(2024, 3, 13),
        observed={date(2024, 3, 11): SMALL_LATTICE_CELL_COUNT, date(2024, 3, 13): SMALL_LATTICE_CELL_COUNT},
        absent=(unpublished_day,),
    )

    # Without this, a lane whose provider never published one day sits at 99% forever with a work list that
    # can never empty.
    assert reconciliation.absent_days == (unpublished_day,)
    assert reconciliation.missing_days == ()
    assert reconciliation.is_complete is True
    assert reconciliation.completeness_fraction == 1.0


def test_is_complete_is_false_while_thin_days_remain() -> None:
    contract = _daily_contract(earliest=date(2024, 3, 11))
    thin_day = date(2024, 3, 12)

    reconciliation = _reconcile(
        contract,
        through_day=date(2024, 3, 13),
        observed={
            date(2024, 3, 11): SMALL_LATTICE_CELL_COUNT,
            thin_day: 1,
            date(2024, 3, 13): SMALL_LATTICE_CELL_COUNT,
        },
        absent=(),
    )

    # A partial fill is worth retrying, so it is not satisfied -- `partial` is never `complete`.
    assert reconciliation.thin_days == (thin_day,)
    assert reconciliation.missing_days == ()
    assert reconciliation.is_complete is False
    assert reconciliation.completeness_fraction == pytest.approx(2 / 3)


def test_a_missing_day_alone_also_defeats_is_complete() -> None:
    contract = _daily_contract(earliest=date(2024, 3, 11))

    reconciliation = _reconcile(
        contract,
        through_day=date(2024, 3, 12),
        observed={date(2024, 3, 11): SMALL_LATTICE_CELL_COUNT},
    )

    assert reconciliation.missing_days == (date(2024, 3, 12),)
    assert reconciliation.is_complete is False
    assert reconciliation.completeness_fraction == pytest.approx(0.5)


def test_a_sub_floor_day_marked_no_data_is_thin_and_the_absence_does_not_close_it() -> None:
    """Precedence pin: floor beats thin beats absence. Rows on the ground outrank a `no_data` verdict."""
    contract = _daily_contract(earliest=date(2024, 3, 11))
    contested_day = date(2024, 3, 11)

    reconciliation = _reconcile(
        contract,
        through_day=contested_day,
        observed={contested_day: 1},
        absent=(contested_day,),
    )

    assert _state_on(reconciliation, contested_day) is DayState.THIN
    assert reconciliation.absent_days == ()
    assert reconciliation.is_complete is False


def test_a_cell_fraction_that_rounds_to_zero_still_floors_at_one_cell() -> None:
    # round(397 * 0.001) == 0; without the floor an empty day would clear a zero threshold and every hole in
    # the lane would report covered.
    contract = _daily_contract(earliest=date(2024, 3, 11), minimum_cell_fraction=0.001)

    reconciliation = _reconcile(
        contract,
        through_day=date(2024, 3, 12),
        observed={date(2024, 3, 12): 1},
        expected_cell_count=NASA_POWER_LATTICE_CELL_COUNT,
    )

    assert _state_on(reconciliation, date(2024, 3, 11)) is DayState.MISSING
    assert _state_on(reconciliation, date(2024, 3, 12)) is DayState.COVERED
    assert reconciliation.is_complete is False


def test_a_whole_lattice_contract_reads_one_cell_short_as_thin_not_covered() -> None:
    contract = _daily_contract(earliest=date(2024, 3, 11))

    reconciliation = _reconcile(
        contract,
        through_day=date(2024, 3, 11),
        observed={date(2024, 3, 11): SMALL_LATTICE_CELL_COUNT - 1},
    )

    entry = reconciliation.days[0]
    assert entry.state is DayState.THIN
    assert entry.observed_cell_count == SMALL_LATTICE_CELL_COUNT - 1
    assert entry.expected_cell_count == SMALL_LATTICE_CELL_COUNT


def test_a_reconciled_window_hands_its_holes_straight_to_the_range_collapser() -> None:
    """The module's headline promise, end to end: 'there are N missing days' becomes the runs to fetch."""
    contract = _daily_contract(earliest=date(2024, 3, 1))
    present = (
        date(2024, 3, 4),
        date(2024, 3, 5),
        date(2024, 3, 6),
        date(2024, 3, 8),
        date(2024, 3, 9),
    )

    reconciliation = _reconcile(
        contract,
        through_day=date(2024, 3, 10),
        observed=dict.fromkeys(present, SMALL_LATTICE_CELL_COUNT),
    )

    assert reconciliation.missing_days == (
        date(2024, 3, 1),
        date(2024, 3, 2),
        date(2024, 3, 3),
        date(2024, 3, 7),
        date(2024, 3, 10),
    )
    # Five holes, three requests. Composing the two by hand at every call site is how a filler ends
    # up paying per day, so the seam between them is pinned here rather than assumed.
    assert contiguous_ranges(reconciliation.missing_days) == (
        (date(2024, 3, 1), date(2024, 3, 3)),
        (date(2024, 3, 7), date(2024, 3, 7)),
        (date(2024, 3, 10), date(2024, 3, 10)),
    )


def test_a_weekly_lane_classifies_only_its_release_days_and_ignores_the_rest() -> None:
    """A weekly contract must not report the six non-release days of each week as holes."""
    contract = _weekly_contract(earliest=FIRST_TUESDAY_AFTER_MIDWEEK_OPEN)
    missed_release = date(2024, 3, 26)

    reconciliation = reconcile_signal(
        contract,
        "drought_category",
        date(2024, 4, 2),
        {
            FIRST_TUESDAY_AFTER_MIDWEEK_OPEN: SMALL_LATTICE_CELL_COUNT,
            # A Wednesday. Not a release day, so it is neither coverage nor a hole.
            date(2024, 3, 27): SMALL_LATTICE_CELL_COUNT,
            date(2024, 4, 2): SMALL_LATTICE_CELL_COUNT,
        },
        (),
        SMALL_LATTICE_CELL_COUNT,
    )

    assert reconciliation.required_day_count == 3
    assert reconciliation.missing_days == (missed_release,)
    assert contiguous_ranges(reconciliation.missing_days) == ((missed_release, missed_release),)
    assert reconciliation.completeness_fraction == pytest.approx(2 / 3)


def test_contiguous_ranges_collapses_scattered_days_into_runs() -> None:
    scattered = [
        date(2024, 3, 1),
        date(2024, 3, 2),
        date(2024, 3, 3),
        date(2024, 3, 7),
        date(2024, 3, 10),
        date(2024, 3, 11),
    ]

    # Six holes become three windows: a filler pays per request, not per day.
    assert contiguous_ranges(scattered) == (
        (date(2024, 3, 1), date(2024, 3, 3)),
        (date(2024, 3, 7), date(2024, 3, 7)),
        (date(2024, 3, 10), date(2024, 3, 11)),
    )


def test_contiguous_ranges_renders_a_lone_day_as_an_inclusive_single_day_run() -> None:
    assert contiguous_ranges([date(2024, 3, 14)]) == ((date(2024, 3, 14), date(2024, 3, 14)),)


def test_contiguous_ranges_of_no_days_is_no_runs() -> None:
    assert contiguous_ranges([]) == ()
    assert contiguous_ranges(()) == ()


def test_contiguous_ranges_orders_and_dedupes_before_collapsing() -> None:
    jumbled = [date(2024, 3, 3), date(2024, 3, 1), date(2024, 3, 3), date(2024, 3, 2)]

    assert contiguous_ranges(jumbled) == ((date(2024, 3, 1), date(2024, 3, 3)),)


def test_contiguous_ranges_joins_a_run_across_a_leap_day_month_boundary() -> None:
    # Month arithmetic done on day numbers instead of `timedelta` splits this into two windows.
    across_leap_day = [date(2024, 2, 28), date(2024, 2, 29), date(2024, 3, 1)]

    assert contiguous_ranges(across_leap_day) == ((date(2024, 2, 28), date(2024, 3, 1)),)


def test_declared_contracts_name_only_sources_and_grids_that_exist() -> None:
    assert LANE_COVERAGE_CONTRACTS
    for contract in LANE_COVERAGE_CONTRACTS:
        assert contract.source_key in REAL_DATA_SOURCE_KEYS
        assert contract.grid_name in REAL_GRID_NAMES
        assert contract.signal_names
        assert 0.0 < contract.minimum_cell_fraction <= 1.0


def test_no_contract_promises_a_signal_no_producer_writes() -> None:
    contracted = {name for contract in LANE_COVERAGE_CONTRACTS for name in contract.signal_names}

    assert "wind_speed" in contracted
    assert not contracted & UNINGESTED_SIGNAL_NAMES


def test_a_signal_is_contracted_under_exactly_one_horizon_per_source() -> None:
    pairs = [(contract.source_key, name) for contract in LANE_COVERAGE_CONTRACTS for name in contract.signal_names]

    # Two horizons over one signal would reconcile the same days twice and disagree about the older one.
    assert len(pairs) == len(set(pairs))


def test_declared_horizons_are_the_claims_not_whatever_happens_to_be_loaded() -> None:
    # Hand-spelled so a horizon can never be edited without this test being edited too. What
    # production actually held, measured 2026-08-11: the eight NASA POWER surface signals carry the
    # full 397-cell lattice from 2022-04-30, while soil wetness carries 4 cells for 98 days
    # (2022-04-30..2022-08-05) and widens to 397 on 2022-08-06. ERA5-Land opens 2022-04-30 over
    # 1,470 cells. The surface horizon below is therefore declared 98 days NARROWER than the data
    # the warehouse demonstrably holds -- a deliberately conservative claim, not a measurement of
    # it. Raising it is an owner call; see AGENTS.md §coverage-contract, "the surface horizon is a
    # claim, not the measurement".
    horizons = {
        (contract.source_key, contract.signal_names[0]): contract.earliest_required_day
        for contract in LANE_COVERAGE_CONTRACTS
    }

    assert horizons[("nasa-power-daily", "air_temperature_mean")] == date(2022, 8, 6)
    assert horizons[("nasa-power-daily", "soil_wetness_surface")] == date(2022, 8, 6)
    assert horizons[("open-meteo-era5-land-archive", "soil_water_content_layer_1")] == date(2022, 4, 30)


def test_contracts_for_source_groups_a_pilot_beside_a_field() -> None:
    nasa = contracts_for_source("nasa-power-daily")

    assert len(nasa) == 2
    assert all(contract.source_key == "nasa-power-daily" for contract in nasa)
    assert {contract.signal_names[0] for contract in nasa} == {
        "air_temperature_mean",
        "soil_wetness_surface",
    }


def test_contracts_for_source_cannot_tell_an_uncontracted_lane_from_a_typo() -> None:
    """Both answer `()`. Named so nobody mistakes this function for the guard against drift."""
    # A real lane with no contract yet. Empty is the honest answer.
    assert contracts_for_source("sentinel2-ndvi-l2a") == ()
    # A typo of a lane that IS contracted. Also empty -- indistinguishable here, which is exactly
    # the incident recorded at the top of LANE_COVERAGE_CONTRACTS. `contracts_for_keys` in
    # coverage_census.py is what raises on an unknown key; every caller that can afford to refuse
    # must go through that instead of this.
    assert contracts_for_source("open-meteo-era5-land") == ()
