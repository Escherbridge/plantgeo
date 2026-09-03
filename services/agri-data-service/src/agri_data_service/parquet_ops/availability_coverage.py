"""Lane coverage from the published availability index: one pointer GET, one bounded generation GET."""

from __future__ import annotations

import threading
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from datetime import UTC, timedelta
from typing import TYPE_CHECKING, Final, Literal

import structlog

from agri_data_service.foundation.parquet.lane_contract import nature_has_time_axis
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.parquet_ops.coverage import LaneDays, close_lane_coverage
from agri_data_service.parquet_ops.snapshot_products import (
    FORWARD_PARTITION_KIND,
    ForwardAvailability,
    ForwardAvailabilityWithheld,
)
from agri_data_service.parquet_ops.wire import (
    COVERAGE_AUTHORITY_AVAILABILITY,
    WITHHELD_AVAILABILITY_CHECKSUM_INVALID,
    WITHHELD_AVAILABILITY_MALFORMED,
    WITHHELD_AVAILABILITY_STALE,
    WITHHELD_AVAILABILITY_UNPUBLISHED,
    LaneCoverage,
)
from agri_data_service.pipeline.parquet.availability_index import (
    AvailabilityChecksumError,
    AvailabilityMalformedError,
    AvailabilityUnavailableError,
    BotoAvailabilityStorage,
    availability_pointer_key,
    read_bootstrap_marker,
    read_latest_availability,
)
from agri_data_service.pipeline.parquet.lane_ceiling import allowed_source_ceiling
from agri_data_service.pipeline.parquet.objectstore import availability_lane_root
from agri_data_service.warehouse.schemas.availability_index import AVAILABILITY_REQUIRED_RUNGS

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date, datetime

    from agri_data_service.config import CoverageAuthorityPolicy, Settings
    from agri_data_service.parquet_ops.coverage import CensusLane
    from agri_data_service.parquet_ops.wire import CoverageWithholding
    from agri_data_service.pipeline.parquet.availability_index import (
        AvailabilityIndex,
        AvailabilityNature,
        AvailabilityStorage,
        StoredAvailabilityObject,
    )

logger = structlog.get_logger()

#: How long one lane's pointer answer is reused before it is re-fetched. The pointer is MUTABLE, so
#: this is the whole staleness budget of the availability path; the generation it names is content
#: addressed and therefore cached without expiry. `BotoAvailabilityStorage.read` is an unconditional
#: GET -- there is no `If-None-Match` on the storage protocol -- so revalidation is this TTL and not
#: an ETag exchange. Keep it at or under 60 s: the runbook's steady-state promise is one tiny GET.
POINTER_REVALIDATE_SECONDS: Final = 60

#: A generation key contains its own content digest, so a hit can never be a stale hit. The two
#: bounds are about MEMORY, not correctness: `GENERATION_MAX_BYTES` allows 256 MiB per object and a
#: process holding one of those per lane would be the ceiling incident again, in a different costume.
MAX_CACHED_GENERATION_BYTES: Final = 8 * 1024 * 1024
MAX_CACHED_GENERATIONS: Final = 32

#: Availability reads fan out no wider than the census listing they replace.
AVAILABILITY_LANE_WORKERS: Final = 3

#: How far a lane's PUBLISHED ceiling may sit behind the ceiling its registration allows before the
#: index is withheld as stale rather than trusted as merely quiet. The tolerance is one whole
#: publication period -- `cadence_days + publication_lag_days` -- plus this grace, because a lane's
#: ceiling only advances when a day is actually published: a release lane whose upstream skipped one
#: issue is quiet, and a lane that has missed a period AND the grace on top of it has a publisher
#: that stopped. Too tight a value greys out healthy lanes on a single missed cron tick, which is
#: strictly worse than serving a horizon a few days old -- that horizon is itself on the wire as
#: `source_ceiling_day`, so a client can judge it.
AVAILABILITY_STALE_GRACE_DAYS: Final = 3

_GENERATION_KEY_MARKER: Final = "/availability/generation="

#: What one lane owes before any object is touched.
_LanePlan = Literal["read", "census"]


