"""Availability-authorized row serving for every time-bearing lane.

See `AGENTS.md`, "Availability-authorized serving".
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Final, cast

from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.paths import (
    absence_marker_path,
    try_parse_absence_marker_path,
    try_parse_partition_path,
)
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.availability_coverage import (
    GenerationCachingStorage,
    required_source_ceiling,
)
from agri_data_service.parquet_ops.coverage import (
    DEDICATED_SLIDER_PRODUCT_LAYERS,
    census_lane_from_registration,
)
from agri_data_service.parquet_ops.serving import resolve_day, resolve_release, resolve_window
from agri_data_service.pipeline.parquet.availability_index import (
    EVIDENCE_OBJECT_MAX_BYTES,
    AvailabilityChecksumError,
    AvailabilityError,
    AvailabilityMalformedError,
    AvailabilityUnavailableError,
    BotoAvailabilityStorage,
    read_availability_pointer,
    read_latest_availability,
    read_terminal_evidence,
)
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import availability_lane_root
from agri_data_service.warehouse.schemas.availability_index import AVAILABILITY_REQUIRED_RUNGS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from agri_data_service.config import Settings
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.parquet_ops.duckdb_session import ServingSession
    from agri_data_service.parquet_ops.mtbs_snapshot_catalog import VerifiedMtbsSnapshot
    from agri_data_service.parquet_ops.request_params import ReadScope
    from agri_data_service.parquet_ops.warehouse_reader import (
        PartitionRowReader,
        RowRead,
        RowReadResult,
        WarehouseListing,
    )
    from agri_data_service.parquet_ops.wire import DayEnvelope
    from agri_data_service.pipeline.parquet.availability_index import (
        AvailabilityIndex,
        AvailabilityNature,
        AvailabilityRow,
        AvailabilityStorage,
        EvidenceReceipt,
    )

_LANES: Final = {slug: census_lane_from_registration(registration) for slug, registration in LANE_REGISTRY.items()}
_UNREGISTERED_PRODUCTS: Final = set(DEDICATED_SLIDER_PRODUCT_LAYERS) - _LANES.keys()
if _UNREGISTERED_PRODUCTS:
    raise ValueError("serving products lack a registered history floor: " + ", ".join(sorted(_UNREGISTERED_PRODUCTS)))

# One part is bounded independently, then the request is bounded across every part it selected.
# These are serving-resource guards, not publication limits: an oversized authorized object is
# refused rather than downloaded without a ceiling merely because its receipt is otherwise valid.
MAX_VERIFIED_PART_BYTES: Final = 64 * 1024 * 1024
MAX_VERIFIED_READ_BYTES: Final = 512 * 1024 * 1024
MAX_VERIFIED_MARKER_BYTES: Final = 1024 * 1024
MAX_VERIFIED_MARKERS_BYTES: Final = 8 * 1024 * 1024
MAX_VERIFIED_AVAILABILITY_INDEXES: Final = 16
MAX_CACHED_INDEX_GENERATION_BYTES: Final = 8 * 1024 * 1024
MAX_CACHED_INDEX_BYTES: Final = 16 * 1024 * 1024
MAX_CACHED_INDEX_ROWS: Final = 100_000


@dataclass(slots=True)
class AvailabilityAuthorizedListing:
    """A listing-shaped view containing only objects named by one verified generation."""

    index: AvailabilityIndex
    scope: ReadScope
    store: AvailabilityStorage
    physical: WarehouseListing
    _receipts: dict[str, EvidenceReceipt] = field(init=False, repr=False)
    _keys_by_day: dict[date, tuple[str, ...]] = field(init=False, repr=False)
    _rows_by_day: dict[date, AvailabilityRow] = field(init=False, repr=False)
    _absence_receipts: dict[date, EvidenceReceipt] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._receipts = {}
        self._keys_by_day = {}
        self._rows_by_day = {}
        self._absence_receipts = {}
        for row in self.index.rows:
            if row.rung != self.scope.tier:
                continue
            self._rows_by_day[row.day] = row
            receipts = list(row.data_receipts)
            if row.completion_receipt is not None:
                receipts.append(row.completion_receipt)
            for receipt in receipts:
                self._receipts[receipt.key] = receipt
            keys = tuple(receipt.key for receipt in receipts)
            if row.terminal_state == "governed_absence" and not keys:
                keys = (absence_marker_path(self.scope.layer, self.scope.kind, self.scope.tier, row.day),)
            self._keys_by_day[row.day] = keys

    def list_keys(
        self,
        layer: str,
        kind: PartitionKind,
        tier: ZoomTier,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> tuple[str, ...]:
        """Return only generation-bound objects matching the requested physical coordinates."""
        if (layer, kind, tier) != (self.scope.layer, self.scope.kind, self.scope.tier):
            return ()
        return tuple(
            key
            for day in sorted(self._rows_by_day)
            if (year is None or day.year == year) and (month is None or day.month == month)
            for key in self._day_keys(day)
        )

    def _day_keys(self, day: date) -> tuple[str, ...]:
        """Return generation-derived layout names without opening their evidence objects."""
        return self._keys_by_day[day]

    def _absence_receipt(self, day: date) -> EvidenceReceipt:
        """Verify and cache the selected absence receipt, never an unrelated day's evidence."""
        cached = self._absence_receipts.get(day)
        if cached is not None:
            return cached
        row = self._rows_by_day[day]
        try:
            terminal = read_terminal_evidence(self.store, row.terminal_receipt, identity=self.index.pointer.identity)
        except AvailabilityError as exc:
            raise _availability_refusal(self.scope.layer, exc) from exc
        if terminal.absence_receipt is None:
            raise faults.availability_malformed(
                layer=self.scope.layer,
                detail=f"{row.day.isoformat()} z{row.rung} has no absence receipt",
            )
        expected_key = absence_marker_path(self.scope.layer, self.scope.kind, self.scope.tier, day)
        if terminal.absence_receipt.key != expected_key:
            raise faults.availability_malformed(
                layer=self.scope.layer,
                detail=f"{day.isoformat()} z{row.rung} absence receipt names an unexpected object",
            )
        self._absence_receipts[day] = terminal.absence_receipt
        self._receipts[expected_key] = terminal.absence_receipt
        return terminal.absence_receipt

    def iter_tier_keys(self, layer: str, kind: PartitionKind, tier: ZoomTier) -> Iterator[str]:
        if (layer, kind, tier) != (self.scope.layer, self.scope.kind, self.scope.tier):
            return
        for day in sorted(self._keys_by_day):
            yield from self._keys_by_day[day]

    def iter_stream_keys(self, layer: str, kind: PartitionKind) -> Iterator[str]:
        if (layer, kind) == (self.scope.layer, self.scope.kind):
            yield from self.list_keys(layer, kind, self.scope.tier)

    def read_object(self, relative_key: str) -> bytes | None:
        """Read and re-check an authorized non-Parquet object by its recorded digest."""
        receipt = self._receipts.get(relative_key)
        if receipt is None:
            parsed = try_parse_absence_marker_path(relative_key)
            if (
                parsed is not None
                and (parsed.layer, parsed.kind, parsed.zoom) == (self.scope.layer, self.scope.kind, self.scope.tier)
                and parsed.day in self._rows_by_day
                and self._rows_by_day[parsed.day].terminal_state == "governed_absence"
            ):
                receipt = self._absence_receipt(parsed.day)
        if receipt is None:
            return None
        try:
            stored = self.store.read(relative_key, max_bytes=EVIDENCE_OBJECT_MAX_BYTES)
        except AvailabilityError as exc:
            raise _availability_refusal(self.scope.layer, exc) from exc
        if stored is None:
            raise faults.availability_stale(
                layer=self.scope.layer,
                detail=f"authorized object {relative_key!r} is missing",
            )
        if sha256_digest(stored.payload) != receipt.sha256:
            raise faults.availability_checksum_invalid(
                layer=self.scope.layer,
                detail=f"authorized object {relative_key!r} differs from its receipt",
            )
        return stored.payload

    @contextmanager
    def verified_object_uris(
        self,
        keys: Sequence[str],
    ) -> Iterator[dict[str, str]]:
        """Point DuckDB at the exact receipt-bound bytes selected by this generation."""
        data_receipts, completion_receipts = self._selected_read_receipts(keys)
        # Completion is the publication commit point. Verify it before admitting any part bytes;
        # a concurrent rewrite clears it before replacing part-0 and therefore fails closed here.
        self._verify_receipts(
            completion_receipts,
            max_bytes=MAX_VERIFIED_MARKER_BYTES,
            aggregate_bytes=MAX_VERIFIED_MARKERS_BYTES,
        )
        with TemporaryDirectory(prefix="plantgeo-serving-") as directory:
            root = Path(directory)
            exact_sources: dict[str, str] = {}
            total_bytes = 0
            for index, receipt in enumerate(data_receipts):
                payload = self._verified_payload(receipt, max_bytes=MAX_VERIFIED_PART_BYTES)
                total_bytes += len(payload)
                if total_bytes > MAX_VERIFIED_READ_BYTES:
                    raise faults.ServingRefusalError(
                        "read_over_budget",
                        f"Receipt-bound read exceeded its {MAX_VERIFIED_READ_BYTES}-byte ceiling",
                    )
                target = root / f"part-{index:05d}.parquet"
                target.write_bytes(payload)
                del payload
                exact_sources[receipt.key] = target.as_posix()
            yield exact_sources

    @contextmanager
    def verified_session(
        self,
        session: ServingSession,
        keys: Sequence[str],
    ) -> Iterator[ServingSession]:
        """Install receipt-bound local sources on an agent query's serving session."""
        with self.verified_object_uris(keys) as exact_sources:
            yield replace(session, object_uris=exact_sources)

    def _selected_read_receipts(
        self,
        keys: Sequence[str],
    ) -> tuple[tuple[EvidenceReceipt, ...], tuple[EvidenceReceipt, ...]]:
        """Resolve selected parts and their day commit markers from this exact generation."""
        parts: list[EvidenceReceipt] = []
        completions: dict[str, EvidenceReceipt] = {}
        for key in keys:
            receipt = self._receipts.get(key)
            parsed = try_parse_partition_path(key)
            if receipt is None or parsed is None:
                raise faults.availability_unpublished(
                    layer=self.scope.layer,
                    detail=f"row read selected object {key!r} outside the current generation",
                )
            row = self._rows_by_day.get(parsed.day)
            if row is None or row.terminal_state != "published" or receipt not in row.data_receipts:
                raise faults.availability_malformed(
                    layer=self.scope.layer,
                    detail=f"row read selected object {key!r} without a published-day receipt",
                )
            completion = row.completion_receipt
            if completion is None:
                raise faults.availability_malformed(
                    layer=self.scope.layer,
                    detail=f"{parsed.day.isoformat()} z{row.rung} has no completion receipt",
                )
            parts.append(receipt)
            completions[completion.key] = completion
        return tuple(parts), tuple(completions[key] for key in sorted(completions))

    def _verify_receipts(
        self,
        receipts: Sequence[EvidenceReceipt],
        *,
        max_bytes: int,
        aggregate_bytes: int,
    ) -> None:
        """Verify small control objects sequentially and charge them before reading the next."""
        consumed = 0
        for receipt in receipts:
            payload = self._verified_payload(receipt, max_bytes=max_bytes)
            consumed += len(payload)
            if consumed > aggregate_bytes:
                raise faults.ServingRefusalError(
                    "read_over_budget",
                    f"Receipt-bound control objects exceeded their {aggregate_bytes}-byte ceiling",
                )

    def _verified_payload(self, receipt: EvidenceReceipt, *, max_bytes: int) -> bytes:
        """Read one bounded object and return it only when its availability digest matches."""
        try:
            stored = self.store.read(receipt.key, max_bytes=max_bytes)
        except AvailabilityError as exc:
            raise _availability_refusal(self.scope.layer, exc) from exc
        if stored is None:
            raise faults.availability_stale(
                layer=self.scope.layer,
                detail=f"authorized object {receipt.key!r} is missing",
            )
        if sha256_digest(stored.payload) != receipt.sha256:
            raise faults.availability_checksum_invalid(
                layer=self.scope.layer,
                detail=f"authorized object {receipt.key!r} differs from its receipt",
            )
        return stored.payload

    def mtbs_snapshot_loader(self, day: date) -> VerifiedMtbsSnapshot | None:
        """Delegate MTBS proof, but admit only a day present in this exact generation and rung."""
        loader = getattr(self.physical, "mtbs_snapshot_loader", None)
        loader = cast("Callable[[date], VerifiedMtbsSnapshot | None] | None", loader)
        snapshot = None if loader is None else loader(day)
        if snapshot is None:
            return None
        available_day = snapshot.descriptor.available_day
        if available_day not in self._keys_by_day:
            raise faults.availability_unpublished(
                layer=self.scope.layer,
                detail=f"MTBS snapshot {available_day.isoformat()} is absent from the current generation",
            )
        return snapshot


