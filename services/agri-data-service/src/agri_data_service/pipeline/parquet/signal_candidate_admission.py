"""Revalidate preserved signal candidates without publication; see AGENTS.md."""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import completion_marker_path, partition_path
from agri_data_service.pipeline.parquet.signal_coordinate_preview import (
    DIMENSION_BYTES,
    DIMENSION_ROWS,
    DIMENSION_SHA256,
    MAX_BASE_ROWS,
    SOURCE_COMPLETE_SHA256,
    SOURCE_MANIFEST_SHA256,
    SOURCE_ROOT,
    build_coordinate_candidate,
    logical_sha256,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    import pyarrow as pa

    from agri_data_service.foundation.parquet.zoom import ZoomTier


@dataclass(frozen=True, slots=True)
class SignalArchivePin:
    """Exact byte and population bounds on one preserved archive."""

    sha256: str
    byte_count: int
    manifest_sha256: str
    members: int
    expanded_bytes: int


ARCHIVE_PIN: Final = SignalArchivePin(
    "4ca8a36083474d55426d8032275306198af5e30349c22407bc7f13bb6f768124",
    54_850_520,
    "26fd5219f481bd50d2ba8067b28cf2109e76e72dc43b2941f5905d8fdfea2e57",
    1_555,
    65_008_613,
)
FIRST_DAY: Final = date(2025, 12, 28)
DAY_COUNT: Final = 222
RUNGS: Final[tuple[ZoomTier, ...]] = (13, 9, 5, 0)
BASE_RUNG: Final[ZoomTier] = 13
ORIGINAL_OBJECT_COUNT: Final = 2
MAX_MEMBER_BYTES: Final = 8 * 1024 * 1024
EXPECTED_ROWS: Final = {"13": 3_506_555, "9": 3_506_555, "5": 3_506_555, "0": 67_464}


def _require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("evidence must be a JSON object")
    result: dict[str, object] = {}
    for key, item in value.items():
        _require(isinstance(key, str), "evidence keys must be strings")
        if isinstance(key, str):
            result[key] = item
    return result


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("evidence count must be an integer")
    return value


def _documents(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list):
        raise ValueError("evidence list is missing")
    return tuple(_mapping(item) for item in value)


def _json(payload: bytes) -> dict[str, object]:
    value: object = json.loads(payload)
    return _mapping(value)


def read_signal_archive(path: Path, *, pin: SignalArchivePin = ARCHIVE_PIN) -> dict[str, bytes]:
    """Verify bounded regular members without extracting archive paths."""
    _require(not path.is_symlink() and path.is_file(), "archive must be a regular local file")
    _require(path.stat().st_size == pin.byte_count, "archive byte count differs from pin")
    objects: dict[str, bytes] = {}
    expanded = 0
    with path.open("rb") as source:
        _require(hashlib.file_digest(source, "sha256").hexdigest() == pin.sha256, "archive checksum differs")
        source.seek(0)
        with tarfile.open(fileobj=source, mode="r|gz") as archive:
            for member in archive:
                _require(member.isfile() and member.name not in objects, "nonregular or duplicate archive member")
                _require(len(objects) < pin.members, "extra archive member")
                if member.name == "batch-summary.json":
                    digest = pin.manifest_sha256
                else:
                    _require(re.fullmatch(r"objects/[0-9a-f]{64}", member.name), "unexpected archive member path")
                    digest = member.name.removeprefix("objects/")
                expanded += member.size
                _require(0 <= member.size <= MAX_MEMBER_BYTES, "archive member exceeds byte budget")
                _require(expanded <= pin.expanded_bytes, "expanded archive exceeds byte budget")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError("archive member stream is missing")
                with stream:
                    payload = stream.read(member.size + 1)
                _require(len(payload) == member.size, "archive member is truncated")
                _require(sha256_digest(payload) == digest, "archive member checksum differs")
                objects[member.name] = payload
        source.seek(0)
        _require(hashlib.file_digest(source, "sha256").hexdigest() == pin.sha256, "archive changed during reading")
    _require(len(objects) == pin.members and expanded == pin.expanded_bytes, "archive population differs")
    _require("batch-summary.json" in objects, "archive has no batch manifest")
    return objects


def _referenced(objects: Mapping[str, bytes], reference: Mapping[str, object]) -> bytes:
    digest = reference.get("sha256")
    _require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest), "invalid reference digest")
    path = f"objects/{digest}"
    _require(reference.get("local_path") == path and path in objects, "missing content-addressed reference")
    payload = objects[path]
    _require(reference.get("byte_count") == len(payload), "reference byte count differs")
    _require(sha256_digest(payload) == digest, "reference checksum differs")
    return payload