def lane_root(lane: CensusLane) -> str:
    """Return the physical `layer=<slug>/kind=<kind>` root the lane's availability artifacts sit under.

    Delegated to `availability_lane_root`, which is the publication side's OWN spelling of this root,
    so a bootstrap and a read can never disagree about which lane they mean. It derives from the same
    `stream_prefix` the warehouse writes and the census lists, minus its trailing separator.
    """
    return availability_lane_root(lane.layer, lane.kind)


def required_source_ceiling(lane: CensusLane, *, now: datetime) -> date:
    """Return the oldest ceiling this lane's index may publish and still be believed."""
    tolerance = lane.cadence_days + lane.publication_lag_days + AVAILABILITY_STALE_GRACE_DAYS
    return allowed_source_ceiling(lane, today=now.astimezone(UTC).date()) - timedelta(days=tolerance)


@dataclass(frozen=True, slots=True)
class LaneWithholding:
    """One lane that publishes no selectable days, and the exact reason it may not."""

    layer: str
    kind: str
    reason: CoverageWithholding
    detail: str


@dataclass(frozen=True, slots=True)
class AvailabilityResolution:
    """Which lanes the index answered for, and which still owe a listing census."""

    rows_by_lane: Mapping[tuple[str, str], tuple[LaneCoverage, ...]]
    census_lanes: tuple[CensusLane, ...]
    withheld: tuple[LaneWithholding, ...]

    @property
    def lanes(self) -> tuple[LaneCoverage, ...]:
        """Every availability-proven and withheld rung row, in lane-then-rung order."""
        return tuple(row for rows in self.rows_by_lane.values() for row in rows)


class AvailabilityCoverageReader:
    """One process-held availability reader: pointers revalidated on a TTL, generations kept by sha."""

    def __init__(
        self,
        store: AvailabilityStorage,
        *,
        pointer_ttl_seconds: int = POINTER_REVALIDATE_SECONDS,
    ) -> None:
        self._store = GenerationCachingStorage(inner=store)
        self._ttl = timedelta(seconds=pointer_ttl_seconds)
        self._held: dict[str, tuple[datetime, AvailabilityIndex]] = {}
        #: Lane roots proven to carry a bootstrap receipt. A marker is IMMUTABLE, so a positive
        #: answer can never go stale and is worth holding; a negative one is not, because the lane
        #: it describes may be bootstrapped at any moment.
        self._bootstrapped: set[str] = set()
        # `threading`, not `asyncio`: reads run inside the coverage build's worker threads.
        self._lock = threading.Lock()

    def read(self, lane: CensusLane, *, now: datetime) -> AvailabilityIndex:
        """Return one lane's verified index, re-reading its pointer only once the TTL has run out.

        The staleness test is applied where the pointer is FETCHED and then reused for at most one
        TTL. That is deliberate: within 60 s the lane's own allowed ceiling cannot have moved by a
        day, so re-testing a held index would be the same comparison against the same operands.
        """
        return self.read_lane_root(
            lane_root=lane_root(lane),
            lane=lane.layer,
            nature=_availability_nature(lane),
            oldest_believable_ceiling=required_source_ceiling(lane, now=now),
            now=now,
        )

    def read_lane_root(  # one lane-identity coordinate per arg, none foldable
        self,
        *,
        lane_root: str,
        lane: str,
        nature: AvailabilityNature,
        oldest_believable_ceiling: date | None,
        now: datetime,
    ) -> AvailabilityIndex:
        """Read one lane root's index by its physical coordinates, with no registration record needed.

        The seam a caller that holds no `CensusLane` reaches for -- `snapshot_products`' forward half
        is keyed by product, not by registration, and must not import the census's lane record to ask
        this question.
        """
        held = self._fresh(lane_root, now)
        if held is not None:
            return held
        index = read_latest_availability(
            self._store,
            lane_root=lane_root,
            expected_lane=lane,
            expected_nature=nature,
            expected_required_rungs=AVAILABILITY_REQUIRED_RUNGS,
            required_source_ceiling=oldest_believable_ceiling,
        )
        with self._lock:
            self._held[lane_root] = (now, index)
        return index

    def was_bootstrapped(self, root: str) -> bool:
        """Report whether this lane ever published a bootstrap, from ONE GET at a deterministic key.

        A lane that answers `True` here and has no pointer has LOST its head; a transitional census
        that fell back to a listing for it would quietly re-prove by scan the very lane the artifact
        exists to retire, and would go on doing so forever without anyone being told.
        """
        with self._lock:
            if root in self._bootstrapped:
                return True
        found = read_bootstrap_marker(self._store, lane_root=root) is not None
        if found:
            # Positive only: a bootstrap marker is immutable, so a `True` can never go stale, while a
            # `False` is a lane that may be bootstrapped at any moment.
            with self._lock:
                self._bootstrapped.add(root)
        return found

    def clear(self) -> None:
        """Drop every held pointer and generation; a test that rewrites the store under it needs this."""
        with self._lock:
            self._held.clear()
            self._bootstrapped.clear()
        self._store.clear()

    def _fresh(self, root: str, now: datetime) -> AvailabilityIndex | None:
        with self._lock:
            held = self._held.get(root)
        if held is not None and now - held[0] < self._ttl:
            return held[1]
        return None


