"""Shared builders for externally pinned availability input documents and the store they cite.

Extracted from `test_availability_provenance.py` so the CLI adapter test can validate a document the
contract's own loader accepts without restating ~180 lines of evidence seeding. The two evidence
classes these builders produce are explained there; this module owns only their construction.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.foundation.parquet.completion import CompletedPart, PartitionCompletion
from agri_data_service.pipeline.parquet.availability_index import (
    BOOTSTRAP_INPUT_SCHEMA_VERSION,
    DIGESTED_PROVENANCE,
    MANIFEST_TRUSTED_PROVENANCE,
    PUBLICATION_INPUT_SCHEMA_VERSION,
    AvailabilityConflictError,
    AvailabilityIdentity,
    BootstrapInventoryEvidence,
    BootstrapRequest,
    EvidenceReceipt,
    SourceEvidence,
    StoredAvailabilityObject,
    TerminalEvidence,
    availability_row_from_terminal_evidence,
    build_bootstrap_inventory_evidence,
    build_source_evidence,
    build_terminal_evidence,
    compute_verified_source_inventory_root,
)
from agri_data_service.warehouse.schemas.availability_index import AVAILABILITY_REQUIRED_RUNGS

if TYPE_CHECKING:
    from pathlib import Path

    from agri_data_service.pipeline.parquet.availability_index import AvailabilityProvenance, AvailabilityRow

LANE_ROOT = "layer=test-lane/kind=observed"
CEILING = date(2026, 9, 3)
DIGESTED_DAY = date(2026, 9, 2)
TRUSTED_DAY = date(2020, 3, 1)
FORWARD_TRUSTED_DAY = date(2020, 3, 2)
CREATED_AT = datetime(2026, 9, 3, 12, tzinfo=UTC)
PUBLISHED_AT = CREATED_AT - timedelta(hours=1)
ROW_COUNT = 4


@dataclass
class MemoryAvailabilityStorage:
    """The conditional object operations availability needs, in memory and with a read log."""

    objects: dict[str, StoredAvailabilityObject] = field(default_factory=dict)
    read_log: list[str] = field(default_factory=list)
    version: int = 0

    def seed(self, key: str, payload: bytes) -> EvidenceReceipt:
        """Write one object as a new version and return the receipt a row would cite it by."""
        self.version += 1
        self.objects[key] = StoredAvailabilityObject(
            payload=payload,
            etag=f'"{self.version}"',
            version_id=f"version-{self.version}",
        )
        return EvidenceReceipt(key=key, sha256=sha256_digest(payload))

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        """Return one object, recording the key so a test can prove what was NOT fetched."""
        del max_bytes
        self.read_log.append(key)
        return self.objects.get(key)

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        """Create one object, accepting only an exact idempotent replay."""
        del content_type
        existing = self.objects.get(key)
        if existing is not None:
            if existing.payload != payload:
                raise AvailabilityConflictError("immutable conflict")
            return
        self.seed(key, payload)

    def compare_and_swap(
        self,
        key: str,
        payload: bytes,
        *,
        expected_etag: str | None,
        content_type: str,
    ) -> bool:
        """Advance the pointer only while its comparison token still matches."""
        del content_type
        existing = self.objects.get(key)
        if expected_etag is None:
            if existing is not None:
                return False
        elif existing is None or existing.etag != expected_etag:
            return False
        self.seed(key, payload)
        return True


def write_document(tmp_path: Path, document: dict[str, object], *, name: str = "input.json") -> Path:
    """Write one canonical document and return its path."""
    path = tmp_path / name
    path.write_bytes(canonical_json(document).encode("utf-8"))
    return path


def bootstrap_document(request: BootstrapRequest, *, rows: list[dict[str, object]]) -> dict[str, object]:
    """Render the exact bootstrap input document for one already-built request."""
    return {
        "created_at": format_moment(request.created_at),
        "input_receipts": [receipt.to_wire() for receipt in request.input_receipts],
        "lane": request.identity.lane,
        "lane_root": request.identity.lane_root,
        "nature": request.identity.nature,
        "product": request.identity.product,
        "required_rungs": list(request.identity.required_rungs),
        "rows": rows,
        "schema_version": BOOTSTRAP_INPUT_SCHEMA_VERSION,
        "source_ceiling": request.source_ceiling.isoformat(),
        "verified_source_inventory_root": request.identity.verified_source_inventory_root,
    }


def publication_document(
    request: BootstrapRequest,
    *,
    rows: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Render the exact append/correction input document; defaults to the trusted day's rows."""
    return {
        "bootstrap_receipt_key": f"{LANE_ROOT}/availability/bootstrap/receipt={'a' * 64}.json",
        "bootstrap_receipt_sha256": "a" * 64,
        "created_at": format_moment(request.created_at),
        "lane": request.identity.lane,
        "lane_root": request.identity.lane_root,
        "nature": request.identity.nature,
        "product": request.identity.product,
        "required_rungs": list(request.identity.required_rungs),
        "rows": rows if rows is not None else [row.to_wire() for row in request.rows if row.day == TRUSTED_DAY],
        "schema_version": PUBLICATION_INPUT_SCHEMA_VERSION,
        "source_ceiling": request.source_ceiling.isoformat(),
        "verified_source_inventory_root": request.identity.verified_source_inventory_root,
    }


