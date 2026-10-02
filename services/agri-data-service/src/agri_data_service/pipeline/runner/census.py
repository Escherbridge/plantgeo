"""The full-ladder census: every rung of every stream a lane writes, folded to one status per stream-day.

A day is `data` only when all four rungs read `data`; an incomplete ladder is owed work. See
`pipeline/runner/AGENTS.md` "Census".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final, Protocol

from agri_data_service.foundation.parquet.paths import partition_day_statuses
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from agri_data_service.foundation.parquet.paths import PartitionDayStatus, PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier

#: Base rung first: the ladder a config lane writes and the census reads.
FULL_LADDER_TIERS: Final[tuple[ZoomTier, ...]] = (LANE_BASE_ZOOM_TIER, *DERIVED_ZOOM_TIERS)
#: Config lanes write observed partitions only.
CONFIG_LANE_KIND: Final[PartitionKind] = "observed"
#: A window shorter than this lists month prefixes; a longer one (gap-fill) lists whole years.
MONTH_SCOPED_WINDOW_DAYS: Final = 62
_MONTHS_PER_YEAR: Final = 12


class CensusConflictError(RuntimeError):
    """A stream-day holds both a governed absence and data, or an absent base under derived data (exit 70)."""


class PartitionKeyLister(Protocol):
    """The one listing the census needs; `pipeline/parquet/objectstore.py::ObjectStore` satisfies it."""

    def list_partition_keys(
        self,
        layer: str,
        kind: PartitionKind,
        zoom: ZoomTier,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class LaneCensus:
    """One status per stream per day over `[first, last]`, and the lane-level reading of them."""

    first: date
    last: date
    streams: Mapping[str, Mapping[date, PartitionDayStatus]]
    #: Base-rung status; published data owes only ladder work when its source support is also proven complete.
    base: Mapping[str, Mapping[date, PartitionDayStatus]] = field(default_factory=dict)
    source_owed: Mapping[str, frozenset[date]] = field(default_factory=dict)

    def days(self) -> tuple[date, ...]:
        """Every day of the window, ascending."""
        return tuple(self.first + timedelta(days=offset) for offset in range((self.last - self.first).days + 1))

    def status(self, stream: str, day: date) -> PartitionDayStatus:
        """One stream-day's folded status; a day outside the window reads `missing`."""
        return self.streams.get(stream, {}).get(day, "missing")

    def owed_days(self) -> tuple[date, ...]:
        """Days any stream has not finished (missing or an incomplete ladder), ascending."""
        return tuple(
            day
            for day in self.days()
            if any(
                self.status(stream, day) in {"missing", "incomplete"} or day in self.source_owed.get(stream, ())
                for stream in self.streams
            )
        )

    def source_owed_days(self) -> frozenset[date]:
        """Published base days whose current source support has no full receipt proof."""
        return frozenset(day for days in self.source_owed.values() for day in days)

    def absent_days(self) -> tuple[date, ...]:
        """Days every stream governs as absent, ascending: the absence-recheck candidates."""
        if not self.streams:
            return ()
        return tuple(day for day in self.days() if all(self.status(stream, day) == "absent" for stream in self.streams))

    def data_days(self, stream: str) -> tuple[date, ...]:
        """Days one stream publishes with values, ascending."""
        return tuple(day for day in self.days() if self.status(stream, day) == "data")

    def base_data_days(self) -> frozenset[date]:
        """Days every stream's base rung already carries values (an incomplete ladder is owed, not unsettled)."""
        if not self.base:
            return frozenset()
        return frozenset(
            day for day in self.days() if all(self.base.get(stream, {}).get(day) == "data" for stream in self.streams)
        )

    def lane_data_days(self) -> tuple[date, ...]:
        """Days every stream publishes with values, ascending."""
        if not self.streams:
            return ()
        source_owed = self.source_owed_days()
        return tuple(
            day
            for day in self.days()
            if day not in source_owed and all(self.status(stream, day) == "data" for stream in self.streams)
        )


def fold_ladder(day: date, rungs: Mapping[ZoomTier, PartitionDayStatus], *, stream: str) -> PartitionDayStatus:
    """One stream-day's status across the full ladder; raises on the two shapes no writer may leave."""
    base = rungs[LANE_BASE_ZOOM_TIER]
    if "conflict" in rungs.values():
        raise CensusConflictError(f"{stream} {day.isoformat()} has a data/absence conflict: {dict(rungs)}")
    if base == "absent":
        if any(rungs[tier] in {"data", "incomplete"} for tier in DERIVED_ZOOM_TIERS):
            raise CensusConflictError(
                f"{stream} {day.isoformat()} is absent at the base rung but carries derived parts"
            )
        return "absent"
    if all(status == "data" for status in rungs.values()):
        return "data"
    if base == "missing" and all(status == "missing" for status in rungs.values()):
        return "missing"
    return "incomplete"


def _month_starts(first: date, last: date) -> Iterable[date]:
    cursor = date(first.year, first.month, 1)
    while cursor <= last:
        yield cursor
        rolls_over = cursor.month == _MONTHS_PER_YEAR
        cursor = date(cursor.year + (1 if rolls_over else 0), 1 if rolls_over else cursor.month + 1, 1)


def _prefix_scopes(first: date, last: date) -> Iterable[tuple[int, int | None]]:
    """The (year, month) listing scopes over `[first, last]`: months for a short window, years for a long one."""
    if (last - first).days < MONTH_SCOPED_WINDOW_DAYS:
        for month in _month_starts(first, last):
            yield month.year, month.month
        return
    for year in range(first.year, last.year + 1):
        yield year, None


def read_census(lister: PartitionKeyLister, streams: Sequence[str], first: date, last: date) -> LaneCensus:
    """List every rung of every stream by month (or year, for a gap-fill window), and fold each stream-day's ladder."""
    folded: dict[str, dict[date, PartitionDayStatus]] = {}
    base: dict[str, dict[date, PartitionDayStatus]] = {}
    for stream in streams:
        per_tier: dict[ZoomTier, dict[date, PartitionDayStatus]] = {}
        for tier in FULL_LADDER_TIERS:
            keys: list[str] = []
            for year, month in _prefix_scopes(first, last):
                keys.extend(lister.list_partition_keys(stream, CONFIG_LANE_KIND, tier, year=year, month=month))
            per_tier[tier] = partition_day_statuses(
                layer=stream, kind=CONFIG_LANE_KIND, zoom=tier, first_day=first, last_day=last, keys=keys
            )
        folded[stream] = {
            day: fold_ladder(day, {tier: per_tier[tier][day] for tier in FULL_LADDER_TIERS}, stream=stream)
            for day in per_tier[LANE_BASE_ZOOM_TIER]
        }
        base[stream] = per_tier[LANE_BASE_ZOOM_TIER]
    return LaneCensus(first=first, last=last, streams=folded, base=base)


__all__ = [
    "CONFIG_LANE_KIND",
    "FULL_LADDER_TIERS",
    "MONTH_SCOPED_WINDOW_DAYS",
    "CensusConflictError",
    "LaneCensus",
    "PartitionKeyLister",
    "fold_ladder",
    "read_census",
]