class AvailabilityCoverageReaderHolder:
    """One reader per process, built from settings on first use so an unread setting costs nothing."""

    def __init__(self) -> None:
        self._held: AvailabilityCoverageReader | None = None

    def get(self, source: Settings | None = None) -> AvailabilityCoverageReader:
        """Return the held reader, building its conditional S3 adapter without network I/O."""
        if self._held is None:
            self._held = AvailabilityCoverageReader(BotoAvailabilityStorage.from_settings(source))
        return self._held


@dataclass(frozen=True, slots=True)
class SnapshotForwardAvailability:
    """Prove a snapshot product's FORWARD half from its own availability index, never by listing.

    The port `parquet_ops/snapshot_products.py` declares and this module implements, wired that way
    round because `coverage.py` already imports `snapshot_products` -- an import back the other way
    at module scope would close the cycle.
    """

    reader: AvailabilityCoverageReader
    now: datetime

    def forward_days(self, *, layer: str, first_day: date) -> ForwardAvailability | ForwardAvailabilityWithheld:
        """Return the product's forward-half day sets, or the exact reason none may be published."""
        root = availability_lane_root(layer, FORWARD_PARTITION_KIND)
        try:
            index = self.reader.read_lane_root(
                lane_root=root,
                lane=layer,
                nature="daily_series",
                # A product's forward half has no registration to read a cadence off, so it is judged
                # on presence alone; the row still carries `source_ceiling_day` for a client to see.
                oldest_believable_ceiling=None,
                now=self.now,
            )
        except AvailabilityChecksumError as exc:
            return ForwardAvailabilityWithheld(reason=WITHHELD_AVAILABILITY_CHECKSUM_INVALID, detail=str(exc))
        except AvailabilityMalformedError as exc:
            return ForwardAvailabilityWithheld(reason=WITHHELD_AVAILABILITY_MALFORMED, detail=str(exc))
        except AvailabilityUnavailableError as exc:
            reason: CoverageWithholding = (
                WITHHELD_AVAILABILITY_UNPUBLISHED if exc.code == "availability_missing" else WITHHELD_AVAILABILITY_STALE
            )
            return ForwardAvailabilityWithheld(reason=reason, detail=str(exc))
        ceiling = index.pointer.source_ceiling
        selectable = frozenset(day for day in index.selectable_days() if first_day <= day <= ceiling)
        published = frozenset(
            row.day for row in index.rows if row.day in selectable and row.terminal_state == "published"
        )
        return ForwardAvailability(
            published_days=published,
            absent_days=selectable - published,
            source_ceiling=ceiling,
            generation_sha256=index.pointer.generation_sha256,
            pointer_key=availability_pointer_key(root),
        )


