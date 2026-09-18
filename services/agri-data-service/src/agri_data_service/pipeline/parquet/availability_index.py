"""Immutable availability generations, strict reads, bootstrap, and conditional publication.

The operations seam. Identity and rows live in `availability_documents`, typed evidence in
`availability_evidence`, inputs in `availability_requests`, conditional object access in
`availability_storage`, and evidence proof in `availability_verification`; every public name any of
them defines is re-exported here so this module stays the one import path callers need. Rationale:
see `AGENTS.md` in this directory, "Availability index".
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import structlog

from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.pipeline.parquet.availability_documents import (
    AvailabilityConfig,
    AvailabilityIdentity,
    AvailabilityIndex,
    AvailabilityPointer,
    AvailabilityRow,
    EvidenceReceipt,
    _bootstrap_marker_payload,
    _generation_receipt_sha256,
    _parse_pointer,
    _pointer_payload,
    _require_rows_published_by,
    _require_sorted_nonempty_receipts,
    _row_from_mapping,
    _validate_data_receipt_collection,
    _validate_generation_day,
    _validate_generation_rows,
    availability_bootstrap_marker_key,
    availability_generation_key,
    availability_lane_identity,
    availability_pointer_key,
    availability_provenance_summary,
    availability_row_provenance,
)
from agri_data_service.pipeline.parquet.availability_evidence import (
    BootstrapInventoryEvidence,
    SourceEvidence,
    TerminalEvidence,
    TypedEvidenceArtifact,
    availability_row_from_terminal_evidence,
    build_bootstrap_inventory_evidence,
    build_source_evidence,
    build_terminal_evidence,
    compute_verified_source_inventory_root,
)
from agri_data_service.pipeline.parquet.availability_primitives import (
    BOOTSTRAP_INPUT_SCHEMA_VERSION,
    BOOTSTRAP_MARKER_SCHEMA_VERSION,
    BOOTSTRAP_RECEIPT_MAX_BYTES,
    DIGESTED_PROVENANCE,
    EVIDENCE_OBJECT_MAX_BYTES,
    GENERATION_MAX_BYTES,
    JSON_CONTENT_TYPE,
    MANIFEST_TRUSTED_PROVENANCE,
    MAX_AVAILABILITY_ROWS,
    MAX_PUBLICATION_ATTEMPTS,
    PARQUET_CONTENT_TYPE,
    POINTER_MAX_BYTES,
    PROVENANCE_FIELD,
    PUBLICATION_INPUT_SCHEMA_VERSION,
    TYPED_RECEIPT_MAX_BYTES,
    AlreadyBootstrappedError,
    AvailabilityChecksumError,
    AvailabilityConflictError,
    AvailabilityError,
    AvailabilityMalformedError,
    AvailabilityNature,
    AvailabilityProvenance,
    AvailabilityUnavailableError,
    TerminalState,
    _format_datetime,
    _generation_sha_from_key,
    _optional_string,
    _parse_date,
    _parse_datetime,
    _parse_nature,
    _parse_positive_int,
    _parse_rungs,
    _require_sha256,
    _require_utc,
)
from agri_data_service.pipeline.parquet.availability_requests import (
    BootstrapRequest,
    PublicationRequest,
    PublicationResult,
    _bootstrap_receipt_payload,
    _classify_request_rows,
    _logical_created_at,
    _merge_rows,
    _refuse_trusted_publication_rows,
    _require_config_compatible,
    load_bootstrap_request,
    load_publication_request,
)
from agri_data_service.pipeline.parquet.availability_storage import (
    AvailabilityStorage,
    BotoAvailabilityStorage,
    EvidenceSnapshot,
    StoredAvailabilityObject,
    _dedupe_receipts,
    _dedupe_snapshots,
    _revalidate_snapshots,
    _verify_raw_receipts,
)
from agri_data_service.pipeline.parquet.availability_verification import (
    _verify_bootstrap_inventory_receipts,
    _verify_completion_object,
    _verify_rows_evidence,
    _verify_source_evidence_receipt,
    _verify_system_bootstrap_receipt,
    read_bootstrap_marker,
    read_terminal_evidence,
)
from agri_data_service.pipeline.parquet.coverage_rollup import entry_from_index, refresh_coverage_rollup_entry
from agri_data_service.pipeline.parquet.publication_barrier import postgres_lane_publication_barrier
from agri_data_service.warehouse.schemas.availability_index import (
    AVAILABILITY_INDEX_SCHEMA,
    AVAILABILITY_METADATA_KEYS,
    AVAILABILITY_SCHEMA_VERSION,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from contextlib import AbstractAsyncContextManager
    from datetime import date, datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    AvailabilityPublicationBarrier = Callable[[AsyncSession, str], AbstractAsyncContextManager[bool]]

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class _LoadedLatest:
    pointer: AvailabilityPointer
    rows: tuple[AvailabilityRow, ...]
    etag: str


@dataclass(frozen=True, slots=True)
class _VerifiedGeneration:
    pointer: AvailabilityPointer
    rows: tuple[AvailabilityRow, ...]


async def bootstrap_availability(
    session: AsyncSession,
    store: AvailabilityStorage,
    request: BootstrapRequest,
    *,
    publication_barrier: AvailabilityPublicationBarrier = postgres_lane_publication_barrier,
) -> PublicationResult:
    """Create generation zero while exclusively owning the lane publication boundary."""
    async with publication_barrier(session, request.identity.lane_root) as granted:
        if not granted:
            raise AvailabilityConflictError("availability publication barrier is contended")
        return _refresh_coverage_rollup(store, _bootstrap_availability_owned(store, request))


def _bootstrap_availability_owned(store: AvailabilityStorage, request: BootstrapRequest) -> PublicationResult:
    """Create one lane's immutable bootstrap receipt and generation zero; caller owns its barrier."""
    _require_sha256(request.input_sha256, "bootstrap input sha256")
    _require_utc(request.created_at, "created_at")
    _validate_generation_rows(
        request.rows,
        identity=request.identity,
        source_ceiling=request.source_ceiling,
    )
    _require_rows_published_by(request.rows, request.created_at)
    _require_sorted_nonempty_receipts(request.input_receipts, "bootstrap input receipts")
    receipt_payload = _bootstrap_receipt_payload(request)
    receipt_sha256 = sha256_digest(receipt_payload)
    receipt = EvidenceReceipt(
        key=f"{request.identity.lane_root}/availability/bootstrap/receipt={receipt_sha256}.json",
        sha256=receipt_sha256,
    )
    config = AvailabilityConfig(
        identity=request.identity,
        source_ceiling=request.source_ceiling,
        bootstrap_receipt=receipt,
    )
    snapshots: tuple[EvidenceSnapshot, ...] | None = None
    for attempt in range(1, MAX_PUBLICATION_ATTEMPTS + 1):
        latest = _load_latest_optional(store, request.identity.lane_root)
        if latest is not None:
            if _is_same_bootstrap(latest, config=config):
                return PublicationResult(pointer=latest.pointer, advanced=False, attempts=attempt)
            raise AlreadyBootstrappedError(
                f"availability lane {request.identity.lane_root!r} already has a different bootstrap"
            )
        if snapshots is None:
            inventories, verified_inventory = _verify_bootstrap_inventory_receipts(
                store,
                request.input_receipts,
                expected_identity=request.identity,
                expected_source_ceiling=request.source_ceiling,
            )
            underlying = _dedupe_receipts(
                tuple(receipt for inventory in inventories for receipt in inventory.object_receipts)
            )
            if compute_verified_source_inventory_root(underlying) != request.identity.verified_source_inventory_root:
                raise AvailabilityChecksumError(
                    "bootstrap inventory wrappers do not establish verified_source_inventory_root"
                )
            verified = list(verified_inventory)
            verified.extend(_verify_rows_evidence(store, request.rows, identity=request.identity))
            store.put_immutable(receipt.key, receipt_payload, content_type=JSON_CONTENT_TYPE)
            # The deterministic marker, written BEFORE the pointer exists: it is what proves this
            # lane was bootstrapped at all once its mutable head is gone. Immutable and
            # receipt-derived, so a retried bootstrap re-writes identical bytes and a DIFFERENT
            # bootstrap is refused here rather than quietly beginning a second history.
            store.put_immutable(
                availability_bootstrap_marker_key(request.identity.lane_root),
                _bootstrap_marker_payload(request.identity.lane_root, receipt),
                content_type=JSON_CONTENT_TYPE,
            )
            verified.append(
                _verify_system_bootstrap_receipt(
                    store,
                    receipt,
                    expected_identity=request.identity,
                    maximum_row_count=len(request.rows),
                    maximum_source_ceiling=request.source_ceiling,
                )
            )
            snapshots = _dedupe_snapshots(verified)
        pointer = _write_generation(
            store,
            config=config,
            rows=request.rows,
            prior_generation_key=None,
            prior_generation_sha256=None,
            created_at=request.created_at,
        )
        pointer_payload = _pointer_payload(pointer)
        _revalidate_snapshots(store, snapshots)
        if store.compare_and_swap(
            availability_pointer_key(request.identity.lane_root),
            pointer_payload,
            expected_etag=None,
            content_type=JSON_CONTENT_TYPE,
        ):
            return PublicationResult(pointer=pointer, advanced=True, attempts=attempt, rows=request.rows)
    raise AvailabilityConflictError("availability bootstrap pointer remained contended after bounded retries")


