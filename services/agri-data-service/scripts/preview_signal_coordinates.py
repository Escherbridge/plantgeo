"""Read one signal day and emit local coordinate candidates; no apply operation exists."""

from __future__ import annotations

import argparse
import io
import json
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from agri_data_service.config import settings
from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import completion_marker_path, day_prefix, partition_path
from agri_data_service.pipeline.parquet.availability_index import (
    BotoAvailabilityStorage,
    availability_bootstrap_marker_key,
    availability_pointer_key,
)
from agri_data_service.pipeline.parquet.objectstore import BotoObjectStoreBackend, availability_lane_root
from agri_data_service.pipeline.parquet.signal_coordinate_preview import (
    DIMENSION_BYTES,
    DIMENSION_ROWS,
    DIMENSION_SHA256,
    MAX_BASE_ROWS,
    SOURCE_COMPLETE_SHA256,
    SOURCE_MANIFEST_SHA256,
    SOURCE_ROOT,
    build_coordinate_candidate,
    parquet_bytes,
)

MAX_OBJECT_BYTES = 64 * 1024 * 1024
MAX_ORIGINAL_OBJECTS = 100
MAX_ORIGINAL_BYTES = 128 * 1024 * 1024
MAX_REQUEST_BYTES = 128_000
if TYPE_CHECKING:
    from agri_data_service.foundation.parquet.zoom import ZoomTier

RUNGS: Final[tuple[ZoomTier, ...]] = (0, 5, 9, 13)


def _local_blob(out: Path, payload: bytes) -> dict[str, object]:
    digest = sha256_digest(payload)
    relative = f"objects/{digest}"
    target = out / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("xb") as stream:
            stream.write(payload)
    except FileExistsError:
        if target.read_bytes() != payload:
            raise ValueError(f"local content-addressed artifact conflicts: {relative}") from None
    return {"sha256": digest, "byte_count": len(payload), "local_path": relative}