def resolve_availability_lanes(
    reader: AvailabilityCoverageReader,
    *,
    lanes: Sequence[CensusLane],
    policy: CoverageAuthorityPolicy,
    now: datetime,
) -> AvailabilityResolution:
    """Answer every lane it can from its index and name what the remaining lanes still owe.

    Performs NO object listing on any path. A lane that raises anything other than the four
    availability refusals propagates: an unclassified transport fault is not evidence about content,
    and the census this replaces fails the whole answer for the same reason.
    """
    plans = tuple((lane, _lane_plan(lane)) for lane in lanes)
    to_read = tuple(lane for lane, plan in plans if plan == "read")
    outcomes = _read_lanes(reader, lanes=to_read, policy=policy, now=now)
    rows_by_lane: dict[tuple[str, str], tuple[LaneCoverage, ...]] = {}
    census_lanes: list[CensusLane] = []
    withheld: list[LaneWithholding] = []
    for lane, plan in plans:
        if plan == "census":
            # A `static_lookup` under EITHER policy: RUNBOOK layer-lanes 4a gives an index to every
            # TIME-BEARING lane, and a version stamp is not a time axis.
            census_lanes.append(lane)
            continue
        outcome = outcomes[_lane_key(lane)]
        if outcome.census:
            census_lanes.append(lane)
            continue
        rows_by_lane[_lane_key(lane)] = outcome.rows
        if outcome.withholding is not None:
            withheld.append(outcome.withholding)
    return AvailabilityResolution(
        rows_by_lane=rows_by_lane,
        census_lanes=tuple(census_lanes),
        withheld=tuple(withheld),
    )


def merge_direct_lane_rows(
    *,
    lanes: Sequence[CensusLane],
    resolution: AvailabilityResolution,
    census_rows: Sequence[LaneCoverage],
) -> tuple[LaneCoverage, ...]:
    """Interleave availability and census rows back into registration order, one source per lane."""
    census_by_lane: dict[tuple[str, str], list[LaneCoverage]] = {}
    for row in census_rows:
        census_by_lane.setdefault((row.layer, row.kind), []).append(row)
    merged: list[LaneCoverage] = []
    for lane in lanes:
        key = _lane_key(lane)
        available = resolution.rows_by_lane.get(key)
        merged.extend(available if available is not None else census_by_lane.get(key, ()))
    return tuple(merged)


def lane_coverage_from_index(index: AvailabilityIndex, *, lane: CensusLane) -> tuple[LaneCoverage, ...]:
    """Close one lane's four rung rows against its index, its cadence and its own source ceiling.

    Every rung reports the lane's SELECTABLE days -- the days whose whole authoritative rung set
    agrees on one terminal state -- and not that rung's own rows. The intersection is a subset of
    each rung, so no row over-claims, and a slider can never mount an axis at z13 over a day z0
    cannot draw.
    """
    ceiling = index.pointer.source_ceiling
    selectable = frozenset(day for day in index.selectable_days() if day <= ceiling)
    published = frozenset(row.day for row in index.rows if row.day in selectable and row.terminal_state == "published")
    days = LaneDays(data=published, absent=selectable - published, conflict=frozenset())
    pointer_key = availability_pointer_key(lane_root(lane))
    return tuple(
        replace(
            close_lane_coverage(
                lane=lane,
                tier=tier,
                horizon=ceiling,
                days=days,
                # The publisher already subtracted this lane's publication lag when it declared the
                # ceiling; charging it again would hide one lag period of the real gap tail.
                horizon_already_lag_adjusted=True,
            ),
            coverage_authority=COVERAGE_AUTHORITY_AVAILABILITY,
            availability_generation_sha256=index.pointer.generation_sha256,
            availability_pointer_key=pointer_key,
            source_ceiling_day=ceiling,
            required_rungs=tuple(index.pointer.required_rungs),
        )
        for tier in ZOOM_TIERS
    )