def bootstrap_request(
    store: MemoryAvailabilityStorage,
    *,
    trusted_marker_row_count: int | None = None,
    recorded_digest: str | None = None,
) -> BootstrapRequest:
    """Seed one lane's evidence and return the bootstrap request that binds it."""
    manifest = store.seed("evidence/bootstrap-manifest.json", b"verified manifest")
    identity = AvailabilityIdentity(
        lane_root=LANE_ROOT,
        lane="test-lane",
        product="test-lane",
        nature="daily_series",
        required_rungs=AVAILABILITY_REQUIRED_RUNGS,
        verified_source_inventory_root=compute_verified_source_inventory_root((manifest,)),
    )
    rows = (
        *digested_day_rows(store, identity, DIGESTED_DAY, recorded_digest=recorded_digest),
        *trusted_day_rows(store, identity, TRUSTED_DAY, marker_row_count=trusted_marker_row_count or ROW_COUNT),
    )
    inventory = build_bootstrap_inventory_evidence(
        BootstrapInventoryEvidence(identity=identity, source_ceiling=CEILING, object_receipts=(manifest,))
    )
    store.seed(inventory.receipt.key, inventory.payload)
    return BootstrapRequest(
        identity=identity,
        source_ceiling=CEILING,
        created_at=CREATED_AT,
        input_receipts=(inventory.receipt,),
        rows=tuple(sorted(rows, key=lambda row: (row.day, row.rung))),
        input_sha256=sha256_digest(b"bootstrap input"),
    )


def digested_day_rows(
    store: MemoryAvailabilityStorage,
    identity: AvailabilityIdentity,
    day: date,
    *,
    recorded_digest: str | None = None,
) -> tuple[AvailabilityRow, ...]:
    """Seed one day whose parts exist, are hashed, and are RECORDED by their own completion marker."""
    source = seed_source(store, identity, day)
    rows: list[AvailabilityRow] = []
    for rung in AVAILABILITY_REQUIRED_RUNGS:
        payload = parquet_payload(ROW_COUNT)
        data = store.seed(part_key(day, rung, 0), payload)
        completion = PartitionCompletion(
            part_count=1,
            row_count=ROW_COUNT,
            completed_at=PUBLISHED_AT,
            run_id=f"test-{day}-z{rung}",
            parts=(
                CompletedPart(
                    relative_path=data.key,
                    row_count=ROW_COUNT,
                    byte_count=len(payload),
                    sha256=recorded_digest or data.sha256,
                ),
            ),
        )
        rows.append(
            _row(
                store,
                identity=identity,
                day=day,
                rung=rung,
                source=source,
                completion=completion,
                data_receipts=(data,),
                provenance=DIGESTED_PROVENANCE,
            )
        )
    return tuple(rows)