async def publish_availability(
    session: AsyncSession,
    store: AvailabilityStorage,
    request: PublicationRequest,
    *,
    publication_barrier: AvailabilityPublicationBarrier = postgres_lane_publication_barrier,
) -> PublicationResult:
    """Publish terminal outcomes while exclusively owning the lane publication boundary."""
    async with publication_barrier(session, request.config.identity.lane_root) as granted:
        if not granted:
            raise AvailabilityConflictError("availability publication barrier is contended")
        return _refresh_coverage_rollup(store, _publish_availability_owned(store, request))


def _publish_availability_owned(store: AvailabilityStorage, request: PublicationRequest) -> PublicationResult:
    """Append or correct terminal outcomes and conditionally advance the pointer; caller owns its barrier."""
    _require_sha256(request.input_sha256, "publication input sha256")
    _require_utc(request.created_at, "created_at")
    _validate_generation_rows(
        request.rows,
        identity=request.config.identity,
        source_ceiling=request.config.source_ceiling,
    )
    _refuse_trusted_publication_rows(request.rows)
    _require_rows_published_by(request.rows, request.created_at)
    snapshots: tuple[EvidenceSnapshot, ...] | None = None
    for attempt in range(1, MAX_PUBLICATION_ATTEMPTS + 1):
        latest = _load_latest_required(store, request.config.identity.lane_root)
        _require_config_compatible(request.config, latest.pointer)
        classification = _classify_request_rows(latest.rows, request.rows)
        if classification.is_exact_replay and request.config.source_ceiling <= latest.pointer.source_ceiling:
            return PublicationResult(pointer=latest.pointer, advanced=False, attempts=attempt)
        if classification.stale_conflicting_grains:
            rendered = ", ".join(f"{day.isoformat()}/z{rung}" for day, rung in classification.stale_conflicting_grains)
            raise AvailabilityConflictError(f"stale publication conflicts with existing grains: {rendered}")
        if snapshots is None:
            snapshots = _dedupe_snapshots(
                (
                    _verify_system_bootstrap_receipt(
                        store,
                        request.config.bootstrap_receipt,
                        expected_identity=request.config.identity,
                        maximum_row_count=latest.pointer.rows,
                        maximum_source_ceiling=latest.pointer.source_ceiling,
                    ),
                    *_verify_rows_evidence(store, request.rows, identity=request.config.identity),
                )
            )
        merged = _merge_rows(latest.rows, request.rows)
        effective_config = AvailabilityConfig(
            identity=request.config.identity,
            source_ceiling=max(request.config.source_ceiling, latest.pointer.source_ceiling),
            bootstrap_receipt=request.config.bootstrap_receipt,
        )
        effective_created_at = _logical_created_at(request.created_at, latest.pointer.created_at)
        pointer = _write_generation(
            store,
            config=effective_config,
            rows=merged,
            prior_generation_key=latest.pointer.generation_key,
            prior_generation_sha256=latest.pointer.generation_sha256,
            created_at=effective_created_at,
        )
        pointer_payload = _pointer_payload(pointer)
        _revalidate_snapshots(store, snapshots)
        if store.compare_and_swap(
            availability_pointer_key(request.config.identity.lane_root),
            pointer_payload,
            expected_etag=latest.etag,
            content_type=JSON_CONTENT_TYPE,
        ):
            return PublicationResult(pointer=pointer, advanced=True, attempts=attempt, rows=merged)
    raise AvailabilityConflictError("availability pointer remained contended after bounded retries")


