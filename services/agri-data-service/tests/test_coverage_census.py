"""The census must read the warehouse honestly and refuse a shape it cannot read.

No database. Every read is answered by a stubbed session that dispatches on the statement's line-1
marker, so the classification, the absence expansion and the through-day policy are all exercised
without one. The parts a stub cannot prove -- that the SQL parses, that the joins reach the rows --
belong to the PostgreSQL-backed suite, not here.

Both directions are covered: a lane that is whole, a lane with an interior hole, a hole an absence
explains, and a column the driver returned in the wrong type.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from agri_data_service.execution.coverage_census import (
    PUBLICATION_LAG_DAYS,
    UNMEASURED_PUBLICATION_LAG_DAYS,
    ContractCensus,
    CoverageCensusError,
    ThroughDayBasis,
    census_contracts,
    contracts_for_keys,
    default_through_day,
    load_lane_cells,
    union_missing_days,
)
from agri_data_service.execution.coverage_contract import (
    LANE_COVERAGE_CONTRACTS,
    Cadence,
    DayState,
    LaneCoverageContract,
)
from agri_data_service.execution.coverage_report import (
    contract_payload,
    coverage_status_payload,
    format_ranges,
    render_census,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

_GRID = "test-0.5-degree"
_SOURCE = "nasa-power-daily"
_SUPPORT = "surface"
_FIRST = date(2026, 1, 1)
_THROUGH = date(2026, 1, 10)
# The stub lattice and window, named so an assertion reads as a claim rather than as a literal.
_CELL_COUNT = 4
_WINDOW_DAYS = 10
_HOLE_DAYS = 3
_CONTRACT_PAIR = 2
_WIND_PRESENT_DAYS = 5

_CONTRACT = LaneCoverageContract(
    source_key=_SOURCE,
    signal_names=("precipitation", "wind_speed"),
    support_key=_SUPPORT,
    grid_name=_GRID,
    earliest_required_day=_FIRST,
    cadence=Cadence.DAILY,
)


class _FakeResult:
    """The narrow slice of SQLAlchemy's Result these readers use."""

    def __init__(self, rows: Sequence[Mapping[str, object]]) -> None:
        self._rows = list(rows)

    def mappings(self) -> _FakeResult:
        """Return self, because the fake already yields mappings."""
        return self

    def all(self) -> list[Mapping[str, object]]:
        return self._rows


class _MarkerSession:
    """An AsyncSession stand-in that answers each statement from its line-1 dispatch marker."""

    def __init__(self, scripted: Mapping[str, Sequence[Mapping[str, object]]]) -> None:
        self._scripted = scripted
        self.seen: list[tuple[str, Mapping[str, object]]] = []

    async def execute(self, statement: object, parameters: Mapping[str, object] | None = None) -> _FakeResult:
        marker = str(statement).splitlines()[0].removeprefix("--").strip()
        bound = set(getattr(statement, "_bindparams", {}) or {})
        supplied = set(parameters or {})
        if bound != supplied:
            raise AssertionError(f"{marker} binds {sorted(bound)} but was given {sorted(supplied)}")
        self.seen.append((marker, dict(parameters or {})))
        return _FakeResult(self._scripted.get(marker, ()))


def _cell_rows(count: int) -> list[Mapping[str, object]]:
    return [
        {
            "cell_id": uuid.UUID(int=index),
            "cell_key": f"cell-{index:03d}",
            "latitude": 44.0 + index / 100,
            "longitude": -116.0 + index / 100,
        }
        for index in range(count)
    ]


def _observed_rows(signal: str, days: Sequence[date], *, cells: int) -> list[Mapping[str, object]]:
    return [{"signal_name": signal, "observed_day": day, "cell_count": cells} for day in days]


def _every_day(first: date, last: date) -> list[date]:
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