def preview(  # noqa: PLR0912, PLR0915 - each gate independently binds the local-only evidence graph
    day: date,
    out: Path,
    original_receipts: list[dict[str, Any]],
) -> dict[str, object]:
    """Capture exact inputs, build candidates, and recheck observed identities before reporting."""
    if not date(2022, 4, 30) <= day <= date(2026, 8, 6):
        raise ValueError("day is outside the pinned canonical snapshot")
    backend = BotoObjectStoreBackend.from_credentials(settings.require_object_store())
    store = BotoAvailabilityStorage(bucket=backend.bucket, client=backend.client, prefix=settings.object_store_prefix)
    captured: dict[str, tuple[bytes, dict[str, object]]] = {}

    def capture(key: str, expected_sha: str | None = None) -> bytes:
        stored = store.read(key, max_bytes=MAX_OBJECT_BYTES)
        if stored is None:
            raise ValueError(f"required object missing: {key}")
        if expected_sha is not None and sha256_digest(stored.payload) != expected_sha:
            raise ValueError(f"pinned object checksum mismatch: {key}")
        receipt = {"key": key, **_local_blob(out, stored.payload), "etag": stored.etag, "version_id": stored.version_id}
        captured[key] = (stored.payload, receipt)
        return stored.payload

    manifest_key = f"{SOURCE_ROOT}/manifest.json"
    manifest = json.loads(capture(manifest_key, SOURCE_MANIFEST_SHA256))
    complete = json.loads(capture(f"{SOURCE_ROOT}/_COMPLETE", SOURCE_COMPLETE_SHA256))
    if complete["manifest_key"] != manifest_key or complete["manifest_sha256"] != SOURCE_MANIFEST_SHA256:
        raise ValueError("canonical completion does not bind the pinned manifest")
    dimension_key = f"{SOURCE_ROOT}/_dimensions/spatial_cell.parquet"
    dimension_ref = manifest["dimension_objects"]["spatial_cell"]
    if (
        dimension_ref["key"] != dimension_key
        or dimension_ref["sha256"] != DIMENSION_SHA256
        or dimension_ref["byte_count"] != DIMENSION_BYTES
        or dimension_ref["row_count"] != DIMENSION_ROWS
    ):
        raise ValueError("manifest spatial-cell witness differs from reviewed identity")
    dimension_payload = capture(dimension_key, DIMENSION_SHA256)
    dimension = pq.ParquetFile(io.BytesIO(dimension_payload)).read()
    if len(dimension_payload) != DIMENSION_BYTES or dimension.num_rows != DIMENSION_ROWS:
        raise ValueError("spatial-cell dimension size or population differs")

    def inventory() -> tuple[str, ...]:
        prefix = settings.object_store_prefix.strip("/")
        root = f"{prefix}/" if prefix else ""
        found: list[str] = []
        for rung in RUNGS:
            day_root = day_prefix("signal", "observed", rung, day)
            for item in backend.list_objects(f"{root}{day_root}"):
                if not item.key.startswith(f"{root}{day_root}"):
                    raise ValueError("listing escaped its exact day prefix")
                found.append(item.key[len(root) :])
                if len(found) > MAX_ORIGINAL_OBJECTS:
                    raise ValueError("original object inventory exceeds preview budget")
        return tuple(sorted(found))

    keys = inventory()
    expected = {receipt["key"]: receipt for receipt in original_receipts}
    if len(expected) != len(original_receipts) or set(keys) != set(expected):
        raise ValueError("day inventory differs from the externally pinned request")
    total_bytes = 0
    for key in keys:
        total_bytes += len(capture(key, expected[key]["sha256"]))
        if any(captured[key][1][field] != expected[key][field] for field in ("byte_count", "etag", "version_id")):
            raise ValueError(f"original object identity differs from pinned request: {key}")
        if total_bytes > MAX_ORIGINAL_BYTES:
            raise ValueError("original object bytes exceed preview budget")
    base_marker = completion_marker_path("signal", "observed", 13, day)
    if base_marker not in captured:
        raise ValueError("legacy base has no completion marker")
    marker = PartitionCompletion.from_json_bytes(captured[base_marker][0])
    expected_parts = tuple(partition_path("signal", "observed", 13, day, index) for index in range(marker.part_count))
    base_keys = {key for key in keys if key.startswith(day_prefix("signal", "observed", 13, day))}
    if base_keys != {*expected_parts, base_marker} or not expected_parts:
        raise ValueError("base inventory is not exactly its contiguous completed part set")
    files = [pq.ParquetFile(io.BytesIO(captured[key][0])) for key in expected_parts]
    if sum(file.metadata.num_rows for file in files) > MAX_BASE_ROWS:
        raise ValueError("base metadata exceeds bounded preview row count")
    parts = [file.read() for file in files]
    base = pa.concat_tables(parts)
    if base.num_rows != marker.row_count:
        raise ValueError("base row count disagrees with its completion marker")
    for part in marker.parts:
        if part.relative_path not in captured or (
            sha256_digest(captured[part.relative_path][0]) != part.sha256
            or len(captured[part.relative_path][0]) != part.byte_count
        ):
            raise ValueError("completion marker recorded part identity differs")
    candidate = build_coordinate_candidate(base, dimension, day=day)
    outputs = [
        {
            "rung": rung,
            "row_count": table.num_rows,
            "candidate_serving_key": partition_path("signal", "observed", rung, day),
            **_local_blob(out, parquet_bytes(table)),
        }
        for rung, table in candidate.tables
    ]
    lane_root = availability_lane_root("signal", "observed")
    availability: list[dict[str, object]] = []
    for key in (availability_pointer_key(lane_root), availability_bootstrap_marker_key(lane_root)):
        held = store.read(key, max_bytes=MAX_OBJECT_BYTES)
        availability.append(
            {"key": key, "present": held is not None, "sha256": None if held is None else sha256_digest(held.payload)}
        )
    if inventory() != keys:
        raise ValueError("original day inventory changed during preview")
    for key, (payload, receipt) in captured.items():
        held = store.read(key, max_bytes=MAX_OBJECT_BYTES)
        if (
            held is None
            or held.payload != payload
            or held.etag != receipt["etag"]
            or held.version_id != receipt["version_id"]
        ):
            raise ValueError(f"captured object changed during preview: {key}")
    return {
        "schema_version": "signal-coordinate-preview-v1",
        "apply_authorized": False,
        "layer": "signal",
        "kind": "observed",
        "day": day.isoformat(),
        "original_row_count": base.num_rows,
        "original_logical_sha256": candidate.original_logical_sha256,
        "original_schema_sha256": sha256_digest(base.schema.remove_metadata().serialize().to_pybytes()),
        "preserved_logical_sha256": candidate.preserved_logical_sha256,
        "mapping_sha256": candidate.mapping_sha256,
        "distinct_cell_count": candidate.distinct_cell_count,
        "original_day_keys": list(keys),
        "inputs": [captured[key][1] for key in sorted(captured)],
        "candidates": outputs,
        "availability_observation": availability,
        "limitations": [
            "not an apply manifest",
            "no publication locks acquired",
            "no bucket objects written",
            "availability observations require locked revalidation before any future repair",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--request-sha256", required=True)
    parser.add_argument("--out", type=Path, required=True)
    arguments = parser.parse_args()
    with arguments.request.open("rb") as source:
        payload = source.read(MAX_REQUEST_BYTES + 1)
    if len(payload) > MAX_REQUEST_BYTES:
        raise ValueError("request exceeds preview input budget")
    if sha256_digest(payload) != arguments.request_sha256:
        raise ValueError("request SHA-256 differs from external pin")
    request = json.loads(payload)
    if (
        set(request) != {"schema_version", "day", "objects"}
        or request["schema_version"] != "signal-coordinate-request-v1"
    ):
        raise ValueError("unknown coordinate-preview request shape")
    result = preview(date.fromisoformat(request["day"]), arguments.out, request["objects"])
    result["request_sha256"] = arguments.request_sha256
    result["request_artifact"] = _local_blob(arguments.out, payload)
    receipt = _local_blob(arguments.out, canonical_json(result).encode("utf-8"))
    print(json.dumps({"manifest": receipt, "summary": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