class AuthorizedServingReader:
    """Fresh-pointer reader whose immutable generations alone authorize time-bearing rows."""

    def __init__(self, store: AvailabilityStorage) -> None:
        self._store = GenerationCachingStorage(inner=store)
        self._indexes: dict[str, AvailabilityIndex] = {}
        self._index_cache_bytes = 0
        self._index_cache_rows = 0
        self._index_locks: dict[str, threading.Lock] = {}
        self._cache_lock = threading.Lock()

    def listing(
        self,
        physical: WarehouseListing,
        *,
        scope: ReadScope,
        now: datetime | None = None,
    ) -> WarehouseListing:
        """Return static physical behavior or an availability-bound listing for a time-bearing lane."""
        lane = _LANES.get(scope.layer)
        if lane is None:
            raise faults.availability_unpublished(layer=scope.layer, detail="the lane is not registered")
        if lane.nature == "static_lookup":
            return physical
        nature = lane.nature
        instant = datetime.now(UTC) if now is None else now
        try:
            index = self._read_index(
                nature=nature,
                scope=scope,
                required_ceiling=required_source_ceiling(lane, now=instant),
            )
        except AvailabilityError as exc:
            raise _availability_refusal(scope.layer, exc) from exc
        return AvailabilityAuthorizedListing(index=index, scope=scope, store=self._store, physical=physical)

    def _read_index(
        self,
        *,
        nature: AvailabilityNature,
        scope: ReadScope,
        required_ceiling: date,
    ) -> AvailabilityIndex:
        """Read a fresh pointer while parsing each immutable generation at most once per process."""
        lane_root = availability_lane_root(scope.layer, scope.kind)
        pointer = read_availability_pointer(
            self._store,
            lane_root=lane_root,
            expected_lane=scope.layer,
            expected_nature=nature,
            expected_required_rungs=AVAILABILITY_REQUIRED_RUNGS,
            required_source_ceiling=required_ceiling,
        )
        with self._cache_lock:
            lock = self._index_locks.setdefault(lane_root, threading.Lock())
        with lock:
            with self._cache_lock:
                cached = self._indexes.get(lane_root)
            if cached is not None and cached.pointer == pointer:
                return cached
            index = read_latest_availability(
                self._store,
                lane_root=lane_root,
                expected_lane=scope.layer,
                expected_nature=nature,
                expected_required_rungs=AVAILABILITY_REQUIRED_RUNGS,
                required_source_ceiling=required_ceiling,
            )
            with self._cache_lock:
                self._remember_index(lane_root, index)
            return index

    def _remember_index(self, lane_root: str, index: AvailabilityIndex) -> None:
        """Retain only small parsed generations under aggregate byte and row budgets."""
        generation_bytes = index.pointer.generation_bytes
        generation_rows = index.pointer.rows
        prior = self._indexes.pop(lane_root, None)
        if prior is not None:
            self._index_cache_bytes -= prior.pointer.generation_bytes
            self._index_cache_rows -= prior.pointer.rows
        if generation_bytes > MAX_CACHED_INDEX_GENERATION_BYTES or generation_rows > MAX_CACHED_INDEX_ROWS:
            return
        while self._indexes and (
            len(self._indexes) >= MAX_VERIFIED_AVAILABILITY_INDEXES
            or self._index_cache_bytes + generation_bytes > MAX_CACHED_INDEX_BYTES
            or self._index_cache_rows + generation_rows > MAX_CACHED_INDEX_ROWS
        ):
            oldest = next(iter(self._indexes))
            evicted = self._indexes.pop(oldest)
            self._index_cache_bytes -= evicted.pointer.generation_bytes
            self._index_cache_rows -= evicted.pointer.rows
        if (
            self._index_cache_bytes + generation_bytes <= MAX_CACHED_INDEX_BYTES
            and self._index_cache_rows + generation_rows <= MAX_CACHED_INDEX_ROWS
        ):
            self._indexes[lane_root] = index
            self._index_cache_bytes += generation_bytes
            self._index_cache_rows += generation_rows