def _session(
    *,
    observed: Sequence[Mapping[str, object]],
    absences: Sequence[Mapping[str, object]] = (),
    cells: int = 4,
) -> _MarkerSession:
    return _MarkerSession(
        {
            "coverage_observed_cell_days": observed,
            "coverage_absence_windows": absences,
            "coverage_grid_cells": _cell_rows(cells),
        }
    )


async def _census(session: _MarkerSession) -> ContractCensus:
    censuses = await census_contracts(cast("AsyncSession", session), (_CONTRACT,), through_day=_THROUGH)
    assert len(censuses) == 1
    return censuses[0]


@pytest.mark.asyncio
async def test_full_lane_reports_complete() -> None:
    """Every contracted day at the full lattice is complete, with no work list."""
    days = _every_day(_FIRST, _THROUGH)
    observed = _observed_rows("precipitation", days, cells=4) + _observed_rows("wind_speed", days, cells=4)
    census = await _census(_session(observed=observed))

    assert census.expected_cell_count == _CELL_COUNT
    assert census.is_complete
    assert census.missing_day_count == 0
    for signal in census.signals:
        assert signal.completeness_fraction == pytest.approx(1.0)
        assert signal.required_day_count == len(days)


@pytest.mark.asyncio
async def test_interior_hole_becomes_a_collapsed_range() -> None:
    """A missing run is reported as a run, not as scattered days -- that is what a filler acts on."""
    hole = {date(2026, 1, 4), date(2026, 1, 5), date(2026, 1, 6)}
    present = [day for day in _every_day(_FIRST, _THROUGH) if day not in hole]
    observed = _observed_rows("precipitation", present, cells=4) + _observed_rows(
        "wind_speed", _every_day(_FIRST, _THROUGH), cells=4
    )
    census = await _census(_session(observed=observed))

    precipitation = next(signal for signal in census.signals if signal.signal_name == "precipitation")
    assert precipitation.missing_days == (date(2026, 1, 4), date(2026, 1, 5), date(2026, 1, 6))
    assert format_ranges(precipitation.missing_days) == "2026-01-04..2026-01-06 (3 days)"
    assert precipitation.completeness_fraction == pytest.approx(7 / 10)
    assert census.missing_day_count == _HOLE_DAYS
    assert not census.is_complete


@pytest.mark.asyncio
async def test_a_recorded_absence_resolves_a_hole_and_is_never_a_gap() -> None:
    """An absence is evidence, so the lane reaches 100% with an empty work list, not 70%."""
    hole = {date(2026, 1, 4), date(2026, 1, 5), date(2026, 1, 6)}
    present = [day for day in _every_day(_FIRST, _THROUGH) if day not in hole]
    observed = _observed_rows("precipitation", present, cells=4) + _observed_rows(
        "wind_speed", _every_day(_FIRST, _THROUGH), cells=4
    )
    absences = [
        {
            "signal_name": "precipitation",
            "window_start_day": date(2026, 1, 4),
            "window_end_day": date(2026, 1, 6),
            "absent_cell_count": _CELL_COUNT,
        }
    ]
    census = await _census(_session(observed=observed, absences=absences))

    precipitation = next(signal for signal in census.signals if signal.signal_name == "precipitation")
    assert precipitation.missing_days == ()
    assert precipitation.absent_days == (date(2026, 1, 4), date(2026, 1, 5), date(2026, 1, 6))
    assert precipitation.completeness_fraction == pytest.approx(1.0)
    assert census.is_complete


