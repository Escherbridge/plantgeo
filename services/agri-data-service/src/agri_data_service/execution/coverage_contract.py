"""Declare how far back each lane must be complete, and reconcile that claim against the record.

The missing half of the backfill story. The service could already walk a fixed window
(`historical-*-backfill`) and extend a completed walk forward to the live edge
(`historical-plan-continue`), and `ops_data_streams.sql` could already report that a stream has
`missing_days` -- but nothing connected the two. A hole in the middle of a lane was visible on a
dashboard and unreachable by any verb: the only way to refill 2024-03-14 was to hand-author a
plan naming it. This module is what turns "there are 37 missing days" into the list of 37 days,
so a filler has something to act on.

Three fills, and they are not the same operation:

  * BACK   -- the initial fixed-window walk. Already exists.
  * FORWARD -- extend a finished walk to today. Already exists (`plan_continuation`).
  * GAP    -- refill interior days the record is missing. This module.

A day is MISSING only if the lane is contracted to hold it, the warehouse has no observation
for it, and no governed absence explains it. That third clause is the one that keeps a filler
from grinding forever against days upstream never published: `agri.signal_coverage_audit`
already records `status='no_data'` for a window we asked for and the provider answered nothing
to -- written by `historical_backfill.py` and `historical_cams.py` -- and until now nothing
read it back. An absence is evidence, not a hole.

See `src/agri_data_service/execution/AGENTS.md` §coverage-contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


class Cadence(StrEnum):
    """How often a lane is expected to publish. Decides which days count as required."""

    DAILY = "daily"
    """One observation per calendar day."""

    WEEKLY = "weekly"
    """One release per week, on a fixed weekday. USDM publishes Tuesdays."""


@dataclass(frozen=True, slots=True)
class LaneCoverageContract:
    """What one lane promises: which signals, from which day, at what cadence."""

    source_key: str
    """`agri.data_source.key`. The lane gate every governed view already uses."""

    signal_names: tuple[str, ...]
    """`agri.signal_observation.signal_name`. Every one is contracted independently."""

    support_key: str
    """`agri.signal_observation.support_key`, which discriminates lanes sharing a signal name."""

    grid_name: str
    """`agri.spatial_cell.grid_name`. Scopes the cells a day is required over."""

    earliest_required_day: date
    """The oldest day this lane must hold. Days before it are out of contract, not missing."""

    cadence: Cadence

    weekly_anchor_weekday: int = 1
    """0=Monday. Read only when `cadence` is WEEKLY; 1 is USDM's Tuesday release."""

    minimum_cell_fraction: float = 1.0
    """
    Share of the lane's cells a day must carry before it counts as covered.

    1.0 for a lane that fills its whole lattice every day. Below 1.0 for a lane that is
    legitimately partial -- the NASA POWER soil-wetness pilot is measured on a fraction of the
    397-cell lattice, and holding it to 1.0 would report every one of its ~1,460 days as
    missing and send a filler after cells the upstream product does not cover at that cadence.
    Lowering it is a claim about the lane and belongs here, beside the lane, rather than as a
    threshold buried in a query.
    """


class DayState(StrEnum):
    """What the record says about one contracted day."""

    COVERED = "covered"
    """Observations landed at or above the lane's cell-fraction floor."""

    THIN = "thin"
    """Some cells landed, but under the floor. An observation, and also a partial fill."""

    ABSENT = "absent"
    """We asked and upstream published nothing. Evidence, not a hole. Never refetched."""

    MISSING = "missing"
    """Contracted, unobserved, unexplained. The only state a gap filler acts on."""


@dataclass(frozen=True, slots=True)
class DayCoverage:
    """One contracted day and what the warehouse holds for it."""

    day: date
    state: DayState
    observed_cell_count: int
    expected_cell_count: int