def _table(payload: bytes) -> pa.Table:
    file = pq.ParquetFile(io.BytesIO(payload))
    _require(0 < file.metadata.num_rows <= MAX_BASE_ROWS, "Parquet row count exceeds candidate budget")
    return file.read()


def verify_signal_day(
    document: Mapping[str, object], objects: Mapping[str, bytes], dimension: pa.Table, *, day: date
) -> dict[str, object]:
    """Check old bytes, coordinate witnesses, original columns and every current derived rung."""
    _require(document.get("day") == day.isoformat(), "candidate day differs")
    _require(document.get("apply_authorized") is False, "candidate must remain preparation-only")
    receipt = _json(_referenced(objects, _mapping(document.get("receipt"))))
    _require(receipt == {key: value for key, value in document.items() if key != "receipt"}, "day receipt differs")
    originals = _documents(document.get("originals"))
    base_key = partition_path("signal", "observed", 13, day)
    marker_key = completion_marker_path("signal", "observed", 13, day)
    _require(
        len(originals) == ORIGINAL_OBJECT_COUNT and {item.get("key") for item in originals} == {base_key, marker_key},
        "original scope is not the exact base part and marker",
    )
    original_bytes = {str(item["key"]): _referenced(objects, item) for item in originals}
    base = _table(original_bytes[base_key])
    marker = PartitionCompletion.from_json_bytes(original_bytes[marker_key])
    _require(marker.part_count == 1 and marker.row_count == base.num_rows, "original completion population differs")
    for part in marker.parts:
        _require(
            part.relative_path == base_key
            and part.sha256 == sha256_digest(original_bytes[base_key])
            and part.byte_count == len(original_bytes[base_key])
            and part.row_count == base.num_rows,
            "original completion part differs",
        )
    rebuilt = build_coordinate_candidate(base, dimension, day=day)
    _require(
        document.get("original_logical_sha256") == rebuilt.original_logical_sha256
        and document.get("preserved_logical_sha256") == rebuilt.preserved_logical_sha256,
        "original-column preservation digest differs",
    )
    _require(
        document.get("mapping_sha256") == rebuilt.mapping_sha256
        and document.get("distinct_cell_count") == rebuilt.distinct_cell_count,
        "coordinate witness differs",
    )
    candidates = _documents(document.get("candidates"))
    _require(tuple(item.get("rung") for item in candidates) == RUNGS, "candidate rung set or order differs")
    outputs: list[dict[str, object]] = []
    for reference, (rung, expected) in zip(candidates, rebuilt.tables, strict=True):
        target = partition_path("signal", "observed", rung, day)
        _require(reference.get("candidate_serving_key") == target, "candidate target escapes the exact day/rung")
        table = _table(_referenced(objects, reference))
        _require(table.equals(expected), "candidate values differ from preserved-source derivation")
        _require(logical_sha256(table) == reference.get("logical_sha256"), "candidate logical digest differs")
        if rung == BASE_RUNG:
            _require(table.select(base.column_names).cast(base.schema).equals(base), "original values or order changed")
        outputs.append(
            {
                "rung": rung,
                "target_key": target,
                "row_count": table.num_rows,
                "sha256": reference["sha256"],
                "byte_count": reference["byte_count"],
                "logical_sha256": reference["logical_sha256"],
            }
        )
    return {
        "day": day.isoformat(),
        "original_row_count": base.num_rows,
        "original_logical_sha256": rebuilt.original_logical_sha256,
        "coordinate_mapping_sha256": rebuilt.mapping_sha256,
        "originals": list(originals),
        "candidates": outputs,
    }


