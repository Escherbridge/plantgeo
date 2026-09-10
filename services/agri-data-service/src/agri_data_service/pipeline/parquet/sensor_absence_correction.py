"""Exact September 5/6 sensor correction evidence and recovery guards; see AGENTS.md."""

from __future__ import annotations

import io
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Final, cast

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import absence_marker_path, day_prefix
from agri_data_service.pipeline.direct.sensors.adapter import merge_sensors_day
from agri_data_service.pipeline.parquet.availability_extension import (
    FinalizedLaneDay,
    LaneDaySource,
    _claim_from_finalized,
    _claim_from_marker,
    _source_object,
)
from agri_data_service.pipeline.parquet.availability_index import (
    EvidenceReceipt,
    SourceEvidence,
    StoredAvailabilityObject,
    TerminalEvidence,
    availability_row_from_terminal_evidence,
    build_source_evidence,
    build_terminal_evidence,
)
from agri_data_service.pipeline.parquet.derivation import derive_and_write_day_tiers
from agri_data_service.pipeline.parquet.objectstore import (
    CompletionWriteReceipt,
    ListedObject,
    ObjectStore,
    ParquetWriteReceipt,
    WrittenObjectLedger,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.availability_index import (
        AvailabilityIdentity,
        AvailabilityIndex,
        AvailabilityRow,
        AvailabilityStorage,
    )

DAYS: Final = (date(2026, 9, 5), date(2026, 9, 6))
RUNGS: Final[tuple[ZoomTier, ...]] = (0, 5, 9, 13)
LANE_ROOT: Final = "layer=sensors/kind=observed"
CANDIDATE_SHA: Final = "e99bce200991ea1b57ef4c2e60a6c36598c992c4d7ad27e700962c528cf8dd40"
ARCHIVE_SHA: Final = "eb3ca823a0a3319cc42e47c4066893cfd88efd58979bfb1e177949d9eb70537e"
MAX_BYTES: Final = 64 * 1024 * 1024
MAX_OBJECTS: Final = 64
MAX_BASE_ROWS: Final = 4000
MAX_QUIESCENCE_SECONDS: Final = 3600
JSON_CONTENT: Final = "application/json"


def encode(value: object) -> bytes:
    """Encode durable dates and receipts deterministically."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False).encode()


def require(condition: object, detail: str) -> None:
    """Refuse an unproven correction precondition."""
    if not condition:
        raise ValueError(detail)


@dataclass
class CandidateMemory:
    """A bounded local-only backend used by the ordinary writer during preparation."""

    objects: dict[str, bytes]

    def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        del content_type
        require(len(payload) <= MAX_BYTES and len(self.objects) < MAX_OBJECTS, "candidate budget exceeded")
        self.objects[key] = payload

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def list_objects(self, prefix: str) -> Iterator[ListedObject]:
        return iter(ListedObject(key, None) for key in sorted(self.objects) if key.startswith(prefix))

    def size_of(self, key: str) -> int | None:
        value = self.objects.get(key)
        return len(value) if value is not None else None

    def get(self, key: str) -> bytes | None:
        return self.objects.get(key)


@dataclass
class SnapshotStorage:
    """Read-only captured availability evidence, with no mutation implementation."""

    objects: dict[str, StoredAvailabilityObject]

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        held = self.objects.get(key)
        require(held is None or len(held.payload) <= max_bytes, "snapshot object exceeds read bound")
        return held

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        del key, payload, content_type
        raise ValueError("captured evidence is read-only")

    def compare_and_swap(self, key: str, payload: bytes, *, expected_etag: str | None, content_type: str) -> bool:
        del key, payload, expected_etag, content_type
        raise ValueError("captured evidence is read-only")


def build_ladder(
    payload: bytes, *, day: date, run_id: str, published_at: datetime
) -> tuple[CandidateMemory, WrittenObjectLedger]:
    """Produce the exact ordinary writer/deriver objects without external I/O."""
    require(day in DAYS, "correction day is outside September 5/6")
    table = pq.read_table(io.BytesIO(payload))
    table = merge_sensors_day(None, table, day=day).table
    require(0 < table.num_rows <= MAX_BASE_ROWS, "candidate row bound exceeded")
    memory = CandidateMemory({})
    store = ObjectStore(memory)
    with store.recording_written_objects() as ledger:
        store.write_partition(table, layer="sensors", kind="observed", zoom=13, day=day)
        derive_and_write_day_tiers(
            store, layer="sensors", kind="observed", day=day, run_id=run_id, now=lambda: published_at
        )
        store.write_completion_marker(
            PartitionCompletion(part_count=1, row_count=table.num_rows, completed_at=published_at, run_id=run_id),
            layer="sensors",
            kind="observed",
            zoom=13,
            day=day,
        )
    require(len(ledger.completions) == len(RUNGS), "candidate lacks four completion receipts")
    require(all(not item.derived_empty for item in ledger.completions.values()), "candidate has an empty rung")
    return memory, ledger


def ledger_wire(ledger: WrittenObjectLedger) -> dict[str, object]:
    """Persist original ordinary-writer receipts for exact-day replay."""
    return cast(
        "dict[str, object]",
        json.loads(
            encode(
                {
                    "partitions": [asdict(r) for r in ledger.partitions.values()],
                    "completions": [asdict(r) for r in ledger.completions.values()],
                }
            )
        ),
    )


def finalized(plan: Mapping[str, Any], day: date, request_sha: str) -> FinalizedLaneDay:
    """Restore the immutable prepared finalization without regenerating timestamps."""
    wire = plan["days"][day.isoformat()]["ledger"]
    ledger = WrittenObjectLedger()
    for values in wire["partitions"]:
        receipt = ParquetWriteReceipt(**{**values, "day": date.fromisoformat(values["day"])})
        ledger.partitions[receipt.relative_path] = receipt
    for values in wire["completions"]:
        marker = CompletionWriteReceipt(**{**values, "day": date.fromisoformat(values["day"])})
        ledger.completions[marker.relative_path] = marker
    published = datetime.fromisoformat(plan["published_at"])
    base = ledger.parts_for(kind="observed", zoom=13, day=day)
    return FinalizedLaneDay(
        terminal_state="published",
        day=day,
        written=ledger,
        source=LaneDaySource(
            origin="captured-nws-positive-absence-correction",
            run_id=plan["run_id"],
            row_count=sum(r.row_count for r in base),
            part_count=len(base),
            exported_at=published,
            detail=f"repair={LANE_ROOT}/availability/repairs/{request_sha}/request.json; source_complete=false; "
            "original capture timestamps and partial-source audits archived by request",
        ),
        published_at=published,
        source_ceiling=date.fromisoformat(plan["source_ceiling"]),
    )


def verify_quiescence(proof: Mapping[str, Any], *, request_sha: str, now: datetime) -> None:
    """Validate a separately pinned operator attestation, not an inferred process state."""
    require(proof.get("schema_version") == "sensors-correction-quiescence/v1", "unknown quiescence proof")
    require(proof.get("request_sha256") == request_sha and proof.get("lane_root") == LANE_ROOT, "proof scope mismatch")
    require(
        proof.get("no_inflight_retry_workers") is True and proof.get("writers_stopped") is True,
        "quiescence must explicitly cover retry workers and ordinary writers",
    )
    require(bool(proof.get("operator")) and bool(proof.get("evidence")), "quiescence proof needs operator and evidence")
    start = datetime.fromisoformat(proof["observed_at"])
    end = datetime.fromisoformat(proof["valid_until"])
    require(
        start.tzinfo is not None and end.tzinfo is not None and start <= now <= end,
        "quiescence proof is expired or future-dated",
    )
    require((end - start).total_seconds() <= MAX_QUIESCENCE_SECONDS, "quiescence proof window exceeds one hour")


def check_known_objects(
    current: Mapping[str, bytes],
    originals: Mapping[str, bytes],
    candidates: Mapping[str, bytes],
    *,
    mutation_started: bool,
) -> None:
    """Allow only exact original objects or journaled exact candidate transition bytes."""
    if not mutation_started:
        require(dict(current) == dict(originals), "original physical inventory changed")
        return
    for key, payload in current.items():
        require(payload == originals.get(key) or payload == candidates.get(key), f"unknown transition object: {key}")


def read_scope(backend: Any, storage: AvailabilityStorage, day: date, prefix: str) -> dict[str, bytes]:
    """Read every object in the four exact day prefixes with explicit population bounds."""
    result: dict[str, bytes] = {}
    for zoom in RUNGS:
        scope = day_prefix("sensors", "observed", zoom, day)
        for listed in backend.list_objects(prefix + scope):
            key = listed.key.removeprefix(prefix)
            require(key.startswith(scope) and len(result) < MAX_OBJECTS, "physical inventory exceeds exact scope")
            held = storage.read(key, max_bytes=MAX_BYTES)
            require(held is not None, "listed object disappeared")
            if held is not None:
                result[key] = held.payload
    return result


def verify_physical(store: ObjectStore, expected: Mapping[str, bytes], actual: Mapping[str, bytes], day: date) -> None:
    """Check every physical byte and independently read all four decoded rungs."""
    require(dict(actual) == dict(expected), "physical ladder differs from prepared exact bytes")
    for zoom in RUNGS:
        marker = store.read_completion_receipt("sensors", "observed", zoom, day)
        part = store.read_partition_with_receipts("sensors", "observed", zoom, day)
        require(marker is not None and part.table.num_rows > 0, "published rung is incomplete")
        if marker is not None:
            require(marker.completion.row_count == part.table.num_rows, "completion row count mismatch")
        for receipt in part.parts:
            require(sha256_digest(expected[receipt.relative_path]) == receipt.sha256, "readback part digest mismatch")


def original_keys(day: date) -> set[str]:
    """Name exactly the four false-absence objects that this correction may remove."""
    return {absence_marker_path("sensors", "observed", zoom, day) for zoom in RUNGS}


def verify_retry(payload: bytes, outcome: FinalizedLaneDay) -> None:
    """Require a surviving retry claim to equal the exact durable finalization."""
    expected = _claim_from_finalized(outcome, lane="sensors", kind="observed", lane_root=LANE_ROOT, day=outcome.day)
    observed = _claim_from_marker(payload, lane_root=LANE_ROOT, day=outcome.day)
    require(observed == expected, "retry claim differs from durable correction finalization")


def verify_index_row(row: AvailabilityRow, outcome: FinalizedLaneDay, identity: AvailabilityIdentity) -> None:
    """Bind a replacement row to the exact prepared source and physical receipts."""
    source_key, source_payload = _source_object(LANE_ROOT, day=outcome.day, source=outcome.source)
    source_artifact = build_source_evidence(
        SourceEvidence(
            identity=identity,
            day=outcome.day,
            source_ceiling=outcome.source_ceiling,
            object_receipts=(EvidenceReceipt(key=source_key, sha256=sha256_digest(source_payload)),),
        )
    )
    require(
        row.day == outcome.day and row.terminal_state == "published" and row.published_at == outcome.published_at,
        "indexed day/state/time differs from correction",
    )
    require(
        row.source_receipt == source_artifact.receipt,
        "indexed source differs from correction",
    )
    require(
        row.source_ceiling == outcome.source_ceiling and row.provenance == "digested",
        "indexed coverage/provenance differs",
    )
    parts = tuple(r for r in outcome.written.partitions.values() if r.zoom == row.rung)
    marker = next((r for r in outcome.written.completions.values() if r.zoom == row.rung), None)
    require(
        {(r.key, r.sha256) for r in row.data_receipts} == {(r.relative_path, r.sha256) for r in parts},
        "indexed parts differ from prepared parts",
    )
    require(row.row_count == sum(r.row_count for r in parts), "indexed row count differs")
    require(marker is not None and row.completion_receipt is not None, "indexed completion is missing")
    if marker is not None and row.completion_receipt is not None:
        require(
            (row.completion_receipt.key, row.completion_receipt.sha256) == (marker.relative_path, marker.sha256),
            "indexed completion differs",
        )
        terminal = TerminalEvidence(
            identity=identity,
            day=outcome.day,
            rung=row.rung,
            terminal_state="published",
            row_count=sum(r.row_count for r in parts),
            source_ceiling=outcome.source_ceiling,
            published_at=outcome.published_at,
            source_receipt=source_artifact.receipt,
            data_receipts=tuple(
                EvidenceReceipt(key=r.relative_path, sha256=r.sha256)
                for r in sorted(parts, key=lambda r: r.relative_path)
            ),
            completion_receipt=EvidenceReceipt(key=marker.relative_path, sha256=marker.sha256),
            absence_receipt=None,
            absence_reason=None,
        )
        artifact = build_terminal_evidence(terminal)
        require(
            row == availability_row_from_terminal_evidence(terminal, terminal_receipt=artifact.receipt),
            "indexed terminal wrapper differs from correction",
        )


def verify_export_source(storage: AvailabilityStorage, outcome: FinalizedLaneDay) -> None:
    """Read the raw source document behind the typed availability source wrapper."""
    key, payload = _source_object(LANE_ROOT, day=outcome.day, source=outcome.source)
    held = storage.read(key, max_bytes=MAX_BYTES)
    require(held is not None and held.payload == payload, "raw correction source differs")


def verify_generation_binding(current: AvailabilityIndex, original: AvailabilityIndex) -> None:
    """Permit newer generations without changing the captured lane/bootstrap identity."""
    require(current.pointer.identity == original.pointer.identity, "availability lane identity changed")
    require(
        current.pointer.bootstrap_receipt == original.pointer.bootstrap_receipt,
        "availability bootstrap binding changed",
    )