async def rollback_availability(  # noqa: PLR0913 - public ownership seam plus retained-generation coordinates
    session: AsyncSession,
    store: AvailabilityStorage,
    *,
    lane_root: str,
    target_generation_key: str,
    created_at: datetime,
    publication_barrier: AvailabilityPublicationBarrier = postgres_lane_publication_barrier,
) -> PublicationResult:
    """Restore a retained generation while exclusively owning the lane publication boundary."""
    async with publication_barrier(session, lane_root) as granted:
        if not granted:
            raise AvailabilityConflictError("availability publication barrier is contended")
        return _refresh_coverage_rollup(
            store,
            _rollback_availability_owned(
                store,
                lane_root=lane_root,
                target_generation_key=target_generation_key,
                created_at=created_at,
            ),
        )


def _refresh_coverage_rollup(store: AvailabilityStorage, result: PublicationResult) -> PublicationResult:
    """Merge this lane's resolved coverage facts into the warehouse rollup, and never fail on it.

    THE CHOKEPOINT, chosen because it already exists: all three publication seams end here, inside
    the caller's per-lane barrier and immediately after the compare-and-swap that made the new
    generation visible. A separate scheduler would have to rediscover which lanes moved and would
    be a second thing to keep alive; this cannot drift from publication because it IS publication.

    A rollup failure is swallowed on purpose. The pointer already advanced, so the publication is
    durable; what a failure leaves behind is a stale CACHE entry, and the reader's pointer-digest
    check turns that into one full per-lane read rather than a wrong answer. Raising here would
    convert a cache miss into a lane that failed to publish.
    """
    if not result.advanced or not result.rows:
        return result
    try:
        refresh_coverage_rollup_entry(
            store,
            entry=entry_from_index(
                AvailabilityIndex(pointer=result.pointer, rows=result.rows),
                updated_at=result.pointer.created_at,
            ),
        )
    except Exception:
        # Broad on purpose: EVERY fault reachable from a cache refresh -- transport, shape, bound --
        # must leave the durable publication above it untouched and reported as the success it was.
        logger.exception(
            "coverage_rollup_refresh_failed",
            lane_root=result.pointer.identity.lane_root,
            generation_sha256=result.pointer.generation_sha256,
        )
    return result