@pytest.mark.asyncio
async def test_an_absence_over_one_cell_of_the_lattice_does_not_excuse_the_lane() -> None:
    """Measured 2026-08-11: 98 permanently out-of-domain ERA5-Land cells write four-year no_data spans.

    Reading "any cell said no_data" as "the lane is excused" retires 1,556 of that lane's 1,560
    contracted days while the provider is publishing 1,470 cells on every one of them.
    """
    hole = {date(2026, 1, 4), date(2026, 1, 5), date(2026, 1, 6)}
    present = [day for day in _every_day(_FIRST, _THROUGH) if day not in hole]
    observed = _observed_rows("precipitation", present, cells=4) + _observed_rows(
        "wind_speed", _every_day(_FIRST, _THROUGH), cells=4
    )
    absences = [
        {
            "signal_name": "precipitation",
            "window_start_day": date(2026, 1, 4),
            "window_end_day": date(2026, 1, 6),
            "absent_cell_count": 1,
        }
    ]
    census = await _census(_session(observed=observed, absences=absences))

    precipitation = next(signal for signal in census.signals if signal.signal_name == "precipitation")
    assert precipitation.absent_days == ()
    assert precipitation.missing_days == (date(2026, 1, 4), date(2026, 1, 5), date(2026, 1, 6))
    assert not census.is_complete


@pytest.mark.asyncio
async def test_an_absence_window_is_clipped_to_the_contracted_span() -> None:
    """A four-year audit window overlapping the contract explains only the days inside it."""
    observed = _observed_rows("wind_speed", _every_day(_FIRST, _THROUGH), cells=4)
    absences = [
        {
            "signal_name": "precipitation",
            "window_start_day": date(2020, 1, 1),
            "window_end_day": date(2030, 1, 1),
            "absent_cell_count": _CELL_COUNT,
        }
    ]
    census = await _census(_session(observed=observed, absences=absences))

    precipitation = next(signal for signal in census.signals if signal.signal_name == "precipitation")
    assert precipitation.absent_days == tuple(_every_day(_FIRST, _THROUGH))
    assert precipitation.missing_days == ()


@pytest.mark.asyncio
async def test_a_day_under_the_cell_floor_is_thin_and_not_missing() -> None:
    """Partial is not complete and is also not a hole; the two are reported apart."""
    days = _every_day(_FIRST, _THROUGH)
    observed = [
        *_observed_rows("precipitation", days[:-1], cells=4),
        {"signal_name": "precipitation", "observed_day": days[-1], "cell_count": 1},
        *_observed_rows("wind_speed", days, cells=4),
    ]
    census = await _census(_session(observed=observed))

    precipitation = next(signal for signal in census.signals if signal.signal_name == "precipitation")
    assert precipitation.thin_days == (_THROUGH,)
    assert precipitation.missing_days == ()
    assert not precipitation.is_complete
    # A thin day is NOT satisfied: it is a partial fill worth retrying, so it must not count.
    assert precipitation.completeness_fraction == pytest.approx(9 / 10)
    assert [entry.state for entry in precipitation.days][-1] is DayState.THIN


@pytest.mark.asyncio
async def test_one_read_serves_two_contracts_sharing_a_lane() -> None:
    """Two contracts on one source, grid and support must not issue the same aggregate read twice."""
    second = LaneCoverageContract(
        source_key=_SOURCE,
        signal_names=("soil_wetness_surface",),
        support_key=_SUPPORT,
        grid_name=_GRID,
        earliest_required_day=_FIRST,
        cadence=Cadence.DAILY,
    )
    session = _session(observed=_observed_rows("precipitation", _every_day(_FIRST, _THROUGH), cells=4))
    censuses = await census_contracts(cast("AsyncSession", session), (_CONTRACT, second), through_day=_THROUGH)

    assert len(censuses) == _CONTRACT_PAIR
    markers = [marker for marker, _ in session.seen]
    assert markers.count("coverage_observed_cell_days") == 1
    assert markers.count("coverage_absence_windows") == 1
    assert markers.count("coverage_grid_cells") == 1


@pytest.mark.asyncio
async def test_the_read_window_is_half_open_on_the_day_after_the_last() -> None:
    """The exclusive bound must be the day AFTER `through_day`, or the last day is never read."""
    session = _session(observed=())
    await _census(session)

    _, parameters = next(entry for entry in session.seen if entry[0] == "coverage_observed_cell_days")
    assert parameters["from_day"] == datetime(2026, 1, 1, tzinfo=UTC)
    assert parameters["through_exclusive"] == datetime(2026, 1, 11, tzinfo=UTC)


