"""Compile a bounded physical-ladder audit into an availability-only publication request.

This module never writes data parts or availability state. See `AGENTS.md`, "Physical-ladder
availability reconciliation", for the operator boundary and evidence model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final, cast

from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.foundation.parquet.paths import try_parse_partition_path
from agri_data_service.pipeline.parquet.availability_documents import EvidenceReceipt
from agri_data_service.pipeline.parquet.availability_evidence import (
    SourceEvidence,
    TerminalEvidence,
    TypedEvidenceArtifact,
    availability_row_from_terminal_evidence,
    build_source_evidence,
    build_terminal_evidence,
)
from agri_data_service.pipeline.parquet.availability_index import read_latest_availability
from agri_data_service.pipeline.parquet.availability_primitives import (
    PUBLICATION_INPUT_SCHEMA_VERSION,
    _format_datetime,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore, availability_lane_root

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.availability_documents import (
        AvailabilityIndex,
        AvailabilityRow,
    )
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage
    from agri_data_service.pipeline.parquet.objectstore import CompletionMarkerRead, ReadPartReceipt

MAX_RECONCILIATION_DAYS: Final = 366


class AvailabilityReconciliationError(RuntimeError):
    """A physical ladder cannot safely become an availability publication."""


@dataclass(frozen=True, slots=True)
class ReconciliationCompilation:
    """Deterministic publication bytes, evidence objects, and the head they were audited against."""

    document: bytes
    document_sha256: str
    artifacts: tuple[TypedEvidenceArtifact, ...]
    head_generation_key: str
    head_generation_sha256: str
    head_pointer_sha256: str
    candidate_days: tuple[date, ...]
    already_blessed_days: tuple[date, ...]
    row_count: int


@dataclass(frozen=True, slots=True)
class _PhysicalRung:
    rung: int
    row_count: int
    completed_at: datetime
    data_receipts: tuple[EvidenceReceipt, ...]
    completion_receipt: EvidenceReceipt


def compile_physical_ladder_reconciliation(  # noqa: PLR0913 - one bounded range coordinate plus its two stores
    store: ObjectStore,
    availability: AvailabilityStorage,
    *,
    lane: str,
    kind: PartitionKind,
    start_day: date,
    end_day: date,
) -> ReconciliationCompilation:
    """Audit `[start_day, end_day]` and compile only complete, currently unblessed ladders."""
    span = (end_day - start_day).days + 1
    if span < 1 or span > MAX_RECONCILIATION_DAYS:
        raise AvailabilityReconciliationError(
            f"reconciliation range must contain 1..{MAX_RECONCILIATION_DAYS} days, got {span}"
        )
    lane_root = availability_lane_root(lane, kind)
    index = read_latest_availability(
        availability,
        lane_root=lane_root,
        expected_lane=lane,
    )
    if start_day < index.pointer.earliest_terminal_day:
        raise AvailabilityReconciliationError(
            f"range starts at {start_day.isoformat()}, before the current generation's authorized earliest "
            f"terminal day {index.pointer.earliest_terminal_day.isoformat()}; widening history requires a "
            "separately pinned source-contract/bootstrap decision"
        )
    if end_day > index.pointer.source_ceiling:
        raise AvailabilityReconciliationError(
            f"range ends at {end_day.isoformat()}, beyond head source ceiling "
            f"{index.pointer.source_ceiling.isoformat()}"
        )

    by_day: dict[date, tuple[AvailabilityRow, ...]] = {}
    for day in (start_day + timedelta(days=offset) for offset in range(span)):
        by_day[day] = tuple(row for row in index.rows if row.day == day)

    candidate_rows: list[AvailabilityRow] = []
    artifacts: list[TypedEvidenceArtifact] = []
    candidate_days: list[date] = []
    blessed_days: list[date] = []
    for day, held in by_day.items():
        rungs = tuple(
            _read_complete_rung(store, lane=lane, kind=kind, day=day, rung=rung)
            for rung in index.pointer.required_rungs
        )
        published_at = max(item.completed_at for item in rungs).astimezone(UTC)
        source_objects = tuple(
            sorted(
                (
                    *(receipt for item in rungs for receipt in item.data_receipts),
                    *(item.completion_receipt for item in rungs),
                ),
                key=lambda receipt: receipt.key,
            )
        )
        source = build_source_evidence(
            SourceEvidence(
                identity=index.pointer.identity,
                day=day,
                source_ceiling=index.pointer.source_ceiling,
                object_receipts=source_objects,
            )
        )
        day_rows: list[AvailabilityRow] = []
        day_artifacts: list[TypedEvidenceArtifact] = [source]
        for item in rungs:
            terminal = TerminalEvidence(
                identity=index.pointer.identity,
                day=day,
                rung=item.rung,
                terminal_state="published",
                row_count=item.row_count,
                source_ceiling=index.pointer.source_ceiling,
                published_at=published_at,
                source_receipt=source.receipt,
                data_receipts=item.data_receipts,
                completion_receipt=item.completion_receipt,
                absence_receipt=None,
                absence_reason=None,
            )
            terminal_artifact = build_terminal_evidence(terminal)
            day_artifacts.append(terminal_artifact)
            day_rows.append(
                availability_row_from_terminal_evidence(terminal, terminal_receipt=terminal_artifact.receipt)
            )
        if held:
            _require_matching_blessed_day(index, day=day, held=held, physical=day_rows)
            blessed_days.append(day)
            continue
        candidate_days.append(day)
        candidate_rows.extend(day_rows)
        artifacts.extend(day_artifacts)

    if not candidate_rows:
        raise AvailabilityReconciliationError("range contains no physically complete unblessed day")
    created_at = max(index.pointer.created_at, *(row.published_at for row in candidate_rows)).astimezone(UTC)
    identity = index.pointer.identity
    document = canonical_json(
        {
            "bootstrap_receipt_key": index.pointer.bootstrap_receipt.key,
            "bootstrap_receipt_sha256": index.pointer.bootstrap_receipt.sha256,
            "created_at": _format_datetime(created_at),
            "lane": identity.lane,
            "lane_root": identity.lane_root,
            "nature": identity.nature,
            "product": identity.product,
            "required_rungs": list(identity.required_rungs),
            "rows": [row.to_wire() for row in candidate_rows],
            "schema_version": PUBLICATION_INPUT_SCHEMA_VERSION,
            "source_ceiling": index.pointer.source_ceiling.isoformat(),
            "verified_source_inventory_root": identity.verified_source_inventory_root,
        }
    ).encode("utf-8")
    pointer_payload = canonical_json(index.pointer.to_wire()).encode("utf-8")
    return ReconciliationCompilation(
        document=document,
        document_sha256=sha256_digest(document),
        artifacts=tuple(artifacts),
        head_generation_key=index.pointer.generation_key,
        head_generation_sha256=index.pointer.generation_sha256,
        head_pointer_sha256=sha256_digest(pointer_payload),
        candidate_days=tuple(candidate_days),
        already_blessed_days=tuple(blessed_days),
        row_count=len(candidate_rows),
    )


def install_reconciliation_evidence(
    availability: AvailabilityStorage,
    compilation: ReconciliationCompilation,
) -> None:
    """Install only immutable typed evidence; the existing publisher owns the pointer CAS."""
    for artifact in compilation.artifacts:
        availability.put_immutable(artifact.receipt.key, artifact.payload, content_type="application/json")


def _read_complete_rung(
    store: ObjectStore,
    *,
    lane: str,
    kind: PartitionKind,
    day: date,
    rung: int,
) -> _PhysicalRung:
    """Read and hash one rung, refusing every absence, missing marker, or count/digest mismatch."""
    zoom = cast("ZoomTier", rung)
    if store.absence_exists(lane, kind, zoom, day):
        raise AvailabilityReconciliationError(
            f"{lane}/{kind} {day.isoformat()} z{rung} is governed absent, not a complete physical rung"
        )
    marker = store.read_completion_receipt(lane, kind, zoom, day)
    if marker is None:
        raise AvailabilityReconciliationError(f"{lane}/{kind} {day.isoformat()} z{rung} has no completion marker")
    listed_parts = tuple(
        sorted(
            key
            for key in store.list_partition_keys(lane, kind, zoom, year=day.year, month=day.month)
            if (parsed := try_parse_partition_path(key)) is not None and parsed.day == day
        )
    )
    if marker.completion.derived_empty:
        if listed_parts:
            raise AvailabilityReconciliationError(
                f"{lane}/{kind} {day.isoformat()} z{rung} has {len(listed_parts)} physical part(s) "
                "beside a derived-empty completion marker"
            )
        parts: tuple[ReadPartReceipt, ...] = ()
    else:
        if not marker.completion.parts:
            raise AvailabilityReconciliationError(
                f"{lane}/{kind} {day.isoformat()} z{rung} uses a legacy count-only completion marker; "
                "reconciliation requires marker-recorded part identities"
            )
        try:
            parts = store.read_partition_with_receipts(lane, kind, zoom, day).parts
        except Exception as error:
            raise AvailabilityReconciliationError(
                f"{lane}/{kind} {day.isoformat()} z{rung} parts are unreadable: {type(error).__name__}: {error}"
            ) from error
    _require_marker_matches_parts(marker, parts, lane=lane, kind=kind, day=day, rung=rung)
    return _PhysicalRung(
        rung=rung,
        row_count=sum(part.row_count for part in parts),
        completed_at=marker.completion.completed_at,
        data_receipts=tuple(
            EvidenceReceipt(key=part.relative_path, sha256=part.sha256)
            for part in sorted(parts, key=lambda receipt: receipt.relative_path)
        ),
        completion_receipt=EvidenceReceipt(key=marker.relative_path, sha256=marker.sha256),
    )


def _require_marker_matches_parts(  # noqa: PLR0913 - physical coordinate belongs in every refusal
    marker: CompletionMarkerRead,
    parts: Sequence[ReadPartReceipt],
    *,
    lane: str,
    kind: PartitionKind,
    day: date,
    rung: int,
) -> None:
    label = f"{lane}/{kind} {day.isoformat()} z{rung}"
    if marker.completion.part_count != len(parts):
        raise AvailabilityReconciliationError(
            f"{label} marker claims {marker.completion.part_count} parts but {len(parts)} were read"
        )
    rows = sum(part.row_count for part in parts)
    if marker.completion.row_count != rows:
        raise AvailabilityReconciliationError(
            f"{label} marker claims {marker.completion.row_count} rows but parts hold {rows}"
        )
    if marker.completion.parts:
        actual = tuple(
            (part.relative_path, part.row_count, part.byte_count, part.sha256)
            for part in sorted(parts, key=lambda receipt: receipt.relative_path)
        )
        declared = tuple(
            (part.relative_path, part.row_count, part.byte_count, part.sha256) for part in marker.completion.parts
        )
        if declared != actual:
            raise AvailabilityReconciliationError(f"{label} marker part receipts do not match the physical bytes")


def _require_matching_blessed_day(
    index: AvailabilityIndex,
    *,
    day: date,
    held: Sequence[AvailabilityRow],
    physical: Sequence[AvailabilityRow],
) -> None:
    expected = index.pointer.required_rungs
    ordered = tuple(sorted(held, key=lambda row: row.rung))
    if tuple(row.rung for row in ordered) != tuple(sorted(expected)):
        raise AvailabilityReconciliationError(f"blessed {day.isoformat()} does not hold the exact required ladder")
    physical_by_rung = {row.rung: row for row in physical}
    for row in ordered:
        actual = physical_by_rung[row.rung]
        if (
            row.terminal_state != "published"
            or row.row_count != actual.row_count
            or row.data_receipts != actual.data_receipts
            or row.completion_receipt != actual.completion_receipt
        ):
            raise AvailabilityReconciliationError(
                f"blessed {day.isoformat()} z{row.rung} conflicts with the current physical ladder"
            )


__all__ = [
    "MAX_RECONCILIATION_DAYS",
    "AvailabilityReconciliationError",
    "ReconciliationCompilation",
    "compile_physical_ladder_reconciliation",
    "install_reconciliation_evidence",
]