class AuthorizedServingReaderHolder:
    """Build one read-only availability adapter per process."""

    def __init__(self) -> None:
        self._held: AuthorizedServingReader | None = None

    def get(self, source: Settings) -> AuthorizedServingReader:
        if self._held is None:
            self._held = AuthorizedServingReader(BotoAvailabilityStorage.from_settings(source))
        return self._held


def _availability_refusal(layer: str, exc: AvailabilityError) -> faults.ServingRefusalError:
    """Translate evidence vocabulary once, without leaking it into HTTP or agent adapters."""
    if isinstance(exc, AvailabilityChecksumError):
        return faults.availability_checksum_invalid(layer=layer, detail=str(exc))
    if isinstance(exc, AvailabilityMalformedError):
        return faults.availability_malformed(layer=layer, detail=str(exc))
    if isinstance(exc, AvailabilityUnavailableError):
        refusal = faults.availability_unpublished if exc.code == "availability_missing" else faults.availability_stale
        return refusal(layer=layer, detail=str(exc))
    return faults.availability_malformed(layer=layer, detail=str(exc))


def resolve_authorized_day(
    authority: AuthorizedServingReader,
    physical: WarehouseListing,
    reader: PartitionRowReader,
    *,
    scope: ReadScope,
    day: date,
) -> DayEnvelope:
    """Resolve one day from a fresh availability head, preserving static lookup behavior."""
    listing = authority.listing(physical, scope=scope)
    return resolve_day(listing, _receipt_bound_reader(listing, reader), scope=scope, day=day)