def withheld_lane_coverage(lane: CensusLane, *, reason: CoverageWithholding) -> tuple[LaneCoverage, ...]:
    """Publish one lane's rungs with NO selectable days and the exact reason none may be published."""
    pointer_key = availability_pointer_key(lane_root(lane))
    return tuple(
        LaneCoverage(
            layer=lane.layer,
            nature=lane.nature,
            kind=lane.kind,
            zoom=tier,
            earliest_day=None,
            latest_day=None,
            published_ranges=(),
            gap_ranges=(),
            governed_absence_ranges=(),
            coverage_authority=COVERAGE_AUTHORITY_AVAILABILITY,
            availability_pointer_key=pointer_key,
            withheld_reason=reason,
        )
        for tier in ZOOM_TIERS
    )


@dataclass(frozen=True, slots=True)
class _LaneOutcome:
    """One lane's verdict: its rung rows, or that the lane still owes a listing census."""

    rows: tuple[LaneCoverage, ...]
    withholding: LaneWithholding | None
    census: bool


def _read_lanes(
    reader: AvailabilityCoverageReader,
    *,
    lanes: Sequence[CensusLane],
    policy: CoverageAuthorityPolicy,
    now: datetime,
) -> dict[tuple[str, str], _LaneOutcome]:
    """Read every lane's index on a bounded pool, cancelling the rest on the first hard fault."""
    if not lanes:
        return {}
    with ThreadPoolExecutor(
        max_workers=min(AVAILABILITY_LANE_WORKERS, len(lanes)),
        thread_name_prefix="parquet-availability-read",
    ) as pool:
        futures = tuple(pool.submit(_read_lane, reader, lane=lane, policy=policy, now=now) for lane in lanes)
        done, pending = wait(futures, return_when=FIRST_EXCEPTION)
        failed = next((future for future in done if future.exception() is not None), None)
        if failed is not None:
            for future in pending:
                future.cancel()
            failed.result()
        return {_lane_key(lane): future.result() for lane, future in zip(lanes, futures, strict=True)}


def _read_lane(
    reader: AvailabilityCoverageReader,
    *,
    lane: CensusLane,
    policy: CoverageAuthorityPolicy,
    now: datetime,
) -> _LaneOutcome:
    """Read one lane's index, or classify the refusal into a withholding or a transitional census."""
    try:
        index = reader.read(lane, now=now)
    except AvailabilityChecksumError as exc:
        return _withheld(lane, reason=WITHHELD_AVAILABILITY_CHECKSUM_INVALID, detail=str(exc))
    except AvailabilityMalformedError as exc:
        return _withheld(lane, reason=WITHHELD_AVAILABILITY_MALFORMED, detail=str(exc))
    except AvailabilityUnavailableError as exc:
        if exc.code == "availability_missing":
            return _no_pointer(reader, lane, policy=policy, detail=str(exc))
        return _withheld(lane, reason=WITHHELD_AVAILABILITY_STALE, detail=str(exc))
    return _LaneOutcome(rows=lane_coverage_from_index(index, lane=lane), withholding=None, census=False)


def _no_pointer(
    reader: AvailabilityCoverageReader,
    lane: CensusLane,
    *,
    policy: CoverageAuthorityPolicy,
    detail: str,
) -> _LaneOutcome:
    """Decide what a lane with NO pointer owes, which turns on whether it was ever bootstrapped.

    Corruption never falls back -- a lane whose bytes disagree with their receipt would otherwise be
    quietly re-proven by the scan the artifact exists to retire -- and NEITHER does a lane that was
    bootstrapped and then lost its head. Only a lane with no availability history at all may be
    censused, and even that is logged, because the bridge is meant to empty rather than to persist.
    """
    root = lane_root(lane)
    try:
        bootstrapped = reader.was_bootstrapped(root)
    except AvailabilityMalformedError as exc:
        # A marker whose bytes are not the frozen shape settles nothing, and settling nothing is not
        # permission to scan: corruption never falls back, here as everywhere else on this path.
        return _withheld(lane, reason=WITHHELD_AVAILABILITY_MALFORMED, detail=str(exc))
    if bootstrapped:
        logger.warning(
            "availability_pointer_lost",
            lane_root=root,
            layer=lane.layer,
            kind=lane.kind,
            reason=(
                "this lane carries an immutable bootstrap receipt and no pointer, so its head is LOST; "
                "it is withheld rather than re-proven by the listing census the index replaced"
            ),
        )
        return _withheld(lane, reason=WITHHELD_AVAILABILITY_UNPUBLISHED, detail=detail)
    if policy == "census_until_bootstrap":
        logger.warning(
            "availability_census_fallback",
            lane_root=root,
            layer=lane.layer,
            kind=lane.kind,
            reason="no bootstrap receipt and no pointer, so this lane still costs a whole-stream listing",
        )
        return _LaneOutcome(rows=(), withholding=None, census=True)
    return _withheld(lane, reason=WITHHELD_AVAILABILITY_UNPUBLISHED, detail=detail)