@pytest.mark.asyncio
async def test_a_wrongly_typed_column_is_refused_not_coerced() -> None:
    """A driver returning text where a count belongs must fail loudly, never read as zero."""
    session = _session(observed=[{"signal_name": "precipitation", "observed_day": _FIRST, "cell_count": "four"}])
    with pytest.raises(CoverageCensusError, match="cell_count must be an integer"):
        await _census(session)


@pytest.mark.asyncio
async def test_a_timestamp_in_a_day_column_is_reduced_not_refused() -> None:
    """`datetime` subclasses `date`, so the census reduces it rather than comparing it unequal forever."""
    days = _every_day(_FIRST, _THROUGH)
    observed = [
        *(
            {
                "signal_name": "precipitation",
                "observed_day": datetime(day.year, day.month, day.day, tzinfo=UTC),
                "cell_count": 4,
            }
            for day in days
        ),
        *_observed_rows("wind_speed", days, cells=4),
    ]
    census = await _census(_session(observed=observed))

    assert census.is_complete


@pytest.mark.asyncio
async def test_lane_cells_carry_their_identity_and_coordinates() -> None:
    """The absence writer needs the cell id and the probe needs the centroid; both come from here."""
    session = _session(observed=(), cells=3)
    cells = await load_lane_cells(cast("AsyncSession", session), _GRID)

    assert [cell.cell_key for cell in cells] == ["cell-000", "cell-001", "cell-002"]
    assert cells[0].cell_id == uuid.UUID(int=0)
    assert cells[1].latitude == pytest.approx(44.01)


def test_through_day_defaults_to_today_minus_the_measured_lag() -> None:
    """Running a lane to today would report the provider's release schedule as a hole."""
    today = date(2026, 8, 11)
    assert default_through_day("nasa-power-daily", today=today) == date(2026, 8, 6)
    assert default_through_day("open-meteo-era5-land-archive", today=today) == date(2026, 8, 2)
    assert default_through_day("some-unmeasured-lane", today=today) == today - timedelta(
        days=UNMEASURED_PUBLICATION_LAG_DAYS
    )


def test_every_declared_contract_has_a_measured_publication_lag() -> None:
    """A lane falling back to the generous default is a lane nobody measured; name them here."""
    unmeasured = sorted({contract.source_key for contract in LANE_COVERAGE_CONTRACTS} - set(PUBLICATION_LAG_DAYS))
    assert unmeasured == []
    # Guard the guard: the assertion above passes vacuously for any key no contract declares, so a
    # lag entry that nothing holds to could carry an unmeasured number forever. Only lanes actually
    # under contract may declare one.
    contracted = {contract.source_key for contract in LANE_COVERAGE_CONTRACTS}
    assert sorted(set(PUBLICATION_LAG_DAYS) - contracted) == []


def test_a_lane_with_no_measured_lag_falls_back_rather_than_borrowing_a_sibling_s() -> None:
    """`open-meteo-era5-archive` is a different product on its own cadence and nobody measured it."""
    assert "open-meteo-era5-archive" not in PUBLICATION_LAG_DAYS
    today = date(2026, 8, 11)
    assert default_through_day("open-meteo-era5-archive", today=today) == today - timedelta(
        days=UNMEASURED_PUBLICATION_LAG_DAYS
    )


def test_an_undeclared_source_key_is_a_fault_not_an_empty_run() -> None:
    """A typo that silently reports nothing is exactly how a dead lane looks healthy."""
    with pytest.raises(CoverageCensusError, match="no coverage contract declares source key"):
        contracts_for_keys(("open-meteo-era5-land",))
    assert contracts_for_keys(()) == LANE_COVERAGE_CONTRACTS