def _rollback_availability_owned(
    store: AvailabilityStorage,
    *,
    lane_root: str,
    target_generation_key: str,
    created_at: datetime,
) -> PublicationResult:
    """Republish a retained generation after the current head; caller owns its lane barrier."""
    _require_utc(created_at, "created_at")
    target_sha256 = _generation_sha_from_key(lane_root, target_generation_key)
    snapshots: tuple[EvidenceSnapshot, ...] | None = None
    for attempt in range(1, MAX_PUBLICATION_ATTEMPTS + 1):
        latest = _load_latest_required(store, lane_root)
        target = _read_generation_for_pointer(
            store,
            pointer=latest.pointer,
            generation_key=target_generation_key,
            generation_sha256=target_sha256,
            require_pointer_metadata=False,
        )
        if snapshots is None:
            snapshots = _dedupe_snapshots(
                (
                    _verify_system_bootstrap_receipt(
                        store,
                        target.pointer.bootstrap_receipt,
                        expected_identity=target.pointer.identity,
                        maximum_row_count=target.pointer.rows,
                        maximum_source_ceiling=target.pointer.source_ceiling,
                    ),
                    *_verify_rows_evidence(store, target.rows, identity=target.pointer.identity),
                )
            )
        config = AvailabilityConfig(
            identity=target.pointer.identity,
            source_ceiling=target.pointer.source_ceiling,
            bootstrap_receipt=target.pointer.bootstrap_receipt,
        )
        pointer = _write_generation(
            store,
            config=config,
            rows=target.rows,
            prior_generation_key=latest.pointer.generation_key,
            prior_generation_sha256=latest.pointer.generation_sha256,
            created_at=_logical_created_at(created_at, latest.pointer.created_at),
        )
        pointer_payload = _pointer_payload(pointer)
        _revalidate_snapshots(store, snapshots)
        if store.compare_and_swap(
            availability_pointer_key(lane_root),
            pointer_payload,
            expected_etag=latest.etag,
            content_type=JSON_CONTENT_TYPE,
        ):
            return PublicationResult(pointer=pointer, advanced=True, attempts=attempt, rows=target.rows)
    raise AvailabilityConflictError("availability rollback pointer remained contended after bounded retries")


def read_latest_availability(  # noqa: PLR0913
    store: AvailabilityStorage,
    *,
    lane_root: str,
    expected_lane: str | None = None,
    expected_product: str | None = None,
    expected_nature: AvailabilityNature | None = None,
    expected_required_rungs: tuple[int, ...] | None = None,
    required_source_ceiling: date | None = None,
) -> AvailabilityIndex:
    """Read exactly one pointer and its checksum-bound generation, failing closed."""
    latest = _load_latest_required(store, lane_root)
    _require_pointer_expectations(
        latest.pointer,
        expected_lane=expected_lane,
        expected_product=expected_product,
        expected_nature=expected_nature,
        expected_required_rungs=expected_required_rungs,
        required_source_ceiling=required_source_ceiling,
    )
    return AvailabilityIndex(pointer=latest.pointer, rows=latest.rows)


