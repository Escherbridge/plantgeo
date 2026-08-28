"""The tier-agnostic warehouse census shared by every Parquet operation adapter."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import TYPE_CHECKING, Final

import structlog

from agri_data_service.foundation.parquet.lane_contract import nature_has_time_axis, nature_permits_cadence
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.serving import day_status_sets
from agri_data_service.parquet_ops.wire import LaneCoverage, WarehouseCoverage, contiguous_ranges
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRATIONS

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from agri_data_service.foundation.parquet.lane_contract import LaneNature
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.parquet_ops.warehouse_reader import WarehouseListing

logger = structlog.get_logger()

#: How long one census answer is reused. The client caches for 300 s on top of this; the server-side
#: memo is what stops a burst of cold page loads each paying 52 whole-tier listings.
CENSUS_CACHE_SECONDS: Final = 120

#: Every key ONE census may walk, summed across its lanes and tiers.
#: `warehouse_reader.MAX_LISTED_KEYS_PER_REQUEST` bounds a SINGLE listing at 200,000 and the census
#: makes thirteen lanes x four tiers of them, so nothing bounded the total. The bound is roughly four
#: times the census the registry's own day counts imply (fire-detections' ~9,400 days dominate it),
#: which leaves room to grow and still refuses a walk that has plainly lost.
MAX_CENSUS_LISTED_KEYS: Final = 600_000


@dataclass(frozen=True, slots=True)
class CensusLane:
    """One lane the census reports on: its slug, what its partition day means, and which stream."""

    layer: str
    nature: LaneNature
    kind: PartitionKind
    #: Days between publications, from the lane's own registration. 1 means every day is a candidate.
    cadence_days: int = 1
    #: How long after a publication day that day may still arrive; only a release series uses it.
    publication_lag_days: int = 0


def registered_census_lanes() -> tuple[CensusLane, ...]:
    """Return the observed stream of every registered lane, in registry order.

    OBSERVED ONLY, deliberately. The slider's capability rows are keyed by layer name and resolve to
    the FIRST match, so a second row per lane for `kind=forecast` would make which axis a layer draws
    depend on array order. `kind` stays on the wire so a forecast census can be added behind a
    caller that asks for one, rather than being smuggled into the list this one shares.
    """
    return tuple(
        CensusLane(
            layer=registration.slug,
            nature=registration.nature,
            kind="observed",
            cadence_days=registration.cadence_days,
            publication_lag_days=registration.publication_lag_days,
        )
        for registration in LANE_REGISTRATIONS
    )


def build_lane_coverage(listing: WarehouseListing, *, lane: CensusLane, today: date) -> LaneCoverage:
    """Census one lane across every published tier, closing its ranges against the live edge."""
    data_days, absent_days = _written_days(listing, lane=lane)
    if not data_days:
        # Never written: `null` bounds, and no ranges. A slider must not mount an axis over a lane
        # whose span is a guess -- `soil-survey` has 238,986 source rows and 0 written objects.
        return _bounded(lane, earliest_day=None, latest_day=None)
    earliest_day = min(data_days)
    latest_day = max(data_days)
    if not nature_has_time_axis(lane.nature):
        # A `static_lookup`'s partition day is a VERSION STAMP, not an observation day, so no day
        # between two versions ever carried an obligation to exist. Ranging over them would report
        # every reference lane as one enormous gap and gray out a slider that has no axis to scrub.
        return _bounded(lane, earliest_day=earliest_day, latest_day=latest_day)
    return LaneCoverage(
        layer=lane.layer,
        nature=lane.nature,
        kind=lane.kind,
        earliest_day=earliest_day,
        latest_day=latest_day,
        gap_ranges=contiguous_ranges(_owed_but_unwritten(data_days | absent_days, lane=lane, today=today)),
        governed_absence_ranges=contiguous_ranges(
            day for day in absent_days if day >= earliest_day and day not in data_days
        ),
    )


def build_coverage(
    listing: WarehouseListing,
    *,
    lanes: Sequence[CensusLane],
    generated_at: datetime,
) -> WarehouseCoverage:
    """Census every lane, stamped with the instant it was computed rather than with 'now'."""
    today = generated_at.astimezone(UTC).date()
    budgeted = _BudgetedListing(inner=listing, remaining=MAX_CENSUS_LISTED_KEYS)
    return WarehouseCoverage(
        generated_at=generated_at,
        lanes=tuple(build_lane_coverage(budgeted, lane=lane, today=today) for lane in lanes),
    )


class CoverageCache:
    """One memoized census, reused for `ttl_seconds`; the whole reason coverage is affordable."""

    def __init__(self, ttl_seconds: int = CENSUS_CACHE_SECONDS) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._held: WarehouseCoverage | None = None
        # `threading`, not `asyncio`: `get` runs inside the route's worker thread. Without it, every
        # cold page load in a burst started its own 52-listing walk -- the cost the memo exists to
        # avoid, and the guard `environmental-read-model.getSliderCapabilities` already has.
        self._refreshing = threading.Lock()

    def get(self, listing: WarehouseListing, *, lanes: Sequence[CensusLane], now: datetime) -> WarehouseCoverage:
        """Return the held census while it is fresh, else compute one under a single-flight lock."""
        fresh = self._fresh(now)
        if fresh is not None:
            return fresh
        stale = self._held
        if stale is not None and not self._refreshing.acquire(blocking=False):
            # A refresh is already in flight and a previous census exists. Serving it beats blocking
            # a pool thread, and `generated_at` states exactly how old the answer is.
            return stale
        if stale is None:
            self._refreshing.acquire()
        try:
            return self._refresh(listing, lanes=lanes, now=now)
        finally:
            self._refreshing.release()

    def clear(self) -> None:
        """Drop the held census; a test that changes the warehouse under it needs this."""
        self._held = None

    def _fresh(self, now: datetime) -> WarehouseCoverage | None:
        """Return the held census only while it is inside its TTL."""
        held = self._held
        if held is not None and now - held.generated_at < self._ttl:
            return held
        return None

    def _refresh(self, listing: WarehouseListing, *, lanes: Sequence[CensusLane], now: datetime) -> WarehouseCoverage:
        """Build and hold a new census, having won the lock; a queued caller re-checks first."""
        fresh = self._fresh(now)
        if fresh is not None:
            return fresh
        try:
            built = build_coverage(listing, lanes=lanes, generated_at=now)
        except Exception as exc:
            previous = self._held
            if previous is None:
                raise
            # STALE BEATS NOTHING. A failed refresh must not turn a census the warehouse earned into
            # a whole-map absence claim; the answer carries the instant it was actually computed.
            logger.warning(
                "coverage_census_served_stale",
                fault=type(exc).__name__,
                generated_at=previous.generated_at.isoformat(),
            )
            return previous
        self._held = built
        return built


@dataclass
class _BudgetedListing:
    """A `WarehouseListing` spending ONE key budget across every listing of a single census."""

    inner: WarehouseListing
    remaining: int

    def list_keys(
        self,
        layer: str,
        kind: PartitionKind,
        tier: ZoomTier,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> tuple[str, ...]:
        """List one tier, charging what it returned against the census's aggregate budget."""
        keys = self.inner.list_keys(layer, kind, tier, year=year, month=month)
        self.remaining -= len(keys)
        if self.remaining < 0:
            raise faults.census_budget_exhausted(listed_keys=MAX_CENSUS_LISTED_KEYS)
        return keys

    def read_object(self, relative_key: str) -> bytes | None:
        """Delegate; markers are read one at a time and are not what the budget is about."""
        return self.inner.read_object(relative_key)