@pytest.mark.asyncio
async def test_the_report_states_completeness_the_missing_count_and_the_ranges() -> None:
    """The three facts the layer lane standard requires must all be present in the human output."""
    hole = {date(2026, 1, 4), date(2026, 1, 5)}
    present = [day for day in _every_day(_FIRST, _THROUGH) if day not in hole]
    observed = _observed_rows("precipitation", present, cells=4) + _observed_rows(
        "wind_speed", _every_day(_FIRST, _THROUGH), cells=4
    )
    census = await _census(_session(observed=observed))
    rendered = render_census((census,))

    assert "nasa-power-daily" in rendered
    assert f"{_GRID} (4 cells)" in rendered
    assert "80.0%" in rendered
    assert "missing: 2026-01-04..2026-01-05 (2 days)" in rendered
    assert "VERDICT: incomplete -- 2 signal-days missing" in rendered
    assert "Run coverage-fill" in rendered
    assert str(ThroughDayBasis.OPERATOR) in rendered


@pytest.mark.asyncio
async def test_a_thin_only_lane_is_not_sent_to_a_verb_that_cannot_close_a_thin_day() -> None:
    """`coverage-fill` reads missing_days only, so pointing a thin-only lane at it is a green dead end."""
    days = _every_day(_FIRST, _THROUGH)
    observed = _observed_rows("precipitation", days, cells=1) + _observed_rows("wind_speed", days, cells=4)
    census = await _census(_session(observed=observed))
    rendered = render_census((census,))

    assert census.missing_day_count == 0
    assert not census.is_complete
    assert "VERDICT: incomplete -- 0 signal-days missing, 10 thin." in rendered
    assert "coverage-fill cannot close a thin day" in rendered
    assert "Run coverage-fill" not in rendered


@pytest.mark.asyncio
async def test_the_json_payload_carries_the_denominators_behind_every_fraction() -> None:
    """A reader must be able to re-derive completeness rather than trust the number."""
    observed = _observed_rows("precipitation", _every_day(_FIRST, _THROUGH), cells=4) + _observed_rows(
        "wind_speed", _every_day(_FIRST, _THROUGH)[:5], cells=4
    )
    census = await _census(_session(observed=observed))
    payload = contract_payload(census)

    assert payload["expected_cell_count"] == _CELL_COUNT
    assert payload["through_day"] == "2026-01-10"
    assert payload["missing_day_count"] == _WINDOW_DAYS - _WIND_PRESENT_DAYS
    signals = payload["signals"]
    assert isinstance(signals, list)
    wind = next(entry for entry in signals if entry["signal_name"] == "wind_speed")
    assert wind["required_day_count"] == _WINDOW_DAYS
    assert wind["missing_ranges"] == [{"start_day": "2026-01-06", "end_day": "2026-01-10", "day_count": 5}]
    whole = coverage_status_payload((census,))
    assert whole["contract_count"] == 1
    assert whole["is_complete"] is False


@pytest.mark.asyncio
async def test_missing_days_union_across_signals_for_one_fetch() -> None:
    """One upstream request returns every parameter, so the work list is the union, not the sum."""
    observed = _observed_rows("precipitation", _every_day(_FIRST, _THROUGH)[:5], cells=4) + _observed_rows(
        "wind_speed", _every_day(_FIRST, _THROUGH)[:7], cells=4
    )
    census = await _census(_session(observed=observed))

    assert union_missing_days(census.signals) == tuple(_every_day(date(2026, 1, 6), _THROUGH))


def test_format_ranges_reads_as_an_operator_expects() -> None:
    """A single day says one day; an empty list says none; a long list is summarised, not dumped."""
    assert format_ranges(()) == "none"
    assert format_ranges((date(2026, 1, 1),)) == "2026-01-01 (1 day)"
    scattered = [date(2026, 1, 1) + timedelta(days=offset * 2) for offset in range(15)]
    rendered = format_ranges(scattered, limit=3)
    assert rendered.endswith("and 12 more range(s)")
