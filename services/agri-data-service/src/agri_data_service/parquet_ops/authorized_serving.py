"""Availability-authorized row serving for every time-bearing lane.

See `AGENTS.md`, "Availability-authorized serving".
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.availability_coverage import (
    GenerationCachingStorage,
    required_source_ceiling,
)
from agri_data_service.parquet_ops.coverage import CensusLane, DEDICATED_SLIDER_PRODUCT_LAYERS
from agri_data_service.parquet_ops.serving import resolve_day, resolve_release, resolve_window
from agri_data_service.pipeline.parquet.availability_index import (
    AvailabilityChecksumError,
    AvailabilityError,
    AvailabilityMalformedError,
    AvailabilityUnavailableError,
    BotoAvailabilityStorage,
    EVIDENCE_OBJECT_MAX_BYTES,
    read_latest_availability,
    read_terminal_evidence,
)
from agri_data_service.pipeline.parquet.objectstore import availability_lane_root
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.warehouse.schemas.availability_index import AVAILABILITY_REQUIRED_RUNGS

if TYPE_CHECKING:
    from collections.abc import Iterator

    from agri_data_service.config import Settings
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.parquet_ops.mtbs_snapshot_catalog import VerifiedMtbsSnapshot
    from agri_data_service.parquet_ops.request_params import ReadScope
    from agri_data_service.parquet_ops.warehouse_reader import PartitionRowReader, WarehouseListing
    from agri_data_service.parquet_ops.wire import DayEnvelope
    from agri_data_service.pipeline.parquet.availability_index import (
        AvailabilityIndex,
        AvailabilityRow,
        AvailabilityStorage,
        EvidenceReceipt,
    )

_LANES: Final = {
    slug: CensusLane(
        layer=slug,
        nature=registration.nature,
        kind="observed",
        cadence_days=registration.cadence_days,
        publication_lag_days=registration.publication_lag_days,
    )
    for slug, registration in LANE_REGISTRY.items()
}
_LANES.update(
    {
        layer: CensusLane(layer=layer, nature="daily_series", kind="observed")
        for layer in DEDICATED_SLIDER_PRODUCT_LAYERS
        if layer not in _LANES
    }
)


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

    def __post_init__(self) -> None:
        self._receipts = {}
        self._keys_by_day = {}
        self._rows_by_day = {}
        for row in self.index.rows:
            if row.rung != self.scope.tier:
                continue
            self._rows_by_day[row.day] = row
            receipts = list(row.data_receipts)
            if row.completion_receipt is not None:
                receipts.append(row.completion_receipt)
            for receipt in receipts:
                self._receipts[receipt.key] = receipt
            self._keys_by_day[row.day] = tuple(receipt.key for receipt in receipts)

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
        """Load an absence receipt only when the resolver addresses that day."""
        keys = self._keys_by_day[day]
        row = self._rows_by_day[day]
        if row.terminal_state != "governed_absence" or keys:
            return keys
        try:
            terminal = read_terminal_evidence(self.store, row.terminal_receipt, identity=self.index.pointer.identity)
        except AvailabilityError as exc:
            raise _availability_refusal(self.scope.layer, exc) from exc
        if terminal.absence_receipt is None:
            raise faults.availability_malformed(
                layer=self.scope.layer,
                detail=f"{row.day.isoformat()} z{row.rung} has no absence receipt",
            )
        self._receipts[terminal.absence_receipt.key] = terminal.absence_receipt
        keys = (terminal.absence_receipt.key,)
        self._keys_by_day[day] = keys
        return keys

    def iter_tier_keys(self, layer: str, kind: PartitionKind, tier: ZoomTier) -> Iterator[str]:
        yield from self.list_keys(layer, kind, tier)

    def iter_stream_keys(self, layer: str, kind: PartitionKind) -> Iterator[str]:
        if (layer, kind) == (self.scope.layer, self.scope.kind):
            yield from self.list_keys(layer, kind, self.scope.tier)

    def read_object(self, relative_key: str) -> bytes | None:
        """Read and re-check an authorized non-Parquet object by its recorded digest."""
        receipt = self._receipts.get(relative_key)
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

    def mtbs_snapshot_loader(self, day: date) -> VerifiedMtbsSnapshot | None:
        """Delegate MTBS proof, but admit only a day present in this exact generation and rung."""
        loader = getattr(self.physical, "mtbs_snapshot_loader", None)
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
        instant = datetime.now(UTC) if now is None else now
        try:
            index = read_latest_availability(
                self._store,
                lane_root=availability_lane_root(scope.layer, scope.kind),
                expected_lane=scope.layer,
                expected_nature=lane.nature,
                expected_required_rungs=AVAILABILITY_REQUIRED_RUNGS,
                required_source_ceiling=required_source_ceiling(lane, now=instant),
            )
        except AvailabilityError as exc:
            raise _availability_refusal(scope.layer, exc) from exc
        return AvailabilityAuthorizedListing(index=index, scope=scope, store=self._store, physical=physical)


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
    return resolve_day(authority.listing(physical, scope=scope), reader, scope=scope, day=day)


def resolve_authorized_window(
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
    return resolve_window(listing, reader, scope=scope, first_day=first_day, last_day=last_day)


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
    envelope = resolve_release(listing, reader, scope=scope, as_of=as_of)
    return replace(envelope, requested_day=as_of)