@dataclass(frozen=True, slots=True)
class SignalReconciliation:
    """One signal's contracted window, day by day, with the work list separated out."""

    source_key: str
    signal_name: str
    earliest_required_day: date
    through_day: date
    days: tuple[DayCoverage, ...]

    @property
    def missing_days(self) -> tuple[date, ...]:
        """The gap filler's work list, oldest first. Excludes governed absences by construction."""
        return tuple(entry.day for entry in self.days if entry.state is DayState.MISSING)

    @property
    def thin_days(self) -> tuple[date, ...]:
        """Days that landed under the floor. Refillable, but not holes -- report them apart."""
        return tuple(entry.day for entry in self.days if entry.state is DayState.THIN)

    @property
    def absent_days(self) -> tuple[date, ...]:
        """Days upstream published nothing for. Retrying these is what the audit table prevents."""
        return tuple(entry.day for entry in self.days if entry.state is DayState.ABSENT)

    @property
    def required_day_count(self) -> int:
        return len(self.days)

    @property
    def is_complete(self) -> bool:
        """
        True when nothing is left to fetch.

        Absences count as SATISFIED, and that is the whole point of recording them: a lane whose
        provider never published 2024-03-14 can still be complete, and a definition that said
        otherwise would leave every such lane permanently at 99% with a work list that can never
        empty. Thin days are NOT satisfied -- they are a partial fill worth retrying.
        """
        return not any(entry.state in (DayState.MISSING, DayState.THIN) for entry in self.days)

    @property
    def completeness_fraction(self) -> float:
        """Share of contracted days that need no further fetch. 1.0 on an empty window."""
        if not self.days:
            return 1.0
        satisfied = sum(1 for entry in self.days if entry.state in (DayState.COVERED, DayState.ABSENT))
        return satisfied / len(self.days)


def required_days(contract: LaneCoverageContract, through_day: date) -> tuple[date, ...]:
    """Every day the contract obliges the lane to hold, oldest first.

    Empty when `through_day` precedes the contract's start, which is the honest answer for a
    lane whose history has not opened yet rather than an error.
    """
    if through_day < contract.earliest_required_day:
        return ()
    if contract.cadence is Cadence.DAILY:
        span = (through_day - contract.earliest_required_day).days + 1
        return tuple(contract.earliest_required_day + timedelta(days=offset) for offset in range(span))

    # WEEKLY: walk forward from the first anchor weekday at or after the contract's start, so a
    # contract that opens mid-week does not report the days before its first release as missing.
    offset_to_anchor = (contract.weekly_anchor_weekday - contract.earliest_required_day.weekday()) % 7
    cursor = contract.earliest_required_day + timedelta(days=offset_to_anchor)
    anchors: list[date] = []
    while cursor <= through_day:
        anchors.append(cursor)
        cursor += timedelta(days=7)
    return tuple(anchors)


def covered_cell_floor(contract: LaneCoverageContract, expected_cell_count: int) -> int:
    """Cells a day must carry before it counts as covered. At least one, whatever the fraction rounds to."""
    return max(1, round(expected_cell_count * contract.minimum_cell_fraction))


def reconcile_signal(  # noqa: PLR0913 - one argument per measurement; bundling them hides the inputs
    contract: LaneCoverageContract,
    signal_name: str,
    through_day: date,
    observed_cells_by_day: dict[date, int],
    absent_days: Iterable[date],
    expected_cell_count: int,
) -> SignalReconciliation:
    """Classify every contracted day for one signal.

    Pure: the caller supplies what the warehouse holds, so the whole classification is testable
    without a database. `observed_cells_by_day` is the count of distinct cells carrying an
    accepted observation that day; `absent_days` is what `signal_coverage_audit` recorded
    `no_data` for.

    A day present in BOTH maps is COVERED, not ABSENT. An absence is a statement about a fetch,
    and a later fetch that landed rows supersedes it -- taking the absence first would blank a
    day whose data we demonstrably hold.

    `absent_days` must already hold only days the caller can show the WHOLE lattice was excused on;
    see `execution/AGENTS.md` §coverage-contract for why that filtering is the reader's job.
    """
    absent = set(absent_days)
    floor = covered_cell_floor(contract, expected_cell_count)

    days: list[DayCoverage] = []
    for day in required_days(contract, through_day):
        observed = observed_cells_by_day.get(day, 0)
        if observed >= floor:
            state = DayState.COVERED
        elif observed > 0:
            state = DayState.THIN
        elif day in absent:
            state = DayState.ABSENT
        else:
            state = DayState.MISSING
        days.append(
            DayCoverage(
                day=day,
                state=state,
                observed_cell_count=observed,
                expected_cell_count=expected_cell_count,
            )
        )

    return SignalReconciliation(
        source_key=contract.source_key,
        signal_name=signal_name,
        earliest_required_day=contract.earliest_required_day,
        through_day=through_day,
        days=tuple(days),
    )