def read_availability_pointer(  # noqa: PLR0913 - one declared expectation per arg, exactly as the full read
    store: AvailabilityStorage,
    *,
    lane_root: str,
    expected_lane: str | None = None,
    expected_product: str | None = None,
    expected_nature: AvailabilityNature | None = None,
    expected_required_rungs: tuple[int, ...] | None = None,
    required_source_ceiling: date | None = None,
) -> AvailabilityPointer:
    """Read and check ONE pointer, without fetching the generation it names.

    Every expectation `read_latest_availability` enforces about lane identity, nature, rung contract
    and ceiling staleness is enforced here too -- all of them are pointer facts. What is NOT enforced
    is the generation's own checksum, row population and semantic receipt, because those need the
    generation's bytes. A caller that answers from this pointer alone is therefore trusting a digest
    the publisher computed rather than re-deriving it, and must say so: see
    `parquet_ops/availability_coverage.py`, which uses it only when the coverage rollup already holds
    an entry bound to this exact pointer document.
    """
    stored = store.read(availability_pointer_key(lane_root), max_bytes=POINTER_MAX_BYTES)
    if stored is None:
        raise AvailabilityUnavailableError("availability_missing", f"availability pointer is missing for {lane_root!r}")
    try:
        pointer = _parse_pointer(stored.payload, expected_lane_root=lane_root)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AvailabilityMalformedError(f"malformed availability pointer for {lane_root!r}") from exc
    _require_pointer_expectations(
        pointer,
        expected_lane=expected_lane,
        expected_product=expected_product,
        expected_nature=expected_nature,
        expected_required_rungs=expected_required_rungs,
        required_source_ceiling=required_source_ceiling,
    )
    return pointer


def _require_pointer_expectations(  # noqa: PLR0913 - one declared expectation per arg
    pointer: AvailabilityPointer,
    *,
    expected_lane: str | None,
    expected_product: str | None,
    expected_nature: AvailabilityNature | None,
    expected_required_rungs: tuple[int, ...] | None,
    required_source_ceiling: date | None,
) -> None:
    """Refuse a pointer whose identity, rung contract or ceiling is not what the caller requires."""
    expectations: tuple[tuple[str, object | None, object], ...] = (
        ("lane", expected_lane, pointer.identity.lane),
        ("product", expected_product, pointer.identity.product),
        ("nature", expected_nature, pointer.identity.nature),
        ("required_rungs", expected_required_rungs, pointer.required_rungs),
    )
    for label, expected, actual in expectations:
        if expected is not None and expected != actual:
            raise AvailabilityUnavailableError(
                "availability_stale",
                f"availability {label} {actual!r} does not match required {expected!r}",
            )
    if required_source_ceiling is not None and pointer.source_ceiling < required_source_ceiling:
        raise AvailabilityUnavailableError(
            "availability_stale",
            f"availability source ceiling {pointer.source_ceiling} precedes required {required_source_ceiling}",
        )


def _write_generation(  # noqa: PLR0913
    store: AvailabilityStorage,
    *,
    config: AvailabilityConfig,
    rows: tuple[AvailabilityRow, ...],
    prior_generation_key: str | None,
    prior_generation_sha256: str | None,
    created_at: datetime,
) -> AvailabilityPointer:
    _require_utc(created_at, "created_at")
    _validate_generation_rows(rows, identity=config.identity, source_ceiling=config.source_ceiling)
    _require_rows_published_by(rows, created_at)
    generation_receipt = _generation_receipt_sha256(
        config=config,
        rows=rows,
        prior_generation_key=prior_generation_key,
        prior_generation_sha256=prior_generation_sha256,
        created_at=created_at,
    )
    earliest = min(row.day for row in rows)
    latest = max(row.day for row in rows)
    payload = _serialize_generation(
        config=config,
        rows=rows,
        generation_receipt_sha256=generation_receipt,
        earliest_terminal_day=earliest,
        latest_terminal_day=latest,
        prior_generation_key=prior_generation_key,
        prior_generation_sha256=prior_generation_sha256,
        created_at=created_at,
    )
    generation_sha256 = sha256_digest(payload)
    generation_key = availability_generation_key(config.identity.lane_root, generation_sha256)
    pointer = AvailabilityPointer(
        schema_version=AVAILABILITY_SCHEMA_VERSION,
        identity=config.identity,
        required_rungs=config.identity.required_rungs,
        generation_key=generation_key,
        generation_sha256=generation_sha256,
        generation_receipt_sha256=generation_receipt,
        generation_bytes=len(payload),
        rows=len(rows),
        earliest_terminal_day=earliest,
        latest_terminal_day=latest,
        source_ceiling=config.source_ceiling,
        prior_generation_key=prior_generation_key,
        prior_generation_sha256=prior_generation_sha256,
        created_at=created_at,
        bootstrap_receipt=config.bootstrap_receipt,
    )
    store.put_immutable(generation_key, payload, content_type=PARQUET_CONTENT_TYPE)
    reread = _read_generation_for_pointer(
        store,
        pointer=pointer,
        generation_key=generation_key,
        generation_sha256=generation_sha256,
        require_pointer_metadata=True,
    )
    if reread.rows != rows:
        raise AvailabilityChecksumError("availability generation reread changed its canonical rows")
    return pointer


