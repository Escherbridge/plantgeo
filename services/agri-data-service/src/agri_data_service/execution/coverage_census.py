"""Measure one lane's contracted window against the warehouse, so a hole becomes a list of days.

`coverage_contract.py` decides what a day's state IS; this module is the only thing that asks the
database what the warehouse actually holds. Keeping the two apart is what lets the whole
classification be unit-tested without a database: `reconcile_signal` takes the three measurements
below as plain Python and never sees a session.

Three measurements, one per plane the answer depends on:

  * observed cells per day -- `agri.signal_observation`, gated through the release to the lane.
  * governed absences      -- `agri.signal_coverage_audit`, `status='no_data'` only, and only where
                              the excused cells reach the same floor an observation must clear.
  * expected cell count    -- `agri.spatial_cell`, counted per request, never frozen.

See `src/agri_data_service/execution/AGENTS.md`, the coverage-status/coverage-fill section.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final
from uuid import UUID

from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.execution.coverage_contract import (
    LANE_COVERAGE_CONTRACTS,
    LaneCoverageContract,
    SignalReconciliation,
    covered_cell_floor,
    reconcile_signal,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

_OBSERVED_CELL_DAYS_SQL: Final = text(load_query_sql("execution/coverage_observed_cell_days.sql"))
_ABSENCE_WINDOWS_SQL: Final = text(load_query_sql("execution/coverage_absence_windows.sql"))
_GRID_CELLS_SQL: Final = text(load_query_sql("execution/coverage_grid_cells.sql"))

# How far behind today each provider's live edge sits, measured against production 2026-08-11: NASA
# POWER's newest day was 2026-08-06 (5 days) and Open-Meteo's ERA5-Land archive's was 2026-08-02
# (9 days). A census run to today's date would report every one of those trailing days as a hole and
# send a gap filler after days the provider has not published yet -- which is the forward refresh's
# job and not a gap at all. Declared here rather than in `LaneCoverageContract` because it is a
# property of the provider's release schedule, not of what this lane promises to hold.
#
# Only lanes whose live edge was actually measured appear. `open-meteo-era5-archive` is a different
# reanalysis product on its own release cadence and nobody measured it, so it deliberately falls
# through to the generous fallback rather than borrowing ERA5-Land's number.
PUBLICATION_LAG_DAYS: Final[Mapping[str, int]] = {
    "nasa-power-daily": 5,
    "open-meteo-era5-land-archive": 9,
}

# The lag assumed for a lane with no measurement above. Deliberately generous: over-reporting a
# lane as complete for a fortnight is recoverable on the next run, while under-reporting sends a
# quota-bound fetch after days that do not exist yet. A lane that lands here should be measured and
# added, not left to the fallback.
UNMEASURED_PUBLICATION_LAG_DAYS: Final = 14


class ThroughDayBasis(StrEnum):
    """How the census's last contracted day was chosen. Printed, because it changes every number."""

    OPERATOR = "operator_declared"
    """An explicit `--through`. The operator owns whether the provider has actually reached it."""

    PUBLICATION_LAG = "measured_publication_lag"
    """Today minus this provider's measured lag. The default, and the only safe automated choice."""


class CoverageCensusError(RuntimeError):
    """The warehouse answered in a shape the census cannot read."""


@dataclass(frozen=True, slots=True)
class LaneCell:
    """One lattice cell: what the audit table points at, and where the provider is asked."""

    cell_id: UUID
    cell_key: str
    latitude: float
    longitude: float


@dataclass(frozen=True, slots=True)
class ContractCensus:
    """One contract's whole reconciled window, with the denominators that produced it."""

    contract: LaneCoverageContract
    through_day: date
    through_day_basis: ThroughDayBasis
    expected_cell_count: int
    signals: tuple[SignalReconciliation, ...]

    @property
    def missing_day_count(self) -> int:
        """The lane's total work list size, summed over signals. Days repeat across signals."""
        return sum(len(signal.missing_days) for signal in self.signals)

    @property
    def is_complete(self) -> bool:
        return all(signal.is_complete for signal in self.signals)


def default_through_day(source_key: str, *, today: date) -> date:
    """The newest day it is honest to hold this lane to, given its provider's measured lag."""
    lag = PUBLICATION_LAG_DAYS.get(source_key, UNMEASURED_PUBLICATION_LAG_DAYS)
    return today - timedelta(days=lag)


