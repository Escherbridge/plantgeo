"""Nearest-published-day substitution: one per-lane tolerance rule and the pure nearest-day choice.

See agent/AGENTS.md, "Closest-datapoint reads (2026-10-04)", for the owner decision and the contract.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final, Literal

from agri_data_service.foundation.parquet.lane_contract import nature_has_time_axis
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date

#: The floor every time-axis lane gets: a daily lane may substitute a day up to three days away.
DAILY_TOLERANCE_DAYS: Final = 3

#: A multi-day rhythm (weekly drought, NDVI revisit) may substitute up to two whole intervals away.
CADENCE_TOLERANCE_MULTIPLIER: Final = 2

#: Daily-series lanes whose partitions are SPARSE observations of a multi-day satellite revisit.
#: A `daily_series` may not declare `cadence_days > 1` (lane_registry refuses it), so the registry
#: carries such a lane's MEASURED observation gap as its `publication_lag_days` instead -- vegetation
#: cites "Lag 7 from section 2's MEASURED median 7-day gap between observation days". Listing a lane
#: here says "read that lag as the revisit interval"; it is the one hand-spelled fact in this rule.
SPARSE_REVISIT_LANES: Final = frozenset({"vegetation"})

ResolutionMode = Literal["nearest", "as_of", "static"]


@dataclass(frozen=True, slots=True)
class DayTolerance:
    """How one lane answers a day it did not publish, and the named rule that decided it."""

    mode: ResolutionMode
    tolerance_days: int | None
    rule: str
    #: Days the lane's provider runs behind today (`publication_lag_days`); 0 for sparse-revisit lanes.
    settle_lag_days: int = 0

    def for_request(self, requested: date, today: date | None) -> DayTolerance:
        """Widen the bound by the part of the settle lag that still overlaps the requested day.

        A lane cannot have published anything newer than `today - settle_lag_days`, so a request near
        today is measured from that settled edge; a request older than the lag keeps the plain bound.
        """
        if self.tolerance_days is None or today is None or self.settle_lag_days == 0:
            return self
        # A request after today is still at most the whole lag unsettled: never a wider stretch.
        unsettled = self.settle_lag_days - max(0, (today - requested).days)
        if unsettled <= 0:
            return self
        return replace(self, tolerance_days=self.tolerance_days + unsettled, rule=f"{self.rule}+settle_lag")

    def to_wire(self) -> dict[str, object]:
        """Render the policy beside a lane's result so the caller can see the bound applied."""
        return {
            "resolution": self.mode,
            "tolerance_days": self.tolerance_days,
            "tolerance_rule": self.rule,
            "settle_lag_days": self.settle_lag_days,
        }


def day_tolerance(lane: str) -> DayTolerance:
    """Two-interval rule with a three-day floor, derived from the lane registry's cadence facts.

    - `static_lookup` (no time axis): read at the current release; a day has no meaning.
    - `release_series` with no rhythm (`cadence_days == 1`: MTBS, crop-cover, forecasts): the
      existing as-of rule (newest release at or before the day) already answers every day.
    - every other time-axis lane: tolerance = max(3, 2 x interval), where interval is the
      registry's `cadence_days` (7 for weekly drought -> 14) or, for a sparse-revisit daily lane,
      its measured observation gap (`publication_lag_days`, 7 for NDVI -> 14). Daily lanes get 3.
    - near today the bound also absorbs the lane's settle lag (`DayTolerance.for_request`): ERA5
      lanes settle 5 days behind, so "today" may borrow up to 3 + 5 days back. A sparse-revisit
      lane's lag field is its revisit gap, already inside its interval, so it gets no allowance.
    """
    registration = LANE_REGISTRY.get(lane)
    if registration is None:
        return DayTolerance("nearest", 0, "unregistered_exact_only")
    if not nature_has_time_axis(registration.nature):
        return DayTolerance("static", None, "static_current_release")
    if registration.nature == "release_series" and registration.cadence_days == 1:
        return DayTolerance("as_of", None, "release_as_of")
    interval = registration.cadence_days
    if lane in SPARSE_REVISIT_LANES:
        interval = max(interval, registration.publication_lag_days)
    tolerance = max(DAILY_TOLERANCE_DAYS, CADENCE_TOLERANCE_MULTIPLIER * interval)
    rule = "daily_floor" if tolerance == DAILY_TOLERANCE_DAYS else "two_intervals"
    settle_lag = 0 if lane in SPARSE_REVISIT_LANES else registration.publication_lag_days
    return DayTolerance("nearest", tolerance, rule, settle_lag_days=settle_lag)


@dataclass(frozen=True, slots=True)
class NearestDay:
    """The published day closest to a requested one, its signed offset, and whether it may be served."""

    day: date
    offset: int
    within_tolerance: bool


def nearest_published_day(
    published: Iterable[date], requested: date, *, tolerance_days: int, today: date | None
) -> NearestDay | None:
    """Pick the closest published day other than `requested`; a tie goes to the EARLIER, settled day.

    `today=None` is a forecast read (`selection_scope.schedule_ceiling`): future days are candidates.
    """
    candidates = [day for day in published if day != requested and (today is None or day <= today)]
    if not candidates:
        return None
    # Sort key (gap, day): equal gaps fall back to calendar order, so the earlier day wins a tie.
    best = min(candidates, key=lambda day: (abs((day - requested).days), day))
    offset = (best - requested).days
    return NearestDay(day=best, offset=offset, within_tolerance=abs(offset) <= tolerance_days)