def _load_latest_optional(store: AvailabilityStorage, lane_root: str) -> _LoadedLatest | None:
    pointer_key = availability_pointer_key(lane_root)
    stored = store.read(pointer_key, max_bytes=POINTER_MAX_BYTES)
    if stored is None:
        return None
    try:
        pointer = _parse_pointer(stored.payload, expected_lane_root=lane_root)
        generation = _read_generation_for_pointer(
            store,
            pointer=pointer,
            generation_key=pointer.generation_key,
            generation_sha256=pointer.generation_sha256,
            require_pointer_metadata=True,
        )
    except AvailabilityChecksumError:
        raise
    except AvailabilityUnavailableError:
        raise
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
        raise AvailabilityMalformedError(f"malformed availability evidence for {lane_root!r}") from exc
    return _LoadedLatest(pointer=pointer, rows=generation.rows, etag=stored.etag)


def _load_latest_required(store: AvailabilityStorage, lane_root: str) -> _LoadedLatest:
    latest = _load_latest_optional(store, lane_root)
    if latest is None:
        raise AvailabilityUnavailableError("availability_missing", f"availability pointer is missing for {lane_root!r}")
    return latest


def _read_generation_for_pointer(
    store: AvailabilityStorage,
    *,
    pointer: AvailabilityPointer,
    generation_key: str,
    generation_sha256: str,
    require_pointer_metadata: bool,
) -> _VerifiedGeneration:
    read_ceiling = (
        pointer.generation_bytes
        if generation_key == pointer.generation_key and generation_sha256 == pointer.generation_sha256
        else GENERATION_MAX_BYTES
    )
    stored = store.read(generation_key, max_bytes=read_ceiling)
    if stored is None:
        raise AvailabilityUnavailableError(
            "availability_stale", f"availability generation {generation_key!r} is missing"
        )
    actual_sha256 = sha256_digest(stored.payload)
    if actual_sha256 != generation_sha256:
        raise AvailabilityChecksumError(
            f"availability generation checksum mismatch: expected {generation_sha256}, got {actual_sha256}"
        )
    expected_key = availability_generation_key(pointer.identity.lane_root, actual_sha256)
    if expected_key != generation_key:
        raise AvailabilityChecksumError("availability generation key is not bound to its byte digest")
    parquet_file = pq.ParquetFile(io.BytesIO(stored.payload))
    if parquet_file.metadata.num_rows > MAX_AVAILABILITY_ROWS:
        raise AvailabilityMalformedError("availability generation declares too many physical rows")
    if (
        generation_key == pointer.generation_key
        and generation_sha256 == pointer.generation_sha256
        and parquet_file.metadata.num_rows != pointer.rows
    ):
        raise AvailabilityMalformedError("availability pointer row count disagrees before materialization")
    if not parquet_file.schema_arrow.remove_metadata().equals(AVAILABILITY_INDEX_SCHEMA):
        raise AvailabilityMalformedError("availability generation Arrow schema does not match version 1")
    table = _materialize_generation(parquet_file)
    metadata = table.schema.metadata
    metadata_keys = set() if metadata is None else set(metadata) - {b"ARROW:schema"}
    if metadata is None or metadata_keys != AVAILABILITY_METADATA_KEYS:
        raise AvailabilityMalformedError("availability generation metadata is missing, extra, or malformed")
    rows = tuple(_row_from_mapping(item) for item in cast("list[dict[str, object]]", table.to_pylist()))
    metadata_pointer = _pointer_from_metadata(
        metadata,
        generation_key=generation_key,
        generation_sha256=generation_sha256,
        generation_bytes=len(stored.payload),
    )
    _validate_generation_rows(
        rows,
        identity=metadata_pointer.identity,
        source_ceiling=metadata_pointer.source_ceiling,
    )
    _require_rows_published_by(rows, metadata_pointer.created_at)
    if (
        metadata_pointer.rows != len(rows)
        or metadata_pointer.earliest_terminal_day != min(row.day for row in rows)
        or metadata_pointer.latest_terminal_day != max(row.day for row in rows)
    ):
        raise AvailabilityMalformedError("availability metadata does not match its physical row population")
    expected_receipt = _generation_receipt_sha256(
        config=AvailabilityConfig(
            identity=metadata_pointer.identity,
            source_ceiling=metadata_pointer.source_ceiling,
            bootstrap_receipt=metadata_pointer.bootstrap_receipt,
        ),
        rows=rows,
        prior_generation_key=metadata_pointer.prior_generation_key,
        prior_generation_sha256=metadata_pointer.prior_generation_sha256,
        created_at=metadata_pointer.created_at,
    )
    if expected_receipt != metadata_pointer.generation_receipt_sha256:
        raise AvailabilityChecksumError("availability semantic generation receipt is invalid")
    if require_pointer_metadata and metadata_pointer != pointer:
        raise AvailabilityUnavailableError("availability_stale", "availability pointer and Parquet metadata disagree")
    if not require_pointer_metadata and (
        metadata_pointer.identity != pointer.identity or metadata_pointer.bootstrap_receipt != pointer.bootstrap_receipt
    ):
        raise AvailabilityUnavailableError("availability_stale", "retained generation belongs to another contract")
    return _VerifiedGeneration(pointer=metadata_pointer, rows=rows)