def contracts_for_keys(source_keys: Sequence[str]) -> tuple[LaneCoverageContract, ...]:
    """Every declared contract, or just the named lanes. An unknown key is a fault, not an empty run."""
    if not source_keys:
        return LANE_COVERAGE_CONTRACTS
    declared = {contract.source_key for contract in LANE_COVERAGE_CONTRACTS}
    unknown = sorted(set(source_keys) - declared)
    if unknown:
        raise CoverageCensusError(
            f"no coverage contract declares source key(s) {', '.join(unknown)}; declared: {', '.join(sorted(declared))}"
        )
    wanted = set(source_keys)
    return tuple(contract for contract in LANE_COVERAGE_CONTRACTS if contract.source_key in wanted)


async def load_lane_cells(session: AsyncSession, grid_name: str) -> tuple[LaneCell, ...]:
    """Read one lattice, sorted by cell key, so a probe sample is reproducible between runs."""
    result = await session.execute(_GRID_CELLS_SQL, {"grid_name": grid_name})
    cells: list[LaneCell] = []
    for row in result.mappings().all():
        cell_id: object = row["cell_id"]
        cell_key: object = row["cell_key"]
        latitude: object = row["latitude"]
        longitude: object = row["longitude"]
        cells.append(
            LaneCell(
                cell_id=_require_uuid(cell_id, "cell_id"),
                cell_key=_require_str(cell_key, "cell_key"),
                latitude=_require_float(latitude, "latitude"),
                longitude=_require_float(longitude, "longitude"),
            )
        )
    return tuple(cells)


async def census_contracts(
    session: AsyncSession,
    contracts: Sequence[LaneCoverageContract],
    *,
    through_day: date | None = None,
    today: date | None = None,
) -> tuple[ContractCensus, ...]:
    """Reconcile every contract against the warehouse, reusing one read per identical lane window.

    Two contracts can share a source, a grid and a support -- NASA POWER's meteorology set and its
    soil-wetness set do -- and would otherwise issue the same two aggregate reads twice. The cache
    is keyed on exactly the parameters the reads take, so it can never serve one lane's rows to
    another.
    """
    resolved_today = today or datetime.now(UTC).date()
    observed_cache: dict[tuple[str, str, str, date, date], Mapping[str, dict[date, int]]] = {}
    absence_cache: dict[tuple[str, str, date, date], Mapping[str, Mapping[date, int]]] = {}
    cell_count_cache: dict[str, int] = {}

    censuses: list[ContractCensus] = []
    for contract in contracts:
        basis = ThroughDayBasis.OPERATOR if through_day is not None else ThroughDayBasis.PUBLICATION_LAG
        last_day = (
            through_day if through_day is not None else default_through_day(contract.source_key, today=resolved_today)
        )
        window = (contract.earliest_required_day, last_day)

        observed_key = (contract.source_key, contract.grid_name, contract.support_key, *window)
        if observed_key not in observed_cache:
            observed_cache[observed_key] = await _observed_cell_days(
                session,
                source_key=contract.source_key,
                grid_name=contract.grid_name,
                support_key=contract.support_key,
                from_day=window[0],
                through_day=window[1],
            )
        absence_key = (contract.source_key, contract.support_key, *window)
        if absence_key not in absence_cache:
            absence_cache[absence_key] = await _absent_days(
                session,
                source_key=contract.source_key,
                support_key=contract.support_key,
                from_day=window[0],
                through_day=window[1],
            )
        if contract.grid_name not in cell_count_cache:
            cell_count_cache[contract.grid_name] = len(await load_lane_cells(session, contract.grid_name))

        observed = observed_cache[observed_key]
        absent = absence_cache[absence_key]
        expected_cells = cell_count_cache[contract.grid_name]
        floor = covered_cell_floor(contract, expected_cells)
        censuses.append(
            ContractCensus(
                contract=contract,
                through_day=last_day,
                through_day_basis=basis,
                expected_cell_count=expected_cells,
                signals=tuple(
                    reconcile_signal(
                        contract,
                        signal_name,
                        last_day,
                        dict(observed.get(signal_name, {})),
                        lattice_wide_absences(absent.get(signal_name, {}), floor),
                        expected_cells,
                    )
                    for signal_name in contract.signal_names
                ),
            )
        )
    return tuple(censuses)