def contiguous_ranges(days: Sequence[date]) -> tuple[tuple[date, date], ...]:
    """Collapse a sorted day list into inclusive runs.

    A filler asks upstream for WINDOWS, not days: NASA POWER's daily point API takes a start and
    an end, and 37 scattered days is 37 requests where 4 runs is 4. This is what makes a gap fill
    cost the same order as the original walk rather than one request per hole.
    """
    if not days:
        return ()
    ordered = sorted(set(days))
    runs: list[tuple[date, date]] = []
    start = previous = ordered[0]
    for day in ordered[1:]:
        if (day - previous).days == 1:
            previous = day
            continue
        runs.append((start, previous))
        start = previous = day
    runs.append((start, previous))
    return tuple(runs)


# The lanes under contract, and the day each must be complete from.
#
# Every date here is a CLAIM the cron is then held to, not a description of what happens to be
# loaded -- which is why they are declared rather than read from `min(observed_at)`. Deriving the
# horizon from the data would make the contract unfalsifiable: a lane that lost its first year
# would silently re-contract itself to its own truncated history and report 100%.
LANE_COVERAGE_CONTRACTS: Final[tuple[LaneCoverageContract, ...]] = (
    LaneCoverageContract(
        source_key="nasa-power-daily",
        signal_names=(
            "air_temperature_mean",
            "air_temperature_max",
            "air_temperature_min",
            "dew_point_temperature",
            "precipitation",
            "relative_humidity",
            "surface_shortwave_radiation",
            "wind_speed",
        ),
        support_key="surface",
        grid_name="nasa-power-0.5-degree",
        earliest_required_day=date(2022, 8, 6),
        cadence=Cadence.DAILY,
    ),
    # The three soil-wetness signals, split out only because they open later than the eight above.
    # Measured against production 2026-08-11: 4 cells for exactly 98 days (2022-04-30..2022-08-05),
    # then the full 397 for all 1,462 days from 2022-08-06 onward, gapless. `earliest_required_day`
    # below IS that widening date, so the narrow era is already excluded by the horizon and the
    # lattice is whole for every day this contract requires. They therefore hold to
    # `minimum_cell_fraction` 1.0 like every other lane: a fractional floor here would accept a day
    # carrying 199 of 397 cells as complete, which is precisely the gap this contract exists to find.
    LaneCoverageContract(
        source_key="nasa-power-daily",
        signal_names=(
            "soil_wetness_surface",
            "soil_wetness_root_zone",
            "soil_wetness_profile",
        ),
        support_key="surface",
        grid_name="nasa-power-0.5-degree",
        earliest_required_day=date(2022, 8, 6),
        cadence=Cadence.DAILY,
    ),
    LaneCoverageContract(
        # Verified against `agri.data_source.key` 2026-08-11; the bare `open-meteo-era5-land` this
        # was first written as matches no row, so every reconciliation over it returned empty.
        source_key="open-meteo-era5-land-archive",
        signal_names=(
            "soil_water_content_layer_1",
            "soil_water_content_layer_2",
            "soil_water_content_layer_3",
            "soil_temperature_level_1",
            "soil_temperature_level_2",
            "soil_temperature_level_3",
            "soil_temperature_level_4",
            "vapor_pressure_deficit",
        ),
        support_key="era5-land-0.1deg",
        grid_name="sentinel2-ndvi-0p25deg",
        earliest_required_day=date(2022, 4, 30),
        cadence=Cadence.DAILY,
        # 1470/1568 exactly. The lattice is shared with Sentinel-2, which sees ocean; ERA5-LAND does
        # not publish over water, so 98 of its cells can never carry a reading. Measured 2026-08-11:
        # the 98 never-observed cells span longitude -124.88..-122.38 (mean -124.33) against
        # -124.88..-111.13 for the 1,470 observed -- they are the Pacific edge, not a hole.
        # At 1.0 this lane classified EVERY contracted day as THIN and sent a filler after cells no
        # upstream will ever serve. 0.9375 puts the floor exactly at 1,470 land cells, so a genuine
        # loss of even one still reads THIN.
        minimum_cell_fraction=0.9375,
    ),
)


def contracts_for_source(source_key: str) -> tuple[LaneCoverageContract, ...]:
    """Every contract governing one lane. More than one when a lane holds a pilot beside a field."""
    return tuple(contract for contract in LANE_COVERAGE_CONTRACTS if contract.source_key == source_key)
