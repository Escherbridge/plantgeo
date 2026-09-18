"""Bootstrap and publication inputs: how a pinned document becomes a request, and how requests merge.

Rationale and the contracts these enforce: see `AGENTS.md` in this directory, "Availability index".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.pipeline.parquet.availability_documents import (
    AvailabilityConfig,
    AvailabilityIdentity,
    AvailabilityPointer,
    AvailabilityRow,
    EvidenceReceipt,
    _parse_identity,
    _parse_receipts,
    _parse_rows,
    _require_expected_rows,
    _require_rows_published_by,
    _require_typed_receipt_key,
    _validate_generation_rows,
    availability_provenance_summary,
)
from agri_data_service.pipeline.parquet.availability_primitives import (
    BOOTSTRAP_INPUT_SCHEMA_VERSION,
    BOOTSTRAP_RECEIPT_MAX_BYTES,
    MANIFEST_TRUSTED_PROVENANCE,
    MAX_INPUT_BYTES,
    PUBLICATION_INPUT_SCHEMA_VERSION,
    SYSTEM_BOOTSTRAP_SCHEMA_VERSION,
    AvailabilityChecksumError,
    AvailabilityConflictError,
    AvailabilityMalformedError,
    _decode_json_object,
    _format_datetime,
    _parse_date,
    _parse_datetime,
    _require_exact_keys,
    _require_sha256,
    _require_string,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class BootstrapRequest:
    """Exact offline bootstrap input and its externally pinned digest."""

    identity: AvailabilityIdentity
    source_ceiling: date
    created_at: datetime
    input_receipts: tuple[EvidenceReceipt, ...]
    rows: tuple[AvailabilityRow, ...]
    input_sha256: str

    @property
    def provenance_summary(self) -> dict[str, object]:
        """Return the per-class row count and day range this input will record in its receipt."""
        return availability_provenance_summary(self.rows)


@dataclass(frozen=True, slots=True)
class PublicationRequest:
    """Exact append or correction input."""

    config: AvailabilityConfig
    created_at: datetime
    rows: tuple[AvailabilityRow, ...]
    input_sha256: str


@dataclass(frozen=True, slots=True)
class PublicationResult:
    """The winning pointer, whether this call advanced it, and the generation it now names."""

    pointer: AvailabilityPointer
    advanced: bool
    attempts: int
    #: The rows the winning generation holds. Carried so the async publication seams can refresh the
    #: coverage rollup without re-reading and re-verifying the generation they just wrote. EMPTY on a
    #: replay that advanced nothing, which is exactly when there is no rollup work to do.
    rows: tuple[AvailabilityRow, ...] = ()


@dataclass(frozen=True, slots=True)
class _RequestRowClassification:
    replayed_grains: tuple[tuple[date, int], ...]
    added_grains: tuple[tuple[date, int], ...]
    conflicting_grains: tuple[tuple[date, int], ...]
    stale_conflicting_grains: tuple[tuple[date, int], ...]

    @property
    def is_exact_replay(self) -> bool:
        return bool(self.replayed_grains) and not self.added_grains and not self.conflicting_grains


def load_bootstrap_request(
    path: Path,
    *,
    expected_sha256: str,
    expected_row_count: int,
) -> BootstrapRequest:
    """Load and validate one externally pinned bootstrap document."""
    value, actual_sha256 = _load_exact_document(path, expected_sha256=expected_sha256)
    _require_exact_keys(
        value,
        {
            "created_at",
            "input_receipts",
            "lane",
            "lane_root",
            "nature",
            "product",
            "required_rungs",
            "rows",
            "schema_version",
            "source_ceiling",
            "verified_source_inventory_root",
        },
        "bootstrap input",
    )
    if value["schema_version"] != BOOTSTRAP_INPUT_SCHEMA_VERSION:
        raise ValueError(f"bootstrap input schema_version must be {BOOTSTRAP_INPUT_SCHEMA_VERSION}")
    identity = _parse_identity(value)
    rows = _parse_rows(value["rows"], identity=identity)
    _require_expected_rows(rows, expected_row_count)
    source_ceiling = _parse_date(value["source_ceiling"], "source_ceiling")
    created_at = _parse_datetime(value["created_at"], "created_at")
    _validate_generation_rows(rows, identity=identity, source_ceiling=source_ceiling)
    _require_rows_published_by(rows, created_at)
    input_receipts = _parse_receipts(value["input_receipts"], "input_receipts")
    if not input_receipts:
        raise ValueError("bootstrap input requires at least one verified manifest/checkpoint receipt")
    if input_receipts != tuple(sorted(input_receipts, key=lambda receipt: receipt.key)):
        raise ValueError("bootstrap input_receipts must use canonical key order")
    for receipt in input_receipts:
        _require_typed_receipt_key(receipt, identity.lane_root, "bootstrap-input")
    return BootstrapRequest(
        identity=identity,
        source_ceiling=source_ceiling,
        created_at=created_at,
        input_receipts=input_receipts,
        rows=rows,
        input_sha256=actual_sha256,
    )


def load_publication_request(
    path: Path,
    *,
    expected_sha256: str,
    expected_row_count: int,
) -> PublicationRequest:
    """Load and validate one externally pinned append/correction document."""
    value, actual_sha256 = _load_exact_document(path, expected_sha256=expected_sha256)
    _require_exact_keys(
        value,
        {
            "bootstrap_receipt_key",
            "bootstrap_receipt_sha256",
            "created_at",
            "lane",
            "lane_root",
            "nature",
            "product",
            "required_rungs",
            "rows",
            "schema_version",
            "source_ceiling",
            "verified_source_inventory_root",
        },
        "publication input",
    )
    if value["schema_version"] != PUBLICATION_INPUT_SCHEMA_VERSION:
        raise ValueError(f"publication input schema_version must be {PUBLICATION_INPUT_SCHEMA_VERSION}")
    identity = _parse_identity(value)
    rows = _parse_rows(value["rows"], identity=identity)
    _require_expected_rows(rows, expected_row_count)
    source_ceiling = _parse_date(value["source_ceiling"], "source_ceiling")
    created_at = _parse_datetime(value["created_at"], "created_at")
    _validate_generation_rows(rows, identity=identity, source_ceiling=source_ceiling)
    _refuse_trusted_publication_rows(rows)
    _require_rows_published_by(rows, created_at)
    return PublicationRequest(
        config=AvailabilityConfig(
            identity=identity,
            source_ceiling=source_ceiling,
            bootstrap_receipt=EvidenceReceipt(
                key=_require_string(value["bootstrap_receipt_key"], "bootstrap_receipt_key"),
                sha256=_require_string(value["bootstrap_receipt_sha256"], "bootstrap_receipt_sha256"),
            ),
        ),
        created_at=created_at,
        rows=rows,
        input_sha256=actual_sha256,
    )


def _refuse_trusted_publication_rows(rows: Sequence[AvailabilityRow]) -> None:
    """Keep the manifest-trusted class to the BOOTSTRAP, which is the only thing that made it necessary.

    A forward publication writes the day it is publishing, so it holds every part's digest already --
    `objectstore.WrittenObjectLedger` recorded them as it uploaded. A trusted row arriving here is
    therefore never a saved download; it is a lost one, and admitting it would let the region owner
    decision D3 bounded to history grow forward one tick at a time.

    CALLED FROM THE SINGLE PUBLISH CHOKEPOINT IN `availability_index.py`, and not only from the document loader.
    The loader serves ONE caller (`interface/cli/data.py`); the PRIMARY forward writer is
    `availability_extension._publish_rows`, which builds its `PublicationRequest` in memory and never
    loads a document at all. A guard on the loader alone would be bypassed by every direct-to-Parquet
    writer owner decision D4 mandates. `load_publication_request` keeps its own call because refusing
    at load time names the offending document before a single object is fetched.
    """
    trusted = [row.grain for row in rows if row.provenance == MANIFEST_TRUSTED_PROVENANCE]
    if trusted:
        rendered = ", ".join(f"{day.isoformat()}/z{rung}" for day, rung in trusted[:5])
        raise ValueError(
            f"publication input carries {len(trusted)} manifest-trusted row(s) ({rendered}); only a bootstrap "
            "may bind a day it did not hash, and a forward publication holds every part digest it wrote"
        )


def _bootstrap_receipt_payload(request: BootstrapRequest) -> bytes:
    outcome_sha256 = sha256_digest(canonical_json([row.to_wire() for row in request.rows]))
    payload = {
        "bootstrap_input_sha256": request.input_sha256,
        "created_at": _format_datetime(request.created_at),
        "input_receipts": [receipt.to_wire() for receipt in request.input_receipts],
        "lane": request.identity.lane,
        "lane_root": request.identity.lane_root,
        "nature": request.identity.nature,
        "outcome_sha256": outcome_sha256,
        "product": request.identity.product,
        # THE WEAKER PROVENANCE, RECORDED WHERE IT CANNOT BE MISSED (spec tripwire, D3): how many
        # rows of this lane were bound by digest, how many by manifest trust, and the day range of
        # each. Derived from the rows themselves, so a replayed bootstrap re-writes identical bytes.
        "provenance": availability_provenance_summary(request.rows),
        "required_rungs": list(request.identity.required_rungs),
        "row_count": len(request.rows),
        "schema_version": SYSTEM_BOOTSTRAP_SCHEMA_VERSION,
        "source_ceiling": request.source_ceiling.isoformat(),
        "verified_source_inventory_root": request.identity.verified_source_inventory_root,
    }
    encoded = canonical_json(payload).encode("utf-8")
    if len(encoded) > BOOTSTRAP_RECEIPT_MAX_BYTES:
        raise AvailabilityMalformedError("system bootstrap receipt exceeds its byte ceiling")
    return encoded


def _merge_rows(
    existing: tuple[AvailabilityRow, ...],
    changes: tuple[AvailabilityRow, ...],
) -> tuple[AvailabilityRow, ...]:
    merged = {row.grain: row for row in existing}
    merged.update((row.grain, row) for row in changes)
    return tuple(sorted(merged.values(), key=lambda row: (row.day, row.rung)))


def _classify_request_rows(
    existing: tuple[AvailabilityRow, ...],
    requested: tuple[AvailabilityRow, ...],
) -> _RequestRowClassification:
    existing_by_grain = {row.grain: row for row in existing}
    replayed: list[tuple[date, int]] = []
    added: list[tuple[date, int]] = []
    conflicting: list[tuple[date, int]] = []
    stale_conflicting: list[tuple[date, int]] = []
    for row in requested:
        held = existing_by_grain.get(row.grain)
        if held is None:
            added.append(row.grain)
        elif held == row:
            replayed.append(row.grain)
        else:
            conflicting.append(row.grain)
            if row.published_at <= held.published_at:
                stale_conflicting.append(row.grain)
    return _RequestRowClassification(
        replayed_grains=tuple(replayed),
        added_grains=tuple(added),
        conflicting_grains=tuple(conflicting),
        stale_conflicting_grains=tuple(stale_conflicting),
    )


def _logical_created_at(requested: datetime, winning: datetime) -> datetime:
    return max(requested, winning + timedelta(microseconds=1))


def _require_config_compatible(config: AvailabilityConfig, pointer: AvailabilityPointer) -> None:
    if config.identity != pointer.identity or config.bootstrap_receipt != pointer.bootstrap_receipt:
        raise AvailabilityConflictError("publication input does not match the lane's immutable bootstrap contract")


def _load_exact_document(path: Path, *, expected_sha256: str) -> tuple[dict[str, object], str]:
    _require_sha256(expected_sha256, "input sha256")
    with path.open("rb") as source:
        payload = source.read(MAX_INPUT_BYTES + 1)
    if len(payload) > MAX_INPUT_BYTES:
        raise ValueError(f"availability input exceeds {MAX_INPUT_BYTES} bytes")
    actual_sha256 = sha256_digest(payload)
    if actual_sha256 != expected_sha256:
        raise AvailabilityChecksumError(
            f"availability input checksum mismatch: expected {expected_sha256}, got {actual_sha256}"
        )
    return _decode_json_object(payload, "availability input"), actual_sha256