def lattice_wide_absences(absent_cells_by_day: Mapping[date, int], floor: int) -> tuple[date, ...]:
    """Days excused across at least as much of the lattice as an observation would have to cover."""
    return tuple(sorted(day for day, cells in absent_cells_by_day.items() if cells >= floor))


async def _observed_cell_days(  # noqa: PLR0913 - one argument per lane-scoping dimension, all keyword-only
    session: AsyncSession,
    *,
    source_key: str,
    grid_name: str,
    support_key: str,
    from_day: date,
    through_day: date,
) -> Mapping[str, dict[date, int]]:
    """Distinct observing cells per signal per day, as `{signal: {day: cells}}`."""
    result = await session.execute(
        _OBSERVED_CELL_DAYS_SQL,
        {
            "source_key": source_key,
            "grid_name": grid_name,
            "support_key": support_key,
            "from_day": _midnight_utc(from_day),
            "through_exclusive": _midnight_utc(through_day + timedelta(days=1)),
        },
    )
    observed: dict[str, dict[date, int]] = {}
    for row in result.mappings().all():
        signal_name: object = row["signal_name"]
        observed_day: object = row["observed_day"]
        cell_count: object = row["cell_count"]
        observed.setdefault(_require_str(signal_name, "signal_name"), {})[
            _require_date(observed_day, "observed_day")
        ] = _require_int(cell_count, "cell_count")
    return observed


async def _absent_days(
    session: AsyncSession,
    *,
    source_key: str,
    support_key: str,
    from_day: date,
    through_day: date,
) -> Mapping[str, Mapping[date, int]]:
    """Expand every recorded `no_data` span into its days, keeping how many cells each one excuses."""
    result = await session.execute(
        _ABSENCE_WINDOWS_SQL,
        {
            "source_key": source_key,
            "support_key": support_key,
            "from_day": _midnight_utc(from_day),
            "through_exclusive": _midnight_utc(through_day + timedelta(days=1)),
        },
    )
    absent: dict[str, dict[date, int]] = {}
    for row in result.mappings().all():
        signal_name: object = row["signal_name"]
        window_start: object = row["window_start_day"]
        window_end: object = row["window_end_day"]
        cell_count: object = row["absent_cell_count"]
        start = max(_require_date(window_start, "window_start_day"), from_day)
        end = min(_require_date(window_end, "window_end_day"), through_day)
        if end < start:
            continue
        cells = _require_int(cell_count, "absent_cell_count")
        days = absent.setdefault(_require_str(signal_name, "signal_name"), {})
        for offset in range((end - start).days + 1):
            day = start + timedelta(days=offset)
            # The widest single span wins rather than the sum. Two spans overlapping a day may name
            # overlapping cell sets, and adding them would invent evidence; the maximum can only
            # under-count, which leaves a day MISSING and therefore refetchable. Erring toward work
            # is the safe direction -- erring the other way retires a day upstream can still serve.
            days[day] = max(days.get(day, 0), cells)
    return {signal: dict(days) for signal, days in absent.items()}


def union_missing_days(signals: Iterable[SignalReconciliation]) -> tuple[date, ...]:
    """Every day any signal is missing, oldest first. One fetch serves every signal of a lane."""
    missing: set[date] = set()
    for signal in signals:
        missing.update(signal.missing_days)
    return tuple(sorted(missing))


def _midnight_utc(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=UTC)


def _require_str(value: object, column: str) -> str:
    if not isinstance(value, str):
        raise CoverageCensusError(f"{column} must be text, got {type(value).__name__}")
    return value


def _require_int(value: object, column: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CoverageCensusError(f"{column} must be an integer, got {type(value).__name__}")
    return value


def _require_float(value: object, column: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CoverageCensusError(f"{column} must be numeric, got {type(value).__name__}")
    return float(value)


def _require_date(value: object, column: str) -> date:
    # `datetime` is a `date` subclass, so the order matters: a timestamp reaching a column this
    # census treats as a calendar day would otherwise compare unequal to every plain date.
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, date):
        raise CoverageCensusError(f"{column} must be a date, got {type(value).__name__}")
    return value


def _require_uuid(value: object, column: str) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        return UUID(value)
    raise CoverageCensusError(f"{column} must be a uuid, got {type(value).__name__}")