def _materialize_generation(parquet_file: pq.ParquetFile) -> pa.Table:
    """Read the whole generation into memory, after the row-count ceiling above has already refused an oversized one.

    Kept as its own seam (not inlined) so a test can monkeypatch it to prove that ceiling is checked
    from metadata BEFORE this read, never after.
    """
    return parquet_file.read()


def _serialize_generation(  # noqa: PLR0913
    *,
    config: AvailabilityConfig,
    rows: tuple[AvailabilityRow, ...],
    generation_receipt_sha256: str,
    earliest_terminal_day: date,
    latest_terminal_day: date,
    prior_generation_key: str | None,
    prior_generation_sha256: str | None,
    created_at: datetime,
) -> bytes:
    metadata = _metadata(
        config=config,
        generation_receipt_sha256=generation_receipt_sha256,
        row_count=len(rows),
        earliest_terminal_day=earliest_terminal_day,
        latest_terminal_day=latest_terminal_day,
        prior_generation_key=prior_generation_key,
        prior_generation_sha256=prior_generation_sha256,
        created_at=created_at,
    )
    schema = AVAILABILITY_INDEX_SCHEMA.with_metadata(metadata)
    table = pa.Table.from_pylist([row.to_arrow() for row in rows], schema=schema)
    sink = io.BytesIO()
    pq.write_table(table, sink, compression="zstd", version="2.6", write_statistics=True)
    return sink.getvalue()


def _metadata(  # noqa: PLR0913
    *,
    config: AvailabilityConfig,
    generation_receipt_sha256: str,
    row_count: int,
    earliest_terminal_day: date,
    latest_terminal_day: date,
    prior_generation_key: str | None,
    prior_generation_sha256: str | None,
    created_at: datetime,
) -> dict[bytes, bytes]:
    values = {
        "bootstrap_receipt_key": config.bootstrap_receipt.key,
        "bootstrap_receipt_sha256": config.bootstrap_receipt.sha256,
        "created_at": _format_datetime(created_at),
        "earliest_terminal_day": earliest_terminal_day.isoformat(),
        "generation_receipt_sha256": generation_receipt_sha256,
        "lane": config.identity.lane,
        "lane_root": config.identity.lane_root,
        "latest_terminal_day": latest_terminal_day.isoformat(),
        "nature": config.identity.nature,
        "prior_generation_key": canonical_json(prior_generation_key),
        "prior_generation_sha256": canonical_json(prior_generation_sha256),
        "product": config.identity.product,
        "required_rungs": canonical_json(config.identity.required_rungs),
        "row_count": str(row_count),
        "schema_version": AVAILABILITY_SCHEMA_VERSION,
        "source_ceiling": config.source_ceiling.isoformat(),
        "verified_source_inventory_root": config.identity.verified_source_inventory_root,
    }
    return {f"availability.{key}".encode(): value.encode() for key, value in values.items()}


