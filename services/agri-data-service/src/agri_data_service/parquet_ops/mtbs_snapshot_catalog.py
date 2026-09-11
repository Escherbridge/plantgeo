"""Admit current snapshots only through verified availability evidence; see parquet_ops/AGENTS.md."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.completion import COMPLETION_SCHEMA_VERSION, PartitionCompletion
from agri_data_service.foundation.parquet.paths import (
    absence_marker_path,
    completion_marker_path,
    day_prefix,
    derived_empty_completion_marker_path,
)
from agri_data_service.foundation.parquet.zoom import validate_zoom_tier
from agri_data_service.parquet_ops.faults import ServingRefusalError
from agri_data_service.pipeline.parquet.availability_extension import LANE_EXPORT_SOURCE_SCHEMA_VERSION
from agri_data_service.pipeline.parquet.availability_index import (
    AvailabilityError,
    AvailabilityUnavailableError,
    BotoAvailabilityStorage,
    EvidenceReceipt,
    SourceEvidence,
    TerminalEvidence,
    build_source_evidence,
    build_terminal_evidence,
    read_bootstrap_marker,
    read_latest_availability,
)
from agri_data_service.warehouse.mtbs_snapshots import (
    MTBS_SNAPSHOT_FIRST_DAY,
    MTBS_SNAPSHOT_LANE_ROOT,
    MtbsSnapshotDescriptor,
    descriptor_from_manifest,
)
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from agri_data_service.config import Settings
    from agri_data_service.pipeline.parquet.availability_index import (
        AvailabilityIndex,
        AvailabilityRow,
        AvailabilityStorage,
        StoredAvailabilityObject,
    )

MAX_METADATA_READS: Final = 20
MAX_METADATA_BYTES: Final = 8 * 1024 * 1024
MAX_DOCUMENT_BYTES: Final = 512 * 1024
RUNGS: Final = (0, 5, 9, 13)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


class _ReadBudget:
    def __init__(self, storage: AvailabilityStorage) -> None:
        self.storage = storage
        self.reads = 0
        self.bytes = 0

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        self.reads += 1
        if self.reads > MAX_METADATA_READS or self.bytes >= MAX_METADATA_BYTES:
            raise ValueError("snapshot metadata budget exceeded")
        stored = self.storage.read(key, max_bytes=min(max_bytes, MAX_METADATA_BYTES - self.bytes))
        if stored is not None:
            self.bytes += len(stored.payload)
            _require(self.bytes <= MAX_METADATA_BYTES, "snapshot metadata bytes exceeded")
        return stored

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        del key, payload, content_type
        raise RuntimeError("snapshot admission is read-only")

    def compare_and_swap(self, key: str, payload: bytes, *, expected_etag: str | None, content_type: str) -> bool:
        del key, payload, expected_etag, content_type
        raise RuntimeError("snapshot admission is read-only")


def _bytes(storage: _ReadBudget, receipt: EvidenceReceipt) -> bytes:
    _require(receipt.key.startswith(MTBS_SNAPSHOT_LANE_ROOT + "/"), "snapshot evidence leaves its lane")
    value = storage.read(receipt.key, max_bytes=MAX_DOCUMENT_BYTES)
    _require(value is not None, "snapshot evidence missing")
    if value is None:
        raise ValueError("snapshot evidence missing")
    _require(hashlib.sha256(value.payload).hexdigest() == receipt.sha256, "snapshot evidence digest mismatch")
    return value.payload


def _receipt(value: object) -> EvidenceReceipt:
    _require(isinstance(value, dict) and set(value) == {"key", "sha256"}, "invalid snapshot evidence receipt")
    if not isinstance(value, dict):
        raise ValueError("invalid snapshot evidence receipt")
    return EvidenceReceipt(key=value["key"], sha256=value["sha256"])


@dataclass(frozen=True, slots=True)
class VerifiedMtbsSnapshot:
    """One availability-indexed replacement day with all-rung metadata bound to its source."""

    descriptor: MtbsSnapshotDescriptor
    rows: tuple[AvailabilityRow, ...]
    storage: AvailabilityStorage

    def rung(self, tier: int) -> AvailabilityRow:
        return next(row for row in self.rows if row.rung == tier)


def _source(
    budget: _ReadBudget, index: AvailabilityIndex, rows: tuple[AvailabilityRow, ...]
) -> tuple[MtbsSnapshotDescriptor, Mapping[str, object]]:
    base = next(row for row in rows if row.rung == BASE_ZOOM_TIER)
    payload = _bytes(budget, base.source_receipt)
    source = json.loads(payload)
    objects = tuple(_receipt(value) for value in source["object_receipts"])
    _require(len(objects) == 1, "snapshot source must bind one exact export document")
    artifact = build_source_evidence(
        SourceEvidence(
            identity=index.pointer.identity,
            day=base.day,
            source_ceiling=base.source_ceiling,
            object_receipts=objects,
        )
    )
    _require(artifact.receipt == base.source_receipt and artifact.payload == payload, "snapshot typed source differs")
    export_payload = _bytes(budget, objects[0])
    export = json.loads(export_payload)
    expected_export_key = (
        f"{MTBS_SNAPSHOT_LANE_ROOT}/availability/source/day={base.day.isoformat()}/export={objects[0].sha256}.json"
    )
    _require(objects[0].key == expected_export_key, "snapshot export source path differs")
    _require(
        export["schema_version"] == LANE_EXPORT_SOURCE_SCHEMA_VERSION and export["origin"] == "mtbs-current-snapshot",
        "unregistered current snapshot source",
    )
    _require(
        export["day"] == base.day.isoformat() and export["lane_root"] == MTBS_SNAPSHOT_LANE_ROOT,
        "snapshot export scope differs",
    )
    _require(isinstance(export["run_id"], str) and bool(export["run_id"]), "snapshot run identity missing")
    detail = json.loads(export["detail"])
    _require(
        set(detail) == {"schema_version", "manifest", "descriptor"}
        and detail["schema_version"] == "mtbs-current-snapshot-binding/v1",
        "snapshot binding schema differs",
    )
    declared = MtbsSnapshotDescriptor.from_wire(detail["descriptor"])
    manifest = _receipt(detail["manifest"])
    _require(
        manifest.key == declared.manifest_key and manifest.sha256 == declared.manifest_sha256,
        "snapshot manifest path binding differs",
    )
    verified = descriptor_from_manifest(_bytes(budget, manifest), expected_sha256=manifest.sha256)
    _require(verified == declared and verified.available_day == base.day, "snapshot availability day differs")
    _require(
        type(export["row_count"]) is int and export["row_count"] == verified.source_row_count == base.row_count,
        "snapshot base count differs",
    )
    _require(
        type(export["part_count"]) is int and export["part_count"] == len(base.data_receipts),
        "snapshot base part count differs",
    )
    published = datetime.fromisoformat(export["exported_at"])
    _require(
        published.utcoffset() == timedelta(0) and published.date() >= base.day, "snapshot export predates availability"
    )
    return verified, export


def _terminal(
    budget: _ReadBudget, index: AvailabilityIndex, row: AvailabilityRow, export: Mapping[str, object]
) -> None:
    payload = _bytes(budget, row.terminal_receipt)
    value = json.loads(payload)
    absence = None if value["absence_receipt"] is None else _receipt(value["absence_receipt"])
    evidence = TerminalEvidence(
        identity=index.pointer.identity,
        day=row.day,
        rung=row.rung,
        terminal_state=row.terminal_state,
        row_count=row.row_count,
        source_ceiling=row.source_ceiling,
        published_at=row.published_at,
        source_receipt=row.source_receipt,
        data_receipts=row.data_receipts,
        completion_receipt=row.completion_receipt,
        absence_receipt=absence,
        absence_reason=row.absence_reason,
    )
    artifact = build_terminal_evidence(evidence)
    _require(
        artifact.receipt == row.terminal_receipt and artifact.payload == payload, "snapshot typed terminal differs"
    )
    _require(row.published_at.date() >= row.day, "snapshot published before availability")
    tier = validate_zoom_tier(row.rung)
    prefix = day_prefix("burn-severity", "observed", tier, row.day)
    _require(
        all(receipt.key.startswith(prefix) for receipt in row.data_receipts), "snapshot part leaves exact day/rung"
    )
    if row.terminal_state == "governed_absence":
        _require(
            absence is not None and absence.key == absence_marker_path("burn-severity", "observed", tier, row.day),
            "snapshot absence path differs",
        )
        if absence is None:
            raise ValueError("snapshot absence missing")
        absence_marker = GovernedAbsence.from_json_bytes(_bytes(budget, absence))
        _require(
            absence_marker.run_id == export["run_id"] and absence_marker.reason == row.absence_reason,
            "snapshot absence source differs",
        )
    else:
        completion = row.completion_receipt
        marker_path = (
            derived_empty_completion_marker_path
            if row.terminal_state == "published" and row.row_count == 0
            else completion_marker_path
        )
        _require(
            completion is not None and completion.key == marker_path("burn-severity", "observed", tier, row.day),
            "snapshot completion path differs",
        )
        if completion is None:
            raise ValueError("snapshot completion missing")
        marker_payload = _bytes(budget, completion)
        marker = PartitionCompletion.from_json_bytes(marker_payload)
        _require(
            marker.derived_empty == (row.terminal_state == "published" and row.row_count == 0),
            "snapshot empty marker differs",
        )
        _require(
            (
                tier == BASE_ZOOM_TIER
                and marker.schema_version == COMPLETION_SCHEMA_VERSION
                and marker.to_json_bytes() == marker_payload
            )
            or tuple(EvidenceReceipt(key=part.relative_path, sha256=part.sha256) for part in marker.parts)
            == row.data_receipts,
            "snapshot part digests differ from completion",
        )
        _require(
            marker.run_id == export["run_id"]
            and marker.row_count == row.row_count
            and marker.part_count == len(row.data_receipts),
            "snapshot completion counts/run differ",
        )


def load_latest_mtbs_snapshot(
    storage: AvailabilityStorage, *, as_of: date, now: datetime
) -> VerifiedMtbsSnapshot | None:
    """Validate only the newest eligible indexed snapshot, never fall back after a failed proof."""
    if as_of < MTBS_SNAPSHOT_FIRST_DAY or now.date() < MTBS_SNAPSHOT_FIRST_DAY:
        return None
    budget = _ReadBudget(storage)
    try:
        _require(now.utcoffset() == timedelta(0), "snapshot clock must be UTC-aware")
        try:
            index = read_latest_availability(
                budget,
                lane_root=MTBS_SNAPSHOT_LANE_ROOT,
                expected_lane="burn-severity",
                expected_product="burn-severity",
                expected_nature="release_series",
                expected_required_rungs=RUNGS,
            )
        except AvailabilityUnavailableError as unavailable:
            if unavailable.code != "availability_missing":
                raise
            if read_bootstrap_marker(budget, lane_root=MTBS_SNAPSHOT_LANE_ROOT) is not None:
                raise ValueError("snapshot availability pointer lost after bootstrap") from None
            return None
        candidates = [row.day for row in index.rows if MTBS_SNAPSHOT_FIRST_DAY <= row.day <= min(as_of, now.date())]
        if not candidates:
            return None
        day = max(candidates)
        rows = tuple(sorted((row for row in index.rows if row.day == day), key=lambda row: row.rung))
        _require(tuple(row.rung for row in rows) == RUNGS, "snapshot lacks its complete rung set")
        _require(
            len({row.source_receipt for row in rows}) == 1 and len({row.source_ceiling for row in rows}) == 1,
            "snapshot rung sources differ",
        )
        descriptor, export = _source(budget, index, rows)
        expected_states = {"published"} if descriptor.source_row_count else {"governed_absence"}
        _require(
            all(
                row.terminal_state in expected_states and row.provenance == "digested" and row.published_at <= now
                for row in rows
            ),
            "snapshot states/provenance differ",
        )
        for row in rows:
            _terminal(budget, index, row, export)
        return VerifiedMtbsSnapshot(descriptor, rows, storage)
    except (AvailabilityError, ValueError, KeyError, TypeError, StopIteration) as error:
        raise ServingRefusalError(
            "mtbs_snapshot_invalid", "Current MTBS snapshot evidence could not be verified"
        ) from error


def configured_snapshot_loader(settings: Settings) -> Callable[[date], VerifiedMtbsSnapshot | None]:
    """Inject lazily constructed real object-store metadata reads into HTTP and CLI listings."""

    def load(as_of: date) -> VerifiedMtbsSnapshot | None:
        return load_latest_mtbs_snapshot(
            BotoAvailabilityStorage.from_settings(settings), as_of=as_of, now=datetime.now(UTC)
        )

    return load


def verify_snapshot_absence(snapshot: VerifiedMtbsSnapshot, *, tier: int, payload: bytes) -> None:
    """Bind the served small absence marker to its already verified terminal receipt."""
    try:
        terminal = json.loads(_bytes(_ReadBudget(snapshot.storage), snapshot.rung(tier).terminal_receipt))
        receipt = _receipt(terminal["absence_receipt"])
        if hashlib.sha256(payload).hexdigest() != receipt.sha256:
            raise ValueError("snapshot absence marker changed")
    except (AvailabilityError, ValueError, KeyError, TypeError) as error:
        raise ServingRefusalError(
            "mtbs_snapshot_stale", "Current MTBS absence differs from its snapshot evidence"
        ) from error
