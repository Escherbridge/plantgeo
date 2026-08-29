"""The exact physical-lane and zoom-rung census shared by Parquet adapters."""

from __future__ import annotations

import threading
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.lane_contract import nature_has_time_axis, nature_permits_cadence
from agri_data_service.foundation.parquet.paths import zoom_prefix
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.serving import day_status_sets
from agri_data_service.parquet_ops.snapshot_products import PRODUCT_BY_LAYER
from agri_data_service.parquet_ops.wire import DayRange, LaneCoverage, WarehouseCoverage, contiguous_ranges
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRATIONS

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from datetime import date

    from agri_data_service.foundation.parquet.lane_contract import LaneNature
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.parquet_ops.warehouse_reader import WarehouseListing

#: How long one census answer is reused. The client caches for 300 s on top of this; the server-side
#: memo is what stops a burst of cold page loads each paying every whole-stream listing.
CENSUS_CACHE_SECONDS: Final = 120

#: Every key ONE census may walk, summed across its lanes and tiers.
#: `warehouse_reader.MAX_LISTED_KEYS_PER_REQUEST` bounds a SINGLE listing at 200,000 and the census
#: spans registered and dedicated-product lanes across four tiers, so nothing else bounds the total.
#: The existing ceiling remains fail-closed: an expanded product census that exceeds it refuses the
#: whole answer instead of presenting a partial layer matrix as complete.
MAX_CENSUS_LISTED_KEYS: Final = 600_000

#: Coverage owns no DuckDB connection; this separately bounds its R2 network fan-out on a cold read.
CENSUS_LIST_WORKERS: Final = 3

# Dedicated slider products are physical warehouse prefixes even though they are not direct-ingest
# registrations. Missing expected prefixes remain in the census with null bounds, which is evidence
# to withhold their capability rather than permission to fall back to another store.
DEDICATED_SLIDER_PRODUCT_LAYERS: Final[tuple[str, ...]] = (
    "climate-field-precipitation",
    "climate-field-shortwave-radiation",
    "soil-field-moisture-0-7cm",
    "soil-field-moisture-28-100cm",
    "soil-field-moisture-7-28cm",
)

NON_SLIDER_REGISTERED_LAYERS: Final = frozenset({"calendar", "signal"})

# Drought is the only direct Parquet release reader; PostgreSQL-backed event releases keep their
# recorded-day coverage until they receive their own bounded carry contract.
BOUNDED_CARRY_RELEASE_LAYERS: Final = frozenset({"drought"})

# The direct drought reader accepts the latest stored release through age 14. Once another release
# or governed absence lands, the older entry becomes historical and reverts to cadence-bounded carry.
LATEST_RELEASE_CARRY_DAYS: Final = 14


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


@dataclass(frozen=True, slots=True)
class _LaneDays:
    """The three serving-relevant status sets retained from one exact physical rung."""

    data: frozenset[date]
    absent: frozenset[date]
    conflict: frozenset[date]


def registered_census_lanes() -> tuple[CensusLane, ...]:
    """Return direct slider streams plus every schema-backed dedicated slider product.

    OBSERVED ONLY, deliberately. The slider's capability rows are keyed by layer name and resolve to
    the FIRST match, so a second row per lane for `kind=forecast` would make which axis a layer draws
    depend on array order. `kind` stays on the wire so a forecast census can be added behind a
    caller that asks for one, rather than being smuggled into the list this one shares.
    """
    registered = tuple(
        CensusLane(
            layer=registration.slug,
            nature=registration.nature,
            kind="observed",
            cadence_days=registration.cadence_days,
            publication_lag_days=registration.publication_lag_days,
        )
        for registration in LANE_REGISTRATIONS
        if registration.slug not in NON_SLIDER_REGISTERED_LAYERS and registration.slug not in PRODUCT_BY_LAYER
    )
    registered_layers = {lane.layer for lane in registered}
    derived = tuple(
        CensusLane(layer=layer, nature="daily_series", kind="observed")
        for layer in DEDICATED_SLIDER_PRODUCT_LAYERS
        if layer not in registered_layers
    )
    return registered + derived