def _pointer_from_metadata(
    metadata: Mapping[bytes, bytes],
    *,
    generation_key: str,
    generation_sha256: str,
    generation_bytes: int,
) -> AvailabilityPointer:
    def text(name: str) -> str:
        return metadata[f"availability.{name}".encode()].decode("utf-8")

    required_rungs_value: object = json.loads(text("required_rungs"))
    prior_value: object = json.loads(text("prior_generation_key"))
    prior_sha_value: object = json.loads(text("prior_generation_sha256"))
    required_rungs = _parse_rungs(required_rungs_value)
    prior_generation_key = _optional_string(prior_value, "prior_generation_key")
    prior_generation_sha256 = _optional_string(prior_sha_value, "prior_generation_sha256")
    identity = AvailabilityIdentity(
        lane_root=text("lane_root"),
        lane=text("lane"),
        product=text("product"),
        nature=_parse_nature(text("nature")),
        required_rungs=required_rungs,
        verified_source_inventory_root=text("verified_source_inventory_root"),
    )
    return AvailabilityPointer(
        schema_version=text("schema_version"),
        identity=identity,
        required_rungs=required_rungs,
        generation_key=generation_key,
        generation_sha256=generation_sha256,
        generation_receipt_sha256=text("generation_receipt_sha256"),
        generation_bytes=generation_bytes,
        rows=_parse_positive_int(text("row_count"), "row_count"),
        earliest_terminal_day=_parse_date(text("earliest_terminal_day"), "earliest_terminal_day"),
        latest_terminal_day=_parse_date(text("latest_terminal_day"), "latest_terminal_day"),
        source_ceiling=_parse_date(text("source_ceiling"), "source_ceiling"),
        prior_generation_key=prior_generation_key,
        prior_generation_sha256=prior_generation_sha256,
        created_at=_parse_datetime(text("created_at"), "created_at"),
        bootstrap_receipt=EvidenceReceipt(
            key=text("bootstrap_receipt_key"),
            sha256=text("bootstrap_receipt_sha256"),
        ),
    )


def _is_same_bootstrap(
    latest: _LoadedLatest,
    *,
    config: AvailabilityConfig,
) -> bool:
    return latest.pointer.identity == config.identity and latest.pointer.bootstrap_receipt == config.bootstrap_receipt


#: The one import path for availability. Every name any sibling module defines is re-exported here so
#: the split into `availability_{primitives,documents,evidence,requests,storage,verification}` stays
#: an internal seam; the underscored entries are reached by the contract tests that exercise one
#: refusal at a time.
__all__ = [
    "AVAILABILITY_SCHEMA_VERSION",
    "BOOTSTRAP_INPUT_SCHEMA_VERSION",
    "BOOTSTRAP_MARKER_SCHEMA_VERSION",
    "BOOTSTRAP_RECEIPT_MAX_BYTES",
    "DIGESTED_PROVENANCE",
    "EVIDENCE_OBJECT_MAX_BYTES",
    "GENERATION_MAX_BYTES",
    "JSON_CONTENT_TYPE",
    "MANIFEST_TRUSTED_PROVENANCE",
    "MAX_AVAILABILITY_ROWS",
    "MAX_PUBLICATION_ATTEMPTS",
    "PARQUET_CONTENT_TYPE",
    "POINTER_MAX_BYTES",
    "PROVENANCE_FIELD",
    "PUBLICATION_INPUT_SCHEMA_VERSION",
    "TYPED_RECEIPT_MAX_BYTES",
    "AlreadyBootstrappedError",
    "AvailabilityChecksumError",
    "AvailabilityConfig",
    "AvailabilityConflictError",
    "AvailabilityError",
    "AvailabilityIdentity",
    "AvailabilityIndex",
    "AvailabilityMalformedError",
    "AvailabilityNature",
    "AvailabilityPointer",
    "AvailabilityProvenance",
    "AvailabilityRow",
    "AvailabilityStorage",
    "AvailabilityUnavailableError",
    "BootstrapInventoryEvidence",
    "BootstrapRequest",
    "BotoAvailabilityStorage",
    "EvidenceReceipt",
    "EvidenceSnapshot",
    "PublicationRequest",
    "PublicationResult",
    "SourceEvidence",
    "StoredAvailabilityObject",
    "TerminalEvidence",
    "TerminalState",
    "TypedEvidenceArtifact",
    "_bootstrap_availability_owned",
    "_dedupe_snapshots",
    "_format_datetime",
    "_materialize_generation",
    "_publish_availability_owned",
    "_refresh_coverage_rollup",
    "_revalidate_snapshots",
    "_rollback_availability_owned",
    "_validate_data_receipt_collection",
    "_validate_generation_day",
    "_verify_completion_object",
    "_verify_raw_receipts",
    "_verify_rows_evidence",
    "_verify_source_evidence_receipt",
    "availability_bootstrap_marker_key",
    "availability_generation_key",
    "availability_lane_identity",
    "availability_pointer_key",
    "availability_provenance_summary",
    "availability_row_from_terminal_evidence",
    "availability_row_provenance",
    "bootstrap_availability",
    "build_bootstrap_inventory_evidence",
    "build_source_evidence",
    "build_terminal_evidence",
    "compute_verified_source_inventory_root",
    "load_bootstrap_request",
    "load_publication_request",
    "publish_availability",
    "read_availability_pointer",
    "read_bootstrap_marker",
    "read_latest_availability",
    "read_terminal_evidence",
    "rollback_availability",
]