def trusted_day_rows(
    store: MemoryAvailabilityStorage,
    identity: AvailabilityIdentity,
    day: date,
    *,
    marker_row_count: int,
) -> tuple[AvailabilityRow, ...]:
    """Seed one day bound WITHOUT its parts: no part object is written and none is ever cited."""
    source = seed_source(store, identity, day)
    rows: list[AvailabilityRow] = []
    for rung in AVAILABILITY_REQUIRED_RUNGS:
        completion = PartitionCompletion(
            part_count=1,
            row_count=marker_row_count,
            completed_at=PUBLISHED_AT,
            run_id=f"test-{day}-z{rung}",
        )
        rows.append(
            _row(
                store,
                identity=identity,
                day=day,
                rung=rung,
                source=source,
                completion=completion,
                data_receipts=(),
                provenance=MANIFEST_TRUSTED_PROVENANCE,
            )
        )
    return tuple(rows)


def _row(  # noqa: PLR0913 - one coordinate of the row being seeded per argument
    store: MemoryAvailabilityStorage,
    *,
    identity: AvailabilityIdentity,
    day: date,
    rung: int,
    source: EvidenceReceipt,
    completion: PartitionCompletion,
    data_receipts: tuple[EvidenceReceipt, ...],
    provenance: AvailabilityProvenance,
) -> AvailabilityRow:
    marker = store.seed(completion_key(day, rung), completion.to_json_bytes())
    evidence = TerminalEvidence(
        identity=identity,
        day=day,
        rung=rung,
        terminal_state="published",
        row_count=ROW_COUNT,
        source_ceiling=CEILING,
        published_at=PUBLISHED_AT,
        source_receipt=source,
        data_receipts=data_receipts,
        completion_receipt=marker,
        absence_receipt=None,
        absence_reason=None,
        provenance=provenance,
    )
    artifact = build_terminal_evidence(evidence)
    return availability_row_from_terminal_evidence(
        evidence,
        terminal_receipt=store.seed(artifact.receipt.key, artifact.payload),
    )


def seed_source(store: MemoryAvailabilityStorage, identity: AvailabilityIdentity, day: date) -> EvidenceReceipt:
    """Seed one day's source evidence wrapper and return its receipt."""
    source_object = store.seed(f"source/{day}/response.bin", f"source:{day}".encode())
    evidence = build_source_evidence(
        SourceEvidence(identity=identity, day=day, source_ceiling=CEILING, object_receipts=(source_object,))
    )
    return store.seed(evidence.receipt.key, evidence.payload)


def day_prefix(day: date, rung: int) -> str:
    """Return one lane-day-rung partition prefix."""
    return f"{LANE_ROOT}/zoom={rung:02d}/year={day.year:04d}/month={day.month:02d}/day={day.day:02d}"


def part_key(day: date, rung: int, part_index: int) -> str:
    """Return one part object key."""
    return f"{day_prefix(day, rung)}/part-{part_index}.parquet"


def completion_key(day: date, rung: int) -> str:
    """Return one completion marker key."""
    return f"{day_prefix(day, rung)}/_complete.json"


def parquet_payload(row_count: int) -> bytes:
    """Return a minimal Parquet payload holding the given row count."""
    sink = io.BytesIO()
    pq.write_table(pa.table({"value": pa.nulls(row_count, type=pa.int8())}), sink)
    return sink.getvalue()


def format_moment(moment: datetime) -> str:
    """Render one UTC instant exactly as the input documents spell it."""
    rendered = moment.astimezone(UTC).isoformat(timespec="microseconds")
    return f"{rendered[:-6]}Z"