def _written_days(listing: WarehouseListing, *, lane: CensusLane) -> tuple[set[date], set[date]]:
    """Union every published tier into the days that hold a release and the days deliberately empty."""
    data_days: set[date] = set()
    absent_days: set[date] = set()
    for tier in ZOOM_TIERS:
        keys = listing.list_keys(lane.layer, lane.kind, tier)
        statuses = day_status_sets(keys, layer=lane.layer, kind=lane.kind, tier=tier)
        # A conflict day HOLDS a release, so the census counts it as written; serving it is what
        # refuses out loud. Reporting it as a gap would claim the lane never wrote that day.
        data_days |= statuses.data | statuses.conflict
        absent_days |= statuses.absent
    return (data_days, absent_days)


def _owed_but_unwritten(accounted: set[date], *, lane: CensusLane, today: date) -> tuple[date, ...]:
    """Return the days this lane OWED and did not deliver, walking its own cadence from the days it did.

    A gap is a day that carried an obligation and is not there. For a `daily_series` (`cadence_days`
    1) that is every day, which is the rule this census has always applied. For a `release_series` it
    is every cadence step: `drought` publishes weekly, so a Wednesday was never owed a USDM map and
    reporting one as missing is a false claim about warehouse content -- the same reasoning the
    `static_lookup` short-circuit above already accepts one rung earlier. Measured 2026-08-25 before
    this rule: 138 releases produced 138 gap ranges, which would paint the lane absent six days in
    seven at cutover while `/release` served those days by carrying the Tuesday forward.
    """
    ordered = sorted(accounted)
    step = timedelta(days=lane.cadence_days)
    owed: list[date] = []
    for previous, following in pairwise(ordered):
        candidate = previous + step
        while candidate < following:
            owed.append(candidate)
            candidate += step
    # At the LIVE EDGE a release is not missing until its publication lag has run out -- USDM's
    # Tuesday map is not late on the Tuesday. A daily series closes against today instead, matching
    # the client's own `closeCoverageGapsAtLiveEdge`: every day up to today was owed an observation,
    # and the days since a lane last published are a gap a reader can act on rather than an unknown.
    horizon = today - timedelta(days=lane.publication_lag_days) if nature_permits_cadence(lane.nature) else today
    candidate = ordered[-1] + step
    while candidate <= horizon:
        owed.append(candidate)
        candidate += step
    return tuple(owed)


def _bounded(lane: CensusLane, *, earliest_day: date | None, latest_day: date | None) -> LaneCoverage:
    """One lane's census with bounds and NO ranges: nothing between those bounds was ever owed."""
    return LaneCoverage(
        layer=lane.layer,
        nature=lane.nature,
        kind=lane.kind,
        earliest_day=earliest_day,
        latest_day=latest_day,
        gap_ranges=(),
        governed_absence_ranges=(),
    )