def verify_signal_candidates(path: Path) -> dict[str, object]:
    """Emit a reproducible local admission packet; runtime ownership and publication remain unproved."""
    objects = read_signal_archive(path)
    manifest = _json(objects["batch-summary.json"])
    _require(
        manifest.get("schema_version") == "signal-coordinate-batch-preview-v1"
        and manifest.get("complete") is True
        and manifest.get("apply_authorized") is False,
        "archive is not the reviewed preparation schema",
    )
    inputs = _documents(manifest.get("source_inputs"))
    expected_sources = {
        f"{SOURCE_ROOT}/manifest.json": SOURCE_MANIFEST_SHA256,
        f"{SOURCE_ROOT}/_COMPLETE": SOURCE_COMPLETE_SHA256,
        f"{SOURCE_ROOT}/_dimensions/spatial_cell.parquet": DIMENSION_SHA256,
    }
    _require(len(inputs) == len(expected_sources), "source witness population differs")
    _require(
        {str(item.get("key")): item.get("sha256") for item in inputs} == expected_sources,
        "source witness identities differ",
    )
    source_bytes = {str(item["key"]): _referenced(objects, item) for item in inputs}
    complete = _json(source_bytes[f"{SOURCE_ROOT}/_COMPLETE"])
    _require(
        complete.get("manifest_key") == f"{SOURCE_ROOT}/manifest.json"
        and complete.get("manifest_sha256") == SOURCE_MANIFEST_SHA256,
        "source completion binding differs",
    )
    dimension_payload = source_bytes[f"{SOURCE_ROOT}/_dimensions/spatial_cell.parquet"]
    dimension = _table(dimension_payload)
    _require(
        len(dimension_payload) == DIMENSION_BYTES and dimension.num_rows == DIMENSION_ROWS,
        "coordinate witness population differs",
    )
    documents = _documents(manifest.get("days"))
    expected_days = tuple(FIRST_DAY + timedelta(days=offset) for offset in range(DAY_COUNT))
    _require(
        tuple(item.get("day") for item in documents) == tuple(day.isoformat() for day in expected_days),
        "archive does not contain the exact ordered 222-day population",
    )
    days = [
        verify_signal_day(document, objects, dimension, day=day)
        for document, day in zip(documents, expected_days, strict=True)
    ]
    rows = {
        str(rung): sum(
            _integer(candidate["row_count"])
            for item in days
            for candidate in _documents(item["candidates"])
            if candidate["rung"] == rung
        )
        for rung in RUNGS
    }
    _require(rows == EXPECTED_ROWS, "verified candidate row totals differ")
    return {
        "schema_version": "signal-candidate-admission-packet/v1",
        "apply_authorized": False,
        "archive_sha256": ARCHIVE_PIN.sha256,
        "archive_bytes": ARCHIVE_PIN.byte_count,
        "batch_manifest_sha256": ARCHIVE_PIN.manifest_sha256,
        "archive_member_count": ARCHIVE_PIN.members,
        "archive_expanded_bytes": ARCHIVE_PIN.expanded_bytes,
        "preservation_verified": True,
        "current_derivation_verified": True,
        "current_bucket_state_verified": False,
        "current_ownership_verified": False,
        "publication_performed": False,
        "availability_verified": False,
        "serving_verified": False,
        "schedule_execution_verified": False,
        "relation_retirement_performed": False,
        "bucket_requests": 0,
        "database_requests": 0,
        "required_rungs": list(RUNGS),
        "original_rows": rows["13"],
        "rows_by_rung": rows,
        "days": days,
        "rollback": {
            "originals_preserved_in_archive": True,
            "durable_remote_archive_verified": False,
            "automatic_rollback_supported": False,
            "rule": "Retain originals and stop on unknown bytes; restore only through a separately reviewed recovery.",
        },
        "required_before_admission": [
            "Pin the deployed revision, all current writers, retry workers and bounded quiescence.",
            "Under ordinary lane-day locks and publication barrier revalidate every original and target inventory.",
            "Read the exact current availability head and bootstrap marker; a surviving marker needs pointer recovery.",
            "Review the exact apply request and durable original/candidate archive plus journal and rollback packet.",
            "Implement and independently review a candidate admission operator; "
            "legacy retract/re-export is prohibited.",
            "Obtain exact owner authorization before writes; this packet authorizes no production action.",
            "Verify all four physical rungs, governed availability and selected-day/spatial/temporal neighbours.",
        ],
    }
