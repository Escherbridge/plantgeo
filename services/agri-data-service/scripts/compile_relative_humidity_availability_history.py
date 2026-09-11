"""Compile the offline 1981-2017 relative-humidity recovery; see `scripts/AGENTS.md`."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import tracemalloc
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import TYPE_CHECKING, Final, cast

from botocore.exceptions import BotoCoreError  # type: ignore[import-untyped]

SERVICE_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SERVICE_ROOT / "src"))
sys.path.insert(0, str(SCRIPTS_ROOT))

import compile_availability_bootstrap as bootstrap  # noqa: E402

from agri_data_service.foundation.canonical import canonical_json, sha256_digest  # noqa: E402
from agri_data_service.pipeline.parquet.availability_index import (  # noqa: E402
    BOOTSTRAP_RECEIPT_MAX_BYTES,
    DIGESTED_PROVENANCE,
    MAX_INPUT_BYTES,
    POINTER_MAX_BYTES,
    PUBLICATION_INPUT_SCHEMA_VERSION,
    SYSTEM_BOOTSTRAP_SCHEMA_VERSION,
    AvailabilityIndex,
    AvailabilityRow,
    BotoAvailabilityStorage,
    SourceEvidence,
    StoredAvailabilityObject,
    TerminalEvidence,
    availability_lane_identity,
    availability_pointer_key,
    availability_row_from_terminal_evidence,
    build_source_evidence,
    build_terminal_evidence,
    load_publication_request,
    read_latest_availability,
)
from agri_data_service.pipeline.parquet.objectstore import availability_lane_root  # noqa: E402
from agri_data_service.warehouse.schemas.availability_index import AVAILABILITY_REQUIRED_RUNGS  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.parquet_ops.coverage import CensusLane

LANE: Final = "climate-field-relative-humidity"
KIND: Final = "observed"
FIRST_DAY: Final = date(1981, 1, 1)
LAST_DAY: Final = date(2017, 12, 31)
EXPECTED_TARGET_DAYS: Final = 13_514
DEFAULT_WORKERS: Final = 8
MAX_WORKERS: Final = 24
MAX_DAYS_PER_INPUT: Final = 6_000
DEFAULT_OUT: Final = SERVICE_ROOT / ".agri-local-runs" / "relative-humidity-availability-history"
INPUT_FILE_NAME: Final = "publication-input-001.json"
RECEIPT_FILE_NAME: Final = "receipt.json"
EVIDENCE_DIRECTORY_NAME: Final = "evidence"
PRIOR_DIRECTORY_NAME: Final = "prior"
OVERSIZED_INPUT_FILE_NAME: Final = "publication-input.json"


class HistoryCompilationError(RuntimeError):
    """The pinned recovery cannot produce an exact offline publication candidate."""


@dataclass(frozen=True, slots=True)
class CurrentAvailability:
    """Stable, fully verified current index plus exact remote identities."""

    index: AvailabilityIndex
    pointer: StoredAvailabilityObject
    pointer_sha256: str
    bootstrap: StoredAvailabilityObject


@dataclass(frozen=True, slots=True)
class Candidate:
    """Publication rows and local evidence produced from the missing physical ladder."""

    rows: tuple[AvailabilityRow, ...]
    artifacts: tuple[bootstrap.EvidenceArtifactFile, ...]
    compilation: bootstrap.LaneCompilation
    target_days: int
    already_indexed_days: int


@dataclass(frozen=True, slots=True)
class PublicationChunk:
    """One bounded, complete-day publication request in the ordered recovery sequence."""

    index: int
    rows: tuple[AvailabilityRow, ...]
    payload: bytes

    @property
    def file_name(self) -> str:
        return f"publication-input-{self.index:03d}.json"

    @property
    def sha256(self) -> str:
        return sha256_digest(self.payload)


@dataclass(slots=True)
class RetryingBucketReader:
    """Add bounded transport retries without changing the shared object-store core."""

    delegate: bootstrap.BucketReader
    attempts: int = 6
    retry_count: int = 0
    _retry_lock: Lock = field(default_factory=Lock)

    @property
    def prefix(self) -> str:
        return self.delegate.prefix

    def list_rung(
        self,
        layer: str,
        kind: PartitionKind,
        rung: ZoomTier,
    ) -> tuple[str, ...]:
        for attempt in range(1, self.attempts + 1):
            try:
                return self.delegate.list_rung(layer, kind, rung)
            except (BotoCoreError, OSError):
                if attempt == self.attempts:
                    raise
                self._backoff(attempt)
        raise AssertionError("bounded list retry loop did not return or raise")

    def read(self, relative_path: str) -> bytes | None:
        for attempt in range(1, self.attempts + 1):
            try:
                return self.delegate.read(relative_path)
            except (BotoCoreError, OSError):
                if attempt == self.attempts:
                    raise
                self._backoff(attempt)
        raise AssertionError("bounded read retry loop did not return or raise")

    def _backoff(self, attempt: int) -> None:
        with self._retry_lock:
            self.retry_count += 1
        time.sleep(min(0.25 * (2 ** (attempt - 1)), 4.0))


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _calendar_days(first: date, last: date) -> tuple[date, ...]:
    if first > last:
        raise ValueError("historical window is inverted")
    return tuple(date.fromordinal(ordinal) for ordinal in range(first.toordinal(), last.toordinal() + 1))


def _read_required(store: BotoAvailabilityStorage, key: str, *, max_bytes: int) -> StoredAvailabilityObject:
    stored = store.read(key, max_bytes=max_bytes)
    if stored is None:
        raise HistoryCompilationError(f"required current availability object {key!r} is missing")
    return stored


def _read_current(store: BotoAvailabilityStorage, *, lane_root: str) -> CurrentAvailability:
    """Read and checksum the current pointer/generation/bootstrap identity without mutating it."""
    pointer_key = availability_pointer_key(lane_root)
    before = _read_required(store, pointer_key, max_bytes=POINTER_MAX_BYTES)
    index = read_latest_availability(
        store,
        lane_root=lane_root,
        expected_lane=LANE,
        expected_product=LANE,
        expected_nature="daily_series",
        expected_required_rungs=AVAILABILITY_REQUIRED_RUNGS,
    )
    expected_pointer = canonical_json(index.pointer.to_wire()).encode("utf-8")
    if before.payload != expected_pointer:
        raise HistoryCompilationError("current pointer changed between its raw read and generation verification")
    bootstrap_receipt = index.pointer.bootstrap_receipt
    expected_bootstrap_key = f"{lane_root}/availability/bootstrap/receipt={bootstrap_receipt.sha256}.json"
    if bootstrap_receipt.key != expected_bootstrap_key:
        raise HistoryCompilationError("current bootstrap receipt key is not content-addressed under its lane")
    bootstrap_stored = _read_required(store, bootstrap_receipt.key, max_bytes=BOOTSTRAP_RECEIPT_MAX_BYTES)
    if sha256_digest(bootstrap_stored.payload) != bootstrap_receipt.sha256:
        raise HistoryCompilationError("current immutable bootstrap receipt checksum disagrees with the pointer")
    _require_bootstrap_identity(bootstrap_stored.payload, index=index)
    return CurrentAvailability(
        index=index,
        pointer=before,
        pointer_sha256=sha256_digest(before.payload),
        bootstrap=bootstrap_stored,
    )


def _require_bootstrap_identity(payload: bytes, *, index: AvailabilityIndex) -> None:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HistoryCompilationError(f"current bootstrap receipt is not JSON: {error}") from error
    if not isinstance(value, dict):
        raise HistoryCompilationError("current bootstrap receipt is not a JSON object")
    if canonical_json(value).encode("utf-8") != payload:
        raise HistoryCompilationError("current bootstrap receipt is not canonical JSON")
    identity = index.pointer.identity
    expected: dict[str, object] = {
        "lane": identity.lane,
        "lane_root": identity.lane_root,
        "nature": identity.nature,
        "product": identity.product,
        "required_rungs": list(identity.required_rungs),
        "schema_version": SYSTEM_BOOTSTRAP_SCHEMA_VERSION,
        "verified_source_inventory_root": identity.verified_source_inventory_root,
    }
    drift = {
        key: {"actual": value.get(key), "expected": expected_value}
        for key, expected_value in expected.items()
        if value.get(key) != expected_value
    }
    if drift:
        raise HistoryCompilationError(f"current bootstrap receipt identity drifted: {drift}")
    pointer = index.pointer
    try:
        bootstrap_ceiling = date.fromisoformat(str(value["source_ceiling"]))
    except (KeyError, ValueError) as error:
        raise HistoryCompilationError("current bootstrap receipt has an invalid source ceiling") from error
    row_count = value.get("row_count")
    if (
        bootstrap_ceiling > pointer.source_ceiling
        or not isinstance(row_count, int)
        or not 0 < row_count <= pointer.rows
    ):
        raise HistoryCompilationError("current bootstrap receipt exceeds the verified generation bounds")


def _missing_days(index: AvailabilityIndex) -> tuple[tuple[date, ...], int]:
    target = _calendar_days(FIRST_DAY, LAST_DAY)
    if len(target) != EXPECTED_TARGET_DAYS:
        raise AssertionError("pinned relative-humidity target day count drifted")
    by_day: dict[date, set[int]] = {}
    for row in index.rows:
        if FIRST_DAY <= row.day <= LAST_DAY:
            by_day.setdefault(row.day, set()).add(row.rung)
    required = set(AVAILABILITY_REQUIRED_RUNGS)
    partial = {day: sorted(rungs) for day, rungs in by_day.items() if rungs != required}
    if partial:
        sample = sorted(partial.items())[:5]
        raise HistoryCompilationError(f"current index partially covers the recovery window: {sample}")
    missing = tuple(day for day in target if day not in by_day)
    if not missing:
        raise HistoryCompilationError("relative-humidity 1981-2017 availability history is already complete")
    return missing, len(by_day)


def _compile_missing(
    reader: bootstrap.BucketReader | RetryingBucketReader,
    lane: CensusLane,
    *,
    current: CurrentAvailability,
    workers: int,
) -> Candidate:
    missing, already_indexed = _missing_days(current.index)
    identity = current.index.pointer.identity
    compilation = bootstrap.LaneCompilation(
        lane=lane,
        lane_root=identity.lane_root,
        source_ceiling=current.index.pointer.source_ceiling,
        created_at=_now(),
        digest_window_days_requested=0,
        digest_window_days_applied=0,
        identity=identity,
    )
    typed_reader = cast("bootstrap.BucketReader", reader)
    ladder = bootstrap._walk_ladder(typed_reader, layer=LANE, kind=KIND)
    absent = tuple(day for day in missing if day not in ladder)
    if absent:
        raise HistoryCompilationError(f"physical ladder is missing {len(absent)} required day(s), first={absent[0]}")
    markers = bootstrap._read_markers(typed_reader, ladder, days=missing, workers=workers)
    compilation.marker_read_count = len(markers)
    terminal_days = bootstrap._terminal_days(ladder, markers, compilation=compilation, days=missing)
    if terminal_days != missing or compilation.excluded_days:
        raise HistoryCompilationError(
            f"historical ladder did not bind exactly: terminal={len(terminal_days)}, "
            f"missing={len(missing)}, exclusions={compilation.excluded_days[:5]}"
        )
    bound_days = bootstrap._bind_days(
        typed_reader,
        ladder,
        markers,
        days=terminal_days,
        digest_floor=date.min,
        compilation=compilation,
        workers=workers,
    )
    if tuple(bound.day for bound in bound_days) != missing or compilation.excluded_days:
        raise HistoryCompilationError(
            f"historical parts did not digest exactly: bound={len(bound_days)}, "
            f"exclusions={compilation.excluded_days[:5]}"
        )

    artifacts: list[bootstrap.EvidenceArtifactFile] = []
    rows: list[AvailabilityRow] = []
    for bound_day in bound_days:
        source = build_source_evidence(
            SourceEvidence(
                identity=identity,
                day=bound_day.day,
                source_ceiling=current.index.pointer.source_ceiling,
                object_receipts=bound_day.source_receipts,
            )
        )
        artifacts.append(bootstrap.EvidenceArtifactFile(key=source.receipt.key, payload=source.payload))
        for bound_rung in bound_day.rungs:
            terminal = TerminalEvidence(
                identity=identity,
                day=bound_day.day,
                rung=bound_rung.rung,
                terminal_state=bound_rung.terminal_state,
                row_count=bound_rung.row_count,
                source_ceiling=current.index.pointer.source_ceiling,
                published_at=bound_rung.published_at,
                source_receipt=source.receipt,
                data_receipts=bound_rung.data_receipts,
                completion_receipt=bound_rung.completion_receipt,
                absence_receipt=bound_rung.absence_receipt,
                absence_reason=bound_rung.absence_reason,
                provenance=bound_rung.provenance,
            )
            terminal_artifact = build_terminal_evidence(terminal)
            artifacts.append(
                bootstrap.EvidenceArtifactFile(key=terminal_artifact.receipt.key, payload=terminal_artifact.payload)
            )
            rows.append(availability_row_from_terminal_evidence(terminal, terminal_receipt=terminal_artifact.receipt))
    ordered_rows = tuple(sorted(rows, key=lambda row: (row.day, row.rung)))
    if any(row.provenance != DIGESTED_PROVENANCE for row in ordered_rows):
        raise HistoryCompilationError("publication candidate contains a manifest-trusted row")
    if len(ordered_rows) != len(missing) * len(AVAILABILITY_REQUIRED_RUNGS):
        raise HistoryCompilationError("publication candidate does not contain one complete rung ladder per missing day")
    compilation.rows = ordered_rows
    compilation.artifacts = tuple(artifacts)
    return Candidate(
        rows=ordered_rows,
        artifacts=tuple(artifacts),
        compilation=compilation,
        target_days=len(missing),
        already_indexed_days=already_indexed,
    )


def _publication_document(
    candidate: Candidate,
    *,
    current: CurrentAvailability,
    rows: Sequence[AvailabilityRow] | None = None,
) -> dict[str, object]:
    pointer = current.index.pointer
    identity = pointer.identity
    selected_rows = candidate.rows if rows is None else rows
    return {
        "bootstrap_receipt_key": pointer.bootstrap_receipt.key,
        "bootstrap_receipt_sha256": pointer.bootstrap_receipt.sha256,
        "created_at": bootstrap._format_instant(candidate.compilation.created_at),
        "lane": identity.lane,
        "lane_root": identity.lane_root,
        "nature": identity.nature,
        "product": identity.product,
        "required_rungs": list(identity.required_rungs),
        "rows": [{**row.to_wire(), "provenance": row.provenance} for row in selected_rows],
        "schema_version": PUBLICATION_INPUT_SCHEMA_VERSION,
        "source_ceiling": pointer.source_ceiling.isoformat(),
        "verified_source_inventory_root": identity.verified_source_inventory_root,
    }


def _publication_chunks(
    candidate: Candidate,
    *,
    current: CurrentAvailability,
) -> tuple[PublicationChunk, ...]:
    rung_count = len(AVAILABILITY_REQUIRED_RUNGS)
    if len(candidate.rows) % rung_count:
        raise HistoryCompilationError("publication rows cannot be split into complete day ladders")
    chunks: list[PublicationChunk] = []
    rows_per_chunk = MAX_DAYS_PER_INPUT * rung_count
    for index, start in enumerate(range(0, len(candidate.rows), rows_per_chunk), start=1):
        rows = candidate.rows[start : start + rows_per_chunk]
        if len({row.day for row in rows}) * rung_count != len(rows):
            raise HistoryCompilationError("publication chunk does not contain complete day ladders")
        payload = canonical_json(_publication_document(candidate, current=current, rows=rows)).encode("utf-8")
        if len(payload) > MAX_INPUT_BYTES:
            raise HistoryCompilationError(
                f"publication chunk {index} exceeds {MAX_INPUT_BYTES} bytes; lower MAX_DAYS_PER_INPUT"
            )
        chunks.append(PublicationChunk(index=index, rows=rows, payload=payload))
    if not chunks:
        raise HistoryCompilationError("publication candidate produced no bounded inputs")
    return tuple(chunks)


def _raw_publication_payloads(document: Mapping[str, object]) -> tuple[bytes, ...]:
    rows = document.get("rows")
    if not isinstance(rows, list) or len(rows) != EXPECTED_TARGET_DAYS * len(AVAILABILITY_REQUIRED_RUNGS):
        raise HistoryCompilationError("oversized publication input does not contain the exact recovery row count")
    rows_per_chunk = MAX_DAYS_PER_INPUT * len(AVAILABILITY_REQUIRED_RUNGS)
    payloads: list[bytes] = []
    for start in range(0, len(rows), rows_per_chunk):
        payload = canonical_json({**document, "rows": rows[start : start + rows_per_chunk]}).encode("utf-8")
        if len(payload) > MAX_INPUT_BYTES:
            raise HistoryCompilationError("resumed publication chunk still exceeds the hard input cap")
        payloads.append(payload)
    return tuple(payloads)


def _require_recovery_rows_current(
    document: Mapping[str, object],
    rows: Sequence[AvailabilityRow],
    *,
    current: CurrentAvailability,
) -> None:
    pointer = current.index.pointer
    identity = pointer.identity
    expected = {
        "bootstrap_receipt_key": pointer.bootstrap_receipt.key,
        "bootstrap_receipt_sha256": pointer.bootstrap_receipt.sha256,
        "lane": identity.lane,
        "lane_root": identity.lane_root,
        "nature": identity.nature,
        "product": identity.product,
        "required_rungs": list(identity.required_rungs),
        "source_ceiling": pointer.source_ceiling.isoformat(),
        "verified_source_inventory_root": identity.verified_source_inventory_root,
    }
    drift = {
        key: {"actual": document.get(key), "expected": value}
        for key, value in expected.items()
        if document.get(key) != value
    }
    if drift:
        raise HistoryCompilationError(f"resumed publication identity is incompatible with current: {drift}")
    missing, already_indexed = _missing_days(current.index)
    by_day: dict[date, set[int]] = {}
    for row in rows:
        by_day.setdefault(row.day, set()).add(row.rung)
    if tuple(sorted(by_day)) != missing or already_indexed:
        raise HistoryCompilationError("current pointer changed the resumed recovery-window coverage")
    required = set(AVAILABILITY_REQUIRED_RUNGS)
    if any(rungs != required for rungs in by_day.values()):
        raise HistoryCompilationError("resumed publication rows do not hold complete rung ladders")


def _require_local_evidence(
    source: Path,
    rows: Sequence[AvailabilityRow],
) -> tuple[int, int]:
    evidence_root = source / EVIDENCE_DIRECTORY_NAME
    receipts = {receipt.key: receipt.sha256 for row in rows for receipt in (row.source_receipt, row.terminal_receipt)}
    expected_paths: set[str] = set()
    byte_count = 0
    for key, expected_sha256 in receipts.items():
        relative = PurePosixPath(key)
        if relative.is_absolute() or ".." in relative.parts:
            raise HistoryCompilationError(f"unsafe local evidence key: {key}")
        path = evidence_root.joinpath(*relative.parts)
        payload = path.read_bytes()
        if sha256_digest(payload) != expected_sha256:
            raise HistoryCompilationError(f"local evidence checksum mismatch: {key}")
        expected_paths.add(relative.as_posix())
        byte_count += len(payload)
    actual_paths = {
        path.relative_to(evidence_root).as_posix() for path in evidence_root.rglob("*.json") if path.is_file()
    }
    if actual_paths != expected_paths:
        raise HistoryCompilationError(
            f"local evidence inventory mismatch: actual={len(actual_paths)}, expected={len(expected_paths)}"
        )
    return len(receipts), byte_count


def _require_pointer_unchanged(
    store: BotoAvailabilityStorage,
    *,
    lane_root: str,
    current: CurrentAvailability,
) -> None:
    after = _read_required(store, availability_pointer_key(lane_root), max_bytes=POINTER_MAX_BYTES)
    if (
        after.payload != current.pointer.payload
        or after.etag != current.pointer.etag
        or after.version_id != current.pointer.version_id
    ):
        raise HistoryCompilationError("availability pointer changed while the candidate was being compiled")


def _refresh_compatible_current(
    store: BotoAvailabilityStorage,
    *,
    lane_root: str,
    compiled_against: CurrentAvailability,
    candidate: Candidate,
) -> tuple[CurrentAvailability, bool]:
    """Rebase across forward-only pointer movement without repeating physical digests."""
    latest = _read_current(store, lane_root=lane_root)
    changed = (
        latest.pointer.payload != compiled_against.pointer.payload
        or latest.pointer.etag != compiled_against.pointer.etag
        or latest.pointer.version_id != compiled_against.pointer.version_id
    )
    if not changed:
        return latest, False
    before = compiled_against.index.pointer
    after = latest.index.pointer
    if (
        after.identity != before.identity
        or after.bootstrap_receipt != before.bootstrap_receipt
        or after.source_ceiling != before.source_ceiling
    ):
        raise HistoryCompilationError(
            "availability pointer changed incompatibly while the candidate was being compiled"
        )
    missing, already_indexed = _missing_days(latest.index)
    candidate_days = tuple(sorted({row.day for row in candidate.rows}))
    if missing != candidate_days or already_indexed != candidate.already_indexed_days:
        raise HistoryCompilationError(
            "availability pointer changed the recovery-window coverage while the candidate was being compiled"
        )
    return latest, True


def _content_addressed_path(root: Path, *, label: str, payload: bytes, suffix: str) -> Path:
    return root / f"{label}={sha256_digest(payload)}.{suffix}"


def _write_receipt(out: Path, receipt: Mapping[str, object]) -> None:
    payload = canonical_json(receipt).encode("utf-8")
    _content_addressed_path(out, label="receipt", payload=payload, suffix="json").write_bytes(payload)
    (out / RECEIPT_FILE_NAME).write_bytes(payload)


def _write_candidate(  # noqa: PLR0913 - receipt assembly keeps measured inputs explicit
    out: Path,
    *,
    current: CurrentAvailability,
    candidate: Candidate,
    publication_chunks: Sequence[PublicationChunk],
    timings: Mapping[str, float],
    peak_memory_bytes: int,
    workers: int,
) -> dict[str, object]:
    out.mkdir(parents=True, exist_ok=False)
    inputs: list[dict[str, object]] = []
    for chunk in publication_chunks:
        input_path = out / chunk.file_name
        input_path.write_bytes(chunk.payload)
        inputs.append(
            {
                "byte_count": len(chunk.payload),
                "first_day": chunk.rows[0].day.isoformat(),
                "index": chunk.index,
                "last_day": chunk.rows[-1].day.isoformat(),
                "path": str(input_path),
                "row_count": len(chunk.rows),
                "sha256": chunk.sha256,
            }
        )
    evidence_root = out / EVIDENCE_DIRECTORY_NAME
    for artifact in candidate.artifacts:
        destination = evidence_root / artifact.key
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(artifact.payload)
    prior_root = out / PRIOR_DIRECTORY_NAME
    prior_root.mkdir()
    pointer_path = _content_addressed_path(prior_root, label="pointer", payload=current.pointer.payload, suffix="json")
    pointer_path.write_bytes(current.pointer.payload)
    bootstrap_path = _content_addressed_path(
        prior_root,
        label="bootstrap-receipt",
        payload=current.bootstrap.payload,
        suffix="json",
    )
    bootstrap_path.write_bytes(current.bootstrap.payload)
    pointer = current.index.pointer
    evidence_bytes = sum(len(artifact.payload) for artifact in candidate.artifacts)
    input_bytes = sum(len(chunk.payload) for chunk in publication_chunks)
    receipt: dict[str, object] = {
        "apply_authorized": False,
        "bootstrap_receipt": {
            "byte_count": len(current.bootstrap.payload),
            "etag": current.bootstrap.etag,
            "key": pointer.bootstrap_receipt.key,
            "local_path": str(bootstrap_path),
            "sha256": pointer.bootstrap_receipt.sha256,
            "version_id": current.bootstrap.version_id,
        },
        "candidate": {
            "evidence_byte_count": evidence_bytes,
            "evidence_object_count": len(candidate.artifacts),
            "first_day": candidate.rows[0].day.isoformat(),
            "fully_digested_rows": len(candidate.rows),
            "hashed_part_byte_count": candidate.compilation.hashed_part_bytes,
            "hashed_part_count": candidate.compilation.hashed_part_count,
            "input_byte_count": input_bytes,
            "input_count": len(publication_chunks),
            "inputs": inputs,
            "last_day": candidate.rows[-1].day.isoformat(),
            "local_payload_byte_count_before_receipt": (
                evidence_bytes + input_bytes + len(current.pointer.payload) + len(current.bootstrap.payload)
            ),
            "local_object_count_before_receipt": len(candidate.artifacts) + len(publication_chunks) + 2,
            "marker_object_count": candidate.compilation.marker_read_count,
            "publication_row_count": len(candidate.rows),
            "target_day_count": candidate.target_days,
        },
        "history_window": {
            "already_indexed_day_count": candidate.already_indexed_days,
            "expected_day_count": EXPECTED_TARGET_DAYS,
            "first_day": FIRST_DAY.isoformat(),
            "last_day": LAST_DAY.isoformat(),
            "missing_day_count": candidate.target_days,
        },
        "lane": LANE,
        "offline_validation": "load_publication_request",
        "peak_traced_memory_bytes": peak_memory_bytes,
        "prior_pointer": {
            "byte_count": len(current.pointer.payload),
            "etag": current.pointer.etag,
            "generation_bytes": pointer.generation_bytes,
            "generation_key": pointer.generation_key,
            "generation_receipt_sha256": pointer.generation_receipt_sha256,
            "generation_sha256": pointer.generation_sha256,
            "key": availability_pointer_key(pointer.identity.lane_root),
            "local_path": str(pointer_path),
            "pointer_sha256": current.pointer_sha256,
            "prior_generation_key": pointer.prior_generation_key,
            "prior_generation_sha256": pointer.prior_generation_sha256,
            "rows": pointer.rows,
            "version_id": current.pointer.version_id,
        },
        "schema_version": "relative-humidity-availability-history-candidate-v1",
        "status": "candidate_complete",
        "timings_seconds": dict(sorted(timings.items())),
        "workers": workers,
    }
    return receipt


def _pointer_record(current: CurrentAvailability, *, local_path: Path) -> dict[str, object]:
    pointer = current.index.pointer
    return {
        "byte_count": len(current.pointer.payload),
        "etag": current.pointer.etag,
        "generation_bytes": pointer.generation_bytes,
        "generation_key": pointer.generation_key,
        "generation_receipt_sha256": pointer.generation_receipt_sha256,
        "generation_sha256": pointer.generation_sha256,
        "key": availability_pointer_key(pointer.identity.lane_root),
        "local_path": str(local_path),
        "pointer_sha256": current.pointer_sha256,
        "prior_generation_key": pointer.prior_generation_key,
        "prior_generation_sha256": pointer.prior_generation_sha256,
        "rows": pointer.rows,
        "version_id": current.pointer.version_id,
    }


def _bootstrap_record(current: CurrentAvailability, *, local_path: Path) -> dict[str, object]:
    receipt = current.index.pointer.bootstrap_receipt
    return {
        "byte_count": len(current.bootstrap.payload),
        "etag": current.bootstrap.etag,
        "key": receipt.key,
        "local_path": str(local_path),
        "sha256": receipt.sha256,
        "version_id": current.bootstrap.version_id,
    }


def _publication_identity_record(current: CurrentAvailability) -> dict[str, object]:
    pointer = current.index.pointer
    identity = pointer.identity
    return {
        "lane": identity.lane,
        "lane_root": identity.lane_root,
        "nature": identity.nature,
        "product": identity.product,
        "required_rungs": list(identity.required_rungs),
        "source_ceiling": pointer.source_ceiling.isoformat(),
        "verified_source_inventory_root": identity.verified_source_inventory_root,
    }


def _resume_oversized(
    source: Path,
    out: Path,
    *,
    store: BotoAvailabilityStorage,
    workers: int,
) -> dict[str, object]:
    started = time.perf_counter()
    oversized_payload = (source / OVERSIZED_INPUT_FILE_NAME).read_bytes()
    try:
        document = json.loads(oversized_payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HistoryCompilationError(f"oversized publication input is not JSON: {error}") from error
    if not isinstance(document, dict) or canonical_json(document).encode("utf-8") != oversized_payload:
        raise HistoryCompilationError("oversized publication input is not canonical JSON")
    raw_rows = document.get("rows")
    if not isinstance(raw_rows, list):
        raise HistoryCompilationError("oversized publication input rows are not a list")
    raw_payloads = _raw_publication_payloads(document)
    out.mkdir(parents=True, exist_ok=False)
    chunks: list[PublicationChunk] = []
    rows: list[AvailabilityRow] = []
    inputs: list[dict[str, object]] = []
    for index, payload in enumerate(raw_payloads, start=1):
        path = out / f"publication-input-{index:03d}.json"
        path.write_bytes(payload)
        row_count = len(
            raw_rows[
                (index - 1) * MAX_DAYS_PER_INPUT * len(AVAILABILITY_REQUIRED_RUNGS) : index
                * MAX_DAYS_PER_INPUT
                * len(AVAILABILITY_REQUIRED_RUNGS)
            ]
        )
        request = load_publication_request(
            path,
            expected_sha256=sha256_digest(payload),
            expected_row_count=row_count,
        )
        chunk = PublicationChunk(index=index, rows=request.rows, payload=payload)
        chunks.append(chunk)
        rows.extend(request.rows)
        inputs.append(
            {
                "byte_count": len(payload),
                "first_day": request.rows[0].day.isoformat(),
                "index": index,
                "last_day": request.rows[-1].day.isoformat(),
                "path": str(path),
                "row_count": len(request.rows),
                "sha256": sha256_digest(payload),
            }
        )
    if len(rows) != EXPECTED_TARGET_DAYS * len(AVAILABILITY_REQUIRED_RUNGS):
        raise HistoryCompilationError("bounded inputs did not reconstruct the exact recovery row count")
    if any(row.provenance != DIGESTED_PROVENANCE for row in rows):
        raise HistoryCompilationError("resumed publication contains a manifest-trusted row")
    lane_root = availability_lane_root(LANE, KIND)
    current = _read_current(store, lane_root=lane_root)
    _require_recovery_rows_current(document, rows, current=current)
    evidence_count, evidence_bytes = _require_local_evidence(source, rows)
    shutil.copytree(source / EVIDENCE_DIRECTORY_NAME, out / EVIDENCE_DIRECTORY_NAME)
    shutil.copytree(source / PRIOR_DIRECTORY_NAME, out / PRIOR_DIRECTORY_NAME)
    final_current = _read_current(store, lane_root=lane_root)
    _require_recovery_rows_current(document, rows, current=final_current)
    prior_root = out / PRIOR_DIRECTORY_NAME
    pointer_path = _content_addressed_path(
        prior_root,
        label="pointer",
        payload=final_current.pointer.payload,
        suffix="json",
    )
    if not pointer_path.exists():
        pointer_path.write_bytes(final_current.pointer.payload)
    bootstrap_path = _content_addressed_path(
        prior_root,
        label="bootstrap-receipt",
        payload=final_current.bootstrap.payload,
        suffix="json",
    )
    if not bootstrap_path.exists():
        bootstrap_path.write_bytes(final_current.bootstrap.payload)
    input_bytes = sum(len(chunk.payload) for chunk in chunks)
    data_receipts = sum(len(row.data_receipts) for row in rows)
    receipt: dict[str, object] = {
        "apply_authorized": False,
        "bootstrap_receipt": _bootstrap_record(final_current, local_path=bootstrap_path),
        "candidate": {
            "evidence_byte_count": evidence_bytes,
            "evidence_object_count": evidence_count,
            "first_day": rows[0].day.isoformat(),
            "fully_digested_rows": len(rows),
            "hashed_part_byte_count": None,
            "hashed_part_count": data_receipts,
            "input_byte_count": input_bytes,
            "input_count": len(chunks),
            "inputs": inputs,
            "last_day": rows[-1].day.isoformat(),
            "marker_object_count": len(rows),
            "publication_row_count": len(rows),
            "target_day_count": len({row.day for row in rows}),
        },
        "history_window": {
            "already_indexed_day_count": 0,
            "expected_day_count": EXPECTED_TARGET_DAYS,
            "first_day": FIRST_DAY.isoformat(),
            "last_day": LAST_DAY.isoformat(),
            "missing_day_count": EXPECTED_TARGET_DAYS,
        },
        "lane": LANE,
        "offline_validation": "load_publication_request for every numbered input",
        "peak_traced_memory_bytes": tracemalloc.get_traced_memory()[1],
        "prior_pointer": _pointer_record(final_current, local_path=pointer_path),
        "publication_identity": _publication_identity_record(final_current),
        "resume": {
            "oversized_input_byte_count": len(oversized_payload),
            "oversized_input_sha256": sha256_digest(oversized_payload),
            "source": str(source),
            "source_physical_digest_timing": "unavailable because the rejected attempt ended before its receipt write",
            "validated_local_evidence": True,
        },
        "schema_version": "relative-humidity-availability-history-candidate-v1",
        "status": "candidate_complete",
        "timings_seconds": {"resume_total": time.perf_counter() - started},
        "transport_retry_count": None,
        "workers": workers,
    }
    _write_receipt(out, receipt)
    return receipt


def _lane() -> CensusLane:
    arguments = bootstrap._parse_arguments(["--lane", LANE])
    lanes = bootstrap._resolve_lanes(arguments)
    if len(lanes) != 1 or lanes[0].layer != LANE:
        raise HistoryCompilationError("registered relative-humidity lane did not resolve exactly once")
    return lanes[0]


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument(
        "--resume-from-oversized",
        type=Path,
        help="split and revalidate one preserved oversized local candidate without repeating object reads",
    )
    arguments = parser.parse_args(argv)
    if not 1 <= arguments.workers <= MAX_WORKERS:
        parser.error(f"--workers must be between 1 and {MAX_WORKERS}")
    if arguments.out.exists():
        parser.error("--out must name a path that does not exist; candidates are never overwritten")
    if arguments.resume_from_oversized is not None and not arguments.resume_from_oversized.is_dir():
        parser.error("--resume-from-oversized must name an existing local candidate directory")
    if arguments.resume_from_oversized is not None:
        source = arguments.resume_from_oversized.resolve()
        out = arguments.out.resolve()
        if out == source or source in out.parents:
            parser.error("--out must not be inside --resume-from-oversized")
    return arguments


def main(argv: Sequence[str] | None = None) -> int:  # noqa: PLR0915 - measured offline orchestration
    arguments = _arguments(argv)
    timings: dict[str, float] = {}
    started = time.perf_counter()
    tracemalloc.start()
    try:
        lane = _lane()
        lane_root = availability_lane_root(LANE, KIND)
        if availability_lane_identity(lane_root) != (LANE, KIND):
            raise HistoryCompilationError("pinned lane root identity drifted")
        store = BotoAvailabilityStorage.from_settings()
        if arguments.resume_from_oversized is not None:
            receipt = _resume_oversized(
                arguments.resume_from_oversized,
                arguments.out,
                store=store,
                workers=arguments.workers,
            )
            print(canonical_json(receipt))
            return 0
        reader = RetryingBucketReader(bootstrap.BucketReader.from_settings())
        phase = time.perf_counter()
        current = _read_current(store, lane_root=lane_root)
        timings["read_current_availability"] = time.perf_counter() - phase
        phase = time.perf_counter()
        candidate = _compile_missing(reader, lane, current=current, workers=arguments.workers)
        timings["bind_missing_history"] = time.perf_counter() - phase
        phase = time.perf_counter()
        compiled_pointer_sha256 = current.pointer_sha256
        current, rebased = _refresh_compatible_current(
            store,
            lane_root=lane_root,
            compiled_against=current,
            candidate=candidate,
        )
        timings["refresh_compatible_current"] = time.perf_counter() - phase
        phase = time.perf_counter()
        publication_chunks = _publication_chunks(candidate, current=current)
        timings["render_publication_inputs"] = time.perf_counter() - phase
        timings["total_before_local_write"] = time.perf_counter() - started
        peak_memory = tracemalloc.get_traced_memory()[1]
        phase = time.perf_counter()
        receipt = _write_candidate(
            arguments.out,
            current=current,
            candidate=candidate,
            publication_chunks=publication_chunks,
            timings=timings,
            peak_memory_bytes=peak_memory,
            workers=arguments.workers,
        )
        timings["local_write"] = time.perf_counter() - phase
        validated_rows: list[AvailabilityRow] = []
        for chunk in publication_chunks:
            request = load_publication_request(
                arguments.out / chunk.file_name,
                expected_sha256=chunk.sha256,
                expected_row_count=len(chunk.rows),
            )
            validated_rows.extend(request.rows)
        if tuple(validated_rows) != candidate.rows:
            raise HistoryCompilationError("offline validation changed the ordered publication rows")
        if any(row.provenance != DIGESTED_PROVENANCE for row in validated_rows):
            raise HistoryCompilationError("offline validation admitted a non-digested publication row")
        phase = time.perf_counter()
        _require_pointer_unchanged(store, lane_root=lane_root, current=current)
        timings["post_write_pointer_revalidation"] = time.perf_counter() - phase
        receipt["peak_traced_memory_bytes"] = tracemalloc.get_traced_memory()[1]
        receipt["pointer_rebase"] = {
            "compiled_pointer_sha256": compiled_pointer_sha256,
            "publication_base_pointer_sha256": current.pointer_sha256,
            "rebased": rebased,
            "rule": "identity, bootstrap receipt, source ceiling, and recovery-window coverage unchanged",
        }
        receipt["transport_retry_count"] = reader.retry_count
        receipt["timings_seconds"] = dict(sorted({**timings, "total": time.perf_counter() - started}.items()))
        _write_receipt(arguments.out, receipt)
        print(canonical_json(receipt))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(canonical_json({"error": str(error), "status": "failed", "type": type(error).__name__}))
        return 1
    finally:
        tracemalloc.stop()


if __name__ == "__main__":
    raise SystemExit(main())