def build_lane_coverage(
    listing: WarehouseListing,
    *,
    lane: CensusLane,
    tier: ZoomTier,
    today: date,
) -> LaneCoverage:
    """Census one physical lane rung, closing its ranges against the live edge."""
    return _lane_coverage(lane=lane, tier=tier, today=today, days=_tier_days(listing, lane=lane, tier=tier))


def _lane_coverage(
    *,
    lane: CensusLane,
    tier: ZoomTier,
    today: date,
    days: _LaneDays,
) -> LaneCoverage:
    """Close one lane's already-listed facts against its declared nature and live edge."""
    data_days = set(days.data)
    absent_days = set(days.absent)
    conflict_days = set(days.conflict)
    if not data_days:
        # Never written: `null` bounds, and no ranges. A slider must not mount an axis over a lane
        # whose span is a guess -- `soil-survey` has 238,986 source rows and 0 written objects.
        return _bounded(lane, tier=tier, earliest_day=None, latest_day=None, published_ranges=())
    earliest_day = min(data_days)
    latest_day = max(data_days)
    if not nature_has_time_axis(lane.nature):
        # A `static_lookup`'s partition day is a VERSION STAMP, not an observation day, so no day
        # between two versions ever carried an obligation to exist. Ranging over them would report
        # every reference lane as one enormous gap and gray out a slider that has no axis to scrub.
        return _bounded(
            lane,
            tier=tier,
            earliest_day=earliest_day,
            latest_day=latest_day,
            published_ranges=contiguous_ranges(data_days),
        )
    if _uses_bounded_release_carry(lane):
        accounted_days = data_days | absent_days
        latest_status_day = max(accounted_days | conflict_days)
        published_days = _release_carried_days(
            data_days,
            lane=lane,
            today=today,
            latest_status_day=latest_status_day,
        )
        if not published_days:
            return _bounded(
                lane,
                tier=tier,
                earliest_day=min(data_days),
                latest_day=max(data_days),
                published_ranges=(),
            )
        governed_absence_days = (
            _release_carried_days(
                absent_days,
                lane=lane,
                today=today,
                latest_status_day=latest_status_day,
            )
            - published_days
        )
        return LaneCoverage(
            layer=lane.layer,
            nature=lane.nature,
            kind=lane.kind,
            zoom=tier,
            earliest_day=min(published_days),
            latest_day=max(published_days),
            published_ranges=contiguous_ranges(published_days),
            gap_ranges=contiguous_ranges(
                _owed_but_unwritten(
                    data_days | absent_days,
                    lane=lane,
                    today=today,
                    conflict_days=conflict_days,
                )
            ),
            governed_absence_ranges=contiguous_ranges(governed_absence_days),
        )
    return LaneCoverage(
        layer=lane.layer,
        nature=lane.nature,
        kind=lane.kind,
        zoom=tier,
        earliest_day=earliest_day,
        latest_day=latest_day,
        published_ranges=contiguous_ranges(data_days),
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
    budgeted = _BudgetedListing(inner=listing, budget=_CensusKeyBudget(MAX_CENSUS_LISTED_KEYS))
    if not lanes:
        return WarehouseCoverage(generated_at=generated_at, evaluated_through_day=today, lanes=())
    jobs = tuple(enumerate(lanes))
    with ThreadPoolExecutor(
        max_workers=min(CENSUS_LIST_WORKERS, len(jobs)),
        thread_name_prefix="parquet-coverage-list",
    ) as pool:
        futures = tuple(pool.submit(_stream_tier_days, budgeted, lane=lane) for _index, lane in jobs)
        done, pending = wait(futures, return_when=FIRST_EXCEPTION)
        failed = next((future for future in done if future.exception() is not None), None)
        if failed is not None:
            for future in pending:
                future.cancel()
            failed.result()
        stream_facts = tuple(future.result() for future in futures)
    return WarehouseCoverage(
        generated_at=generated_at,
        evaluated_through_day=today,
        lanes=tuple(
            _lane_coverage(
                lane=lane,
                tier=tier,
                today=today,
                days=days,
            )
            for (_index, lane), facts in zip(jobs, stream_facts, strict=True)
            for tier, days in zip(ZOOM_TIERS, facts, strict=True)
        ),
    )


class CoverageCache:
    """One memoized census, reused for `ttl_seconds`; the whole reason coverage is affordable."""

    def __init__(self, ttl_seconds: int = CENSUS_CACHE_SECONDS) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._held: WarehouseCoverage | None = None
        self._last_failure: Exception | None = None
        # `threading`, not `asyncio`: `get` runs inside the route's worker thread. Without it, every
        # cold page load in a burst started its own all-stream walk -- the cost the memo exists to
        # avoid, and the guard `environmental-read-model.getSliderCapabilities` already has.
        self._refreshing = threading.Lock()

    def get(self, listing: WarehouseListing, *, lanes: Sequence[CensusLane], now: datetime) -> WarehouseCoverage:
        """Return the held census while it is fresh, else compute one under a single-flight lock."""
        fresh = self._fresh(now)
        if fresh is not None:
            return fresh
        waited_for_refresh = not self._refreshing.acquire(blocking=False)
        if waited_for_refresh:
            self._refreshing.acquire()
        try:
            fresh = self._fresh(now)
            if fresh is not None:
                return fresh
            if waited_for_refresh and self._last_failure is not None:
                raise self._last_failure
            self._last_failure = None
            try:
                built = build_coverage(listing, lanes=lanes, generated_at=now)
            except Exception as exc:
                self._last_failure = exc
                raise
            self._held = built
            return built
        finally:
            self._refreshing.release()

    def clear(self) -> None:
        """Drop the held census; a test that changes the warehouse under it needs this."""
        self._held = None
        self._last_failure = None

    def _fresh(self, now: datetime) -> WarehouseCoverage | None:
        """Return the held census only while it is inside its TTL."""
        held = self._held
        if held is not None and now - held.generated_at < self._ttl:
            return held
        return None


@dataclass
class _BudgetedListing:
    """A `WarehouseListing` spending ONE key budget across every listing of a single census."""

    inner: WarehouseListing
    budget: _CensusKeyBudget

    def iter_tier_keys(self, layer: str, kind: PartitionKind, tier: ZoomTier) -> Iterator[str]:
        """Yield one tier, charging each key before the concurrent census retains it."""
        for key in self.inner.iter_tier_keys(layer, kind, tier):
            self.budget.spend(1)
            yield key

    def iter_stream_keys(self, layer: str, kind: PartitionKind) -> Iterator[str]:
        """Yield one stream, charging every retained key against the aggregate census budget."""
        for key in self.inner.iter_stream_keys(layer, kind):
            self.budget.spend(1)
            yield key

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
        if year is None and month is None:
            return tuple(self.iter_tier_keys(layer, kind, tier))
        keys = self.inner.list_keys(layer, kind, tier, year=year, month=month)
        self.budget.spend(len(keys))
        return keys

    def read_object(self, relative_key: str) -> bytes | None:
        """Delegate; markers are read one at a time and are not what the budget is about."""
        return self.inner.read_object(relative_key)


class _CensusKeyBudget:
    """One thread-safe aggregate key allowance shared by all concurrent lane listings."""

    def __init__(self, remaining: int) -> None:
        self._remaining = remaining
        self._lock = threading.Lock()

    def spend(self, count: int) -> None:
        """Charge retained keys and refuse before the aggregate can grow past its ceiling."""
        with self._lock:
            self._remaining -= count
            if self._remaining < 0:
                raise faults.census_budget_exhausted(listed_keys=MAX_CENSUS_LISTED_KEYS)


def _tier_days(
    listing: WarehouseListing,
    *,
    lane: CensusLane,
    tier: ZoomTier,
) -> _LaneDays:
    """List and classify one tier without retaining its object keys after the result is known."""
    keys = listing.list_keys(lane.layer, lane.kind, tier)
    statuses = day_status_sets(keys, layer=lane.layer, kind=lane.kind, tier=tier)
    # Only a completed, conflict-free partition is readable. Conflict and incomplete days may hold
    # objects, but serving refuses them, so they cannot prove a slider capability safe to publish.
    return _LaneDays(data=statuses.data, absent=statuses.absent, conflict=statuses.conflict)


def _stream_tier_days(
    listing: WarehouseListing,
    *,
    lane: CensusLane,
) -> tuple[_LaneDays, ...]:
    """List one physical stream once, then classify its four exact rungs locally."""
    keys = tuple(listing.iter_stream_keys(lane.layer, lane.kind))
    return tuple(_tier_days_from_keys(keys, lane=lane, tier=tier) for tier in ZOOM_TIERS)


def _tier_days_from_keys(
    keys: tuple[str, ...],
    *,
    lane: CensusLane,
    tier: ZoomTier,
) -> _LaneDays:
    """Classify one rung from a validated stream listing."""
    prefix = zoom_prefix(lane.layer, lane.kind, tier)
    tier_keys = tuple(key for key in keys if key.startswith(prefix))
    statuses = day_status_sets(tier_keys, layer=lane.layer, kind=lane.kind, tier=tier)
    return _LaneDays(data=statuses.data, absent=statuses.absent, conflict=statuses.conflict)


def _owed_but_unwritten(
    accounted: set[date],
    *,
    lane: CensusLane,
    today: date,
    conflict_days: set[date] | None = None,
) -> tuple[date, ...]:
    """Return the days this lane OWED and did not deliver, walking its own cadence from the days it did.

    A daily-series gap is each missing observation day. A release-series gap is each day the bounded
    release reader cannot answer: a published or governed-absence release carries for at most one
    cadence interval, while an owed-but-missing release leaves the whole interval uncovered.
    """
    if _uses_bounded_release_carry(lane):
        return _release_uncovered_days(
            accounted,
            conflict_days=set() if conflict_days is None else conflict_days,
            lane=lane,
            today=today,
        )
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


def _uses_bounded_release_carry(lane: CensusLane) -> bool:
    return lane.nature == "release_series" and lane.layer in BOUNDED_CARRY_RELEASE_LAYERS


def _release_carried_days(
    releases: set[date],
    *,
    lane: CensusLane,
    today: date,
    latest_status_day: date,
) -> set[date]:
    """Expand historical releases by cadence and the latest stored status by its live allowance."""
    historical_carry_days = max(lane.cadence_days - 1, 0)
    return {
        release_day + timedelta(days=offset)
        for release_day in releases
        for offset in range(
            (LATEST_RELEASE_CARRY_DAYS if release_day == latest_status_day else historical_carry_days) + 1
        )
        if release_day + timedelta(days=offset) <= today
    }


def _release_uncovered_days(
    accounted: set[date],
    *,
    conflict_days: set[date],
    lane: CensusLane,
    today: date,
) -> tuple[date, ...]:
    """Return each day the release reader cannot answer under historical and live carry limits."""
    status_days = accounted | conflict_days
    if not status_days:
        return ()
    carried = _release_carried_days(
        accounted,
        lane=lane,
        today=today,
        latest_status_day=max(status_days),
    )
    first_status_day = min(status_days)
    return tuple(
        first_status_day + timedelta(days=offset)
        for offset in range((today - first_status_day).days + 1)
        if first_status_day + timedelta(days=offset) not in carried
    )


def _bounded(
    lane: CensusLane,
    *,
    tier: ZoomTier,
    earliest_day: date | None,
    latest_day: date | None,
    published_ranges: tuple[DayRange, ...],
) -> LaneCoverage:
    """One lane's census with bounds and NO ranges: nothing between those bounds was ever owed."""
    return LaneCoverage(
        layer=lane.layer,
        nature=lane.nature,
        kind=lane.kind,
        zoom=tier,
        earliest_day=earliest_day,
        latest_day=latest_day,
        published_ranges=published_ranges,
        gap_ranges=(),
        governed_absence_ranges=(),
    )
