"""The two immutable bootstrap documents a lane root needs before any generation may cite it.

Layer L3, spec FR-4a. Split out of `forecast_lane_bootstrap.py` when that module passed the size
ceiling. Both documents are byte-compatible with agri-data-service's
`pipeline/parquet/availability_documents.py` on purpose: its
`parquet_ops/availability_coverage.py` re-serializes them and refuses any difference, and
`put_immutable` makes the first attempt permanent. Rationale lives in `AGENTS-forecast-lanes.md`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

from plantgeo_ml_service.foundation.canonical import canonical_json, sha256_digest
from plantgeo_ml_service.foundation.parquet_paths import availability_bootstrap_marker_key
from plantgeo_ml_service.pipeline.forecast_lane_rungs import ForecastLaneError
from plantgeo_ml_service.pipeline.object_store import JSON_CONTENT_TYPE
from plantgeo_ml_service.warehouse.availability import (
    DIGESTED_PROVENANCE,
    MANIFEST_TRUSTED_PROVENANCE,
    AvailabilityIdentity,
    AvailabilityRow,
    EvidenceReceipt,
    format_datetime,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date, datetime

    from plantgeo_ml_service.pipeline.object_store import ObjectStore

#: Verbatim from the sibling's `pipeline/parquet/availability_primitives.py`. These two strings are
#: parsed by its reader, so they are constants to match, not names to choose.
BOOTSTRAP_MARKER_SCHEMA_VERSION: Final = "availability-bootstrap-marker-v1"
SYSTEM_BOOTSTRAP_SCHEMA_VERSION: Final = "availability-system-bootstrap-v1"


def availability_provenance_summary(rows: Sequence[AvailabilityRow]) -> dict[str, object]:
    """Return the per-class row count and day range a bootstrap receipt carries, in canonical spelling.

    A copy of the sibling's `availability_documents.py::availability_provenance_summary`, because the
    receipt bytes it produces are parsed and re-verified there; it belongs in `warehouse/availability.py`
    and is requested for that module in this slice's evidence file.
    """
    summary: dict[str, object] = {}
    for provenance in (DIGESTED_PROVENANCE, MANIFEST_TRUSTED_PROVENANCE):
        days = sorted({row.day for row in rows if row.provenance == provenance})
        summary[provenance] = {
            "earliest_day": days[0].isoformat() if days else None,
            "latest_day": days[-1].isoformat() if days else None,
            "row_count": sum(1 for row in rows if row.provenance == provenance),
        }
    return summary


def bootstrap_receipt_payload(
    rows: Sequence[AvailabilityRow],
    *,
    identity: AvailabilityIdentity,
    source_receipt: EvidenceReceipt,
    source_ceiling: date,
    created_at: datetime,
) -> bytes:
    """Render the system bootstrap receipt, field for field as the sibling's writer renders it."""
    input_receipts = [source_receipt.to_wire()]
    payload = {
        "bootstrap_input_sha256": sha256_digest(canonical_json(input_receipts)),
        "created_at": format_datetime(created_at),
        "input_receipts": input_receipts,
        "lane": identity.lane,
        "lane_root": identity.lane_root,
        "nature": identity.nature,
        "outcome_sha256": sha256_digest(canonical_json([row.to_wire() for row in rows])),
        "product": identity.product,
        "provenance": availability_provenance_summary(rows),
        "required_rungs": list(identity.required_rungs),
        "row_count": len(rows),
        "schema_version": SYSTEM_BOOTSTRAP_SCHEMA_VERSION,
        "source_ceiling": source_ceiling.isoformat(),
        "verified_source_inventory_root": identity.verified_source_inventory_root,
    }
    return canonical_json(payload).encode("utf-8")


def bootstrap_marker_payload(lane_root: str, receipt: EvidenceReceipt) -> bytes:
    """Render the marker. Receipt-derived ONLY, so a re-attempt writes byte-identical immutable content."""
    return canonical_json(
        {
            "bootstrap_receipt_key": receipt.key,
            "bootstrap_receipt_sha256": receipt.sha256,
            "lane_root": lane_root,
            "schema_version": BOOTSTRAP_MARKER_SCHEMA_VERSION,
        }
    ).encode("utf-8")