def _withheld(lane: CensusLane, *, reason: CoverageWithholding, detail: str) -> _LaneOutcome:
    return _LaneOutcome(
        rows=withheld_lane_coverage(lane, reason=reason),
        withholding=LaneWithholding(layer=lane.layer, kind=lane.kind, reason=reason, detail=detail),
        census=False,
    )


def _lane_plan(lane: CensusLane) -> _LanePlan:
    """Decide what one lane owes before any object is touched.

    A `static_lookup`'s partition day is a VERSION STAMP, so it has no time axis and never publishes
    an availability index -- RUNBOOK `layer-lanes.md` 4a gives one to every TIME-BEARING lane. It
    therefore stays on the census under BOTH policies rather than being withheld: withholding it
    would strip a published reference set out of coverage entirely to buy a listing the mode's
    zero-LIST promise was never about.
    """
    return "read" if nature_has_time_axis(lane.nature) else "census"


def _lane_key(lane: CensusLane) -> tuple[str, str]:
    return (lane.layer, lane.kind)


def _availability_nature(lane: CensusLane) -> AvailabilityNature:
    """Narrow a census lane's nature to the two the availability contract covers."""
    if lane.nature == "daily_series":
        return "daily_series"
    if lane.nature == "release_series":
        return "release_series"
    raise ValueError(f"lane {lane.layer!r} has no time axis and cannot own an availability index")


class GenerationCachingStorage:
    """An `AvailabilityStorage` that re-fetches pointers and keeps content-addressed generations.

    Read-only by construction: coverage is a reader, and a writer reached through this wrapper would
    publish generations that never entered the cache and pointers this process still believed stale.
    """

    def __init__(
        self,
        *,
        inner: AvailabilityStorage,
        max_entry_bytes: int = MAX_CACHED_GENERATION_BYTES,
        max_entries: int = MAX_CACHED_GENERATIONS,
    ) -> None:
        self.inner = inner
        self.max_entry_bytes = max_entry_bytes
        self.max_entries = max_entries
        self._objects: dict[str, StoredAvailabilityObject] = {}
        self._lock = threading.Lock()

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        """Serve an immutable generation from memory; always re-fetch the mutable pointer."""
        if _GENERATION_KEY_MARKER not in key:
            return self.inner.read(key, max_bytes=max_bytes)
        with self._lock:
            cached = self._objects.get(key)
        if cached is not None:
            return cached
        stored = self.inner.read(key, max_bytes=max_bytes)
        if stored is not None and len(stored.payload) <= self.max_entry_bytes:
            self._remember(key, stored)
        return stored

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        """Refuse: the coverage reader must never publish."""
        del payload, content_type
        raise NotImplementedError(f"the coverage availability reader is read-only and may not write {key!r}")

    def compare_and_swap(
        self,
        key: str,
        payload: bytes,
        *,
        expected_etag: str | None,
        content_type: str,
    ) -> bool:
        """Refuse: the coverage reader must never advance a pointer."""
        del payload, expected_etag, content_type
        raise NotImplementedError(f"the coverage availability reader is read-only and may not advance {key!r}")

    def clear(self) -> None:
        """Drop every held generation."""
        with self._lock:
            self._objects.clear()

    def _remember(self, key: str, stored: StoredAvailabilityObject) -> None:
        with self._lock:
            if len(self._objects) >= self.max_entries:
                self._objects.pop(next(iter(self._objects)))
            self._objects[key] = stored