def resolve_authorized_window(  # noqa: PLR0913 - authority, physical plane, reader, and closed scope/window
    authority: AuthorizedServingReader,
    physical: WarehouseListing,
    reader: PartitionRowReader,
    *,
    scope: ReadScope,
    first_day: date,
    last_day: date,
) -> tuple[DayEnvelope, ...]:
    """Resolve a bounded window from one availability generation and one shared row budget."""
    listing = authority.listing(physical, scope=scope)
    return resolve_window(
        listing,
        _receipt_bound_reader(listing, reader),
        scope=scope,
        first_day=first_day,
        last_day=last_day,
    )


def resolve_authorized_release(
    authority: AuthorizedServingReader,
    physical: WarehouseListing,
    reader: PartitionRowReader,
    *,
    scope: ReadScope,
    as_of: date,
) -> DayEnvelope:
    """Resolve the latest indexed release while retaining the caller's requested day."""
    listing = authority.listing(physical, scope=scope)
    envelope = resolve_release(listing, _receipt_bound_reader(listing, reader), scope=scope, as_of=as_of)
    return replace(envelope, requested_day=as_of)


@dataclass(frozen=True, slots=True)
class _ReceiptBoundRowReader:
    listing: AvailabilityAuthorizedListing
    inner: PartitionRowReader

    def read_rows(self, read: RowRead) -> RowReadResult:
        with self.listing.verified_object_uris(read.keys) as exact_sources:
            uris = tuple(exact_sources[key] for key in read.keys)
            return self.inner.read_rows(replace(read, object_uris=uris))


def _receipt_bound_reader(
    listing: WarehouseListing,
    reader: PartitionRowReader,
) -> PartitionRowReader:
    """Bind time-bearing row scans to verified bytes; static lookups retain physical serving."""
    if not isinstance(listing, AvailabilityAuthorizedListing):
        return reader
    return _ReceiptBoundRowReader(listing=listing, inner=reader)


@contextmanager
def verified_serving_session(
    listing: WarehouseListing,
    session: ServingSession,
    keys: Sequence[str],
) -> Iterator[ServingSession]:
    """Share exact-byte admission with agent queries that execute their own DuckDB statements."""
    if isinstance(listing, AvailabilityAuthorizedListing):
        with listing.verified_session(session, keys) as verified:
            yield verified
        return
    yield session