def bootstrap_lane(  # noqa: PLR0913 - one keyword per bootstrap boundary is the contract
    store: ObjectStore,
    rows: Sequence[AvailabilityRow],
    *,
    identity: AvailabilityIdentity,
    source_receipt: EvidenceReceipt,
    source_ceiling: date,
    created_at: datetime,
) -> EvidenceReceipt:
    """Create ANY lane root's content-addressed bootstrap receipt and the marker naming it, idempotently.

    The one bootstrap path in this service, whatever a lane's kind or nature: the sibling's
    `parquet_ops/availability_coverage.py` refuses a marker that is not exactly these four fields
    naming a receipt that exists, and a lane whose writer invented its own marker shape published
    an index nothing downstream would admit — permanently, because `put_immutable` never rewrites.

    Both objects go through `put_immutable`, so a replayed bootstrap adopts its own bytes and a
    DIFFERENT bootstrap is refused rather than quietly beginning a second history.
    """
    body = bootstrap_receipt_payload(
        rows,
        identity=identity,
        source_receipt=source_receipt,
        source_ceiling=source_ceiling,
        created_at=created_at,
    )
    digest = sha256_digest(body)
    lane_root = identity.lane_root
    receipt = EvidenceReceipt(key=f"{lane_root}/availability/bootstrap/receipt={digest}.json", sha256=digest)
    # The RECEIPT first: the marker names it, so a marker landing before its receipt would point a
    # reader at an object that is not there yet, and the marker is permanent once written.
    store.put_immutable(receipt.key, body, content_type=JSON_CONTENT_TYPE)
    store.put_immutable(
        availability_bootstrap_marker_key(lane_root),
        bootstrap_marker_payload(lane_root, receipt),
        content_type=JSON_CONTENT_TYPE,
    )
    return receipt


def ensure_lane_bootstrap(  # noqa: PLR0913 - one keyword per bootstrap boundary is the contract
    store: ObjectStore,
    rows: Sequence[AvailabilityRow],
    *,
    identity: AvailabilityIdentity,
    source_receipt: EvidenceReceipt,
    source_ceiling: date,
    created_at: datetime,
) -> EvidenceReceipt:
    """Return the lane root's bootstrap receipt, creating it from this run's rows when it has none.

    The receipt already on the lane WINS: it is the immutable first generation every later pointer
    binds, and re-deriving it from today's rows would produce a second, contradictory history.
    """
    existing = read_lane_bootstrap_receipt(store, lane_root=identity.lane_root)
    if existing is not None:
        return existing
    return bootstrap_lane(
        store,
        rows,
        identity=identity,
        source_receipt=source_receipt,
        source_ceiling=source_ceiling,
        created_at=created_at,
    )


def read_lane_bootstrap_receipt(store: ObjectStore, *, lane_root: str) -> EvidenceReceipt | None:
    """Return the bootstrap receipt this lane root's marker names, or `None` when it has never been bootstrapped.

    ONE GET and never a listing, so a lane that has no availability history answers without a scan.
    """
    payload = store.read_object(availability_bootstrap_marker_key(lane_root))
    if payload is None:
        return None
    expected_fields = {"bootstrap_receipt_key", "bootstrap_receipt_sha256", "lane_root", "schema_version"}
    document = _decoded_marker(payload, expected_fields=expected_fields, lane_root=lane_root)
    return EvidenceReceipt(
        key=str(document["bootstrap_receipt_key"]),
        sha256=str(document["bootstrap_receipt_sha256"]),
    )


def _decoded_marker(payload: bytes, *, expected_fields: set[str], lane_root: str) -> Mapping[str, object]:
    """Decode a bootstrap marker, refusing any shape the sibling's reader would refuse."""
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ForecastLaneError(f"the bootstrap marker of {lane_root!r} is not decodable JSON") from error
    if not isinstance(document, dict) or set(document) != expected_fields:
        raise ForecastLaneError(f"the bootstrap marker of {lane_root!r} does not carry exactly its declared fields")
    if document.get("schema_version") != BOOTSTRAP_MARKER_SCHEMA_VERSION:
        raise ForecastLaneError(f"the bootstrap marker of {lane_root!r} declares an unknown schema version")
    if document.get("lane_root") != lane_root:
        raise ForecastLaneError(f"the bootstrap marker filed under {lane_root!r} describes another lane")
    return document


__all__ = [
    "BOOTSTRAP_MARKER_SCHEMA_VERSION",
    "SYSTEM_BOOTSTRAP_SCHEMA_VERSION",
    "availability_provenance_summary",
    "bootstrap_lane",
    "bootstrap_marker_payload",
    "bootstrap_receipt_payload",
    "ensure_lane_bootstrap",
    "read_lane_bootstrap_receipt",
]
