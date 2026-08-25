"""The whole-warehouse census: what each lane has and has not written, over its whole published span.

Layer L4. Per lane and TIER-AGNOSTIC -- a day counts as covered when any published tier holds it.
No bbox and no zoom, deliberately: one answer is shared by every viewport. See `AGENTS.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.lane_contract import nature_has_time_axis
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.interface.http.serving import day_status_sets
from agri_data_service.interface.http.wire import LaneCoverage, WarehouseCoverage, contiguous_ranges
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRATIONS

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from agri_data_service.foundation.parquet.lane_contract import LaneNature
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.interface.http.warehouse_reader import WarehouseListing

#: How long one census answer is reused. The client caches for 300 s on top of this; the server-side
#: memo is what stops a burst of cold page loads each paying 52 whole-tier listings.
CENSUS_CACHE_SECONDS: Final = 120


@dataclass(frozen=True, slots=True)
class CensusLane:
    """One lane the census reports on: its slug, what its partition day means, and which stream."""

    layer: str
    nature: LaneNature
    kind: PartitionKind


def registered_census_lanes() -> tuple[CensusLane, ...]:
    """Return the observed stream of every registered lane, in registry order.

    OBSERVED ONLY, deliberately. The slider's capability rows are keyed by layer name and resolve to
    the FIRST match, so a second row per lane for `kind=forecast` would make which axis a layer draws
    depend on array order. `kind` stays on the wire so a forecast census can be added behind a
    caller that asks for one, rather than being smuggled into the list this one shares.
    """
    return tuple(
        CensusLane(layer=registration.slug, nature=registration.nature, kind="observed")
        for registration in LANE_REGISTRATIONS
    )


def build_lane_coverage(listing: WarehouseListing, *, lane: CensusLane, today: date) -> LaneCoverage:
    """Census one lane across every published tier, closing its ranges against the live edge."""
    data_days: set[date] = set()
    absent_days: set[date] = set()
    for tier in ZOOM_TIERS:
        keys = listing.list_keys(lane.layer, lane.kind, tier)
        statuses = day_status_sets(keys, layer=lane.layer, kind=lane.kind, tier=tier)
        # A conflict day HOLDS a release, so the census counts it as written; serving it is what
        # refuses out loud. Reporting it as a gap would claim the lane never wrote that day.
        data_days |= statuses.data | statuses.conflict
        absent_days |= statuses.absent
    if not data_days:
        # Never written: `null` bounds, and no ranges. A slider must not mount an axis over a lane
        # whose span is a guess -- `soil-survey` has 238,986 source rows and 0 written objects.
        return LaneCoverage(
            layer=lane.layer,
            nature=lane.nature,
            kind=lane.kind,
            earliest_day=None,
            latest_day=None,
            gap_ranges=(),
            governed_absence_ranges=(),
        )
    earliest_day = min(data_days)
    latest_day = max(data_days)
    if not nature_has_time_axis(lane.nature):
        # A `static_lookup`'s partition day is a VERSION STAMP, not an observation day, so no day
        # between two versions ever carried an obligation to exist. Ranging over them would report
        # every reference lane as one enormous gap and gray out a slider that has no axis to scrub.
        return LaneCoverage(
            layer=lane.layer,
            nature=lane.nature,
            kind=lane.kind,
            earliest_day=earliest_day,
            latest_day=latest_day,
            gap_ranges=(),
            governed_absence_ranges=(),
        )
    # Ranges close against TODAY rather than against `latest_day`, matching the client's own
    # `closeCoverageGapsAtLiveEdge`: the days since a lane last published are a gap a reader can act
    # on, and stopping the census at `latest_day` would render them as unknown instead.
    window_end = max(latest_day, today)
    span = tuple(earliest_day + timedelta(days=offset) for offset in range((window_end - earliest_day).days + 1))
    return LaneCoverage(
        layer=lane.layer,
        nature=lane.nature,
        kind=lane.kind,
        earliest_day=earliest_day,
        latest_day=latest_day,
        gap_ranges=contiguous_ranges(day for day in span if day not in data_days and day not in absent_days),
        governed_absence_ranges=contiguous_ranges(day for day in span if day in absent_days and day not in data_days),
    )


def build_coverage(
    listing: WarehouseListing,
    *,
    lanes: Sequence[CensusLane],
    generated_at: datetime,
) -> WarehouseCoverage:
    """Census every lane, stamped with the instant it was computed rather than with 'now'."""
    today = generated_at.astimezone(UTC).date()
    return WarehouseCoverage(
        generated_at=generated_at,
        lanes=tuple(build_lane_coverage(listing, lane=lane, today=today) for lane in lanes),
    )


class CoverageCache:
    """One memoized census, reused for `ttl_seconds`; the whole reason coverage is affordable."""

    def __init__(self, ttl_seconds: int = CENSUS_CACHE_SECONDS) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._held: WarehouseCoverage | None = None

    def get(self, listing: WarehouseListing, *, lanes: Sequence[CensusLane], now: datetime) -> WarehouseCoverage:
        """Return the held census while it is fresh, else compute and hold a new one."""
        held = self._held
        if held is not None and now - held.generated_at < self._ttl:
            return held
        built = build_coverage(listing, lanes=lanes, generated_at=now)
        self._held = built
        return built

    def clear(self) -> None:
        """Drop the held census; a test that changes the warehouse under it needs this."""
        self._held = None
