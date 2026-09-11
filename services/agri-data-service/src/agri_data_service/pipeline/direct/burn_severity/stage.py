"""Durable bounded MTBS staging queue; no stage is serving authority by itself."""

from __future__ import annotations

import json
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.direct.burn_severity.capture import prepare_capture, validate_capture
from agri_data_service.pipeline.direct.burn_severity.current_snapshot import (
    MAX_ARTIFACT_BYTES,
    MAX_MANIFEST_BYTES,
    canonical_bytes,
    digest,
    validate_source_manifest,
)
from agri_data_service.warehouse.mtbs_snapshots import descriptor_from_manifest

if TYPE_CHECKING:
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage, StoredAvailabilityObject

ROOT = "layer=burn-severity/kind=observed/availability"
QUEUE_KEY = f"{ROOT}/mtbs-staging/_STAGED.json"
MAX_PENDING = 8
MAX_QUEUE_BYTES = 64 * 1024
SHA_LENGTH = 64


def checked_sha(value: object) -> str:
    if not isinstance(value, str) or len(value) != SHA_LENGTH or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("invalid MTBS staged content identity")
    return value


def manifest_key(identity: str) -> str:
    return f"{ROOT}/mtbs-snapshots/manifest={checked_sha(identity)}.json"


def blob_key(identity: str) -> str:
    return f"{ROOT}/mtbs-staging/blobs/{checked_sha(identity)}"


def stage_key(identity: str) -> str:
    return f"{ROOT}/mtbs-staging/stage={checked_sha(identity)}.json"


def read_queue(storage: AvailabilityStorage) -> tuple[StoredAvailabilityObject | None, dict[str, Any]]:
    stored = storage.read(QUEUE_KEY, max_bytes=MAX_QUEUE_BYTES)
    if stored is None:
        return None, {
            "schema": "mtbs-staging-queue/v1",
            "last_capture_day": None,
            "last_content_sha256": None,
            "pending": [],
        }
    queue = json.loads(stored.payload)
    if not isinstance(queue, dict) or set(queue) != {"schema", "last_capture_day", "last_content_sha256", "pending"}:
        raise ValueError("invalid MTBS stage queue shape")
    if (
        queue["schema"] != "mtbs-staging-queue/v1"
        or not isinstance(queue["pending"], list)
        or len(queue["pending"]) > MAX_PENDING
    ):
        raise ValueError("invalid or excessive MTBS stage queue")
    if queue["last_capture_day"] is not None:
        date.fromisoformat(queue["last_capture_day"])
        checked_sha(queue["last_content_sha256"])
    identities = []
    for entry in queue["pending"]:
        if set(entry) != {"manifest_sha256", "available_day"}:
            raise ValueError("invalid MTBS stage queue entry")
        identities.append(checked_sha(entry["manifest_sha256"]))
        date.fromisoformat(entry["available_day"])
    if len(set(identities)) != len(identities) or len({entry["available_day"] for entry in queue["pending"]}) != len(
        identities
    ):
        raise ValueError("duplicate MTBS queued manifest or publication day")
    return stored, queue


def capture_due(queue: dict[str, Any], today: date) -> bool:
    last = queue["last_capture_day"]
    return last is None or today - date.fromisoformat(last) >= timedelta(days=7)


def eligible_stage(queue: dict[str, Any], today: date) -> dict[str, str] | None:
    entries = [entry for entry in queue["pending"] if date.fromisoformat(entry["available_day"]) <= today]
    return min(entries, key=lambda entry: entry["available_day"]) if entries else None


def record_unchanged_capture(storage: AvailabilityStorage, *, capture: Path, identity: str) -> bool:
    """Retain verified check evidence and advance cadence without deriving an unchanged snapshot."""
    manifest, _features = validate_capture(capture, checked_sha(identity))
    stored, queue = read_queue(storage)
    if queue["last_content_sha256"] != manifest["source_content_sha256"]:
        return False
    for receipt in manifest["responses"]:
        path = capture / "blobs" / checked_sha(receipt["sha256"])
        with path.open("rb") as handle:
            body = handle.read(receipt["bytes"] + 1)
        if len(body) != receipt["bytes"] or digest(body) != receipt["sha256"]:
            raise ValueError("unchanged MTBS evidence changed before archiving")
        storage.put_immutable(blob_key(receipt["sha256"]), body, content_type="application/octet-stream")
    storage.put_immutable(manifest_key(identity), canonical_bytes(manifest), content_type="application/json")
    storage.put_immutable(
        f"{ROOT}/mtbs-staging/checks/{identity}.json",
        canonical_bytes(
            {
                "captured_through": manifest["captured_through"],
                "manifest_sha256": identity,
                "source_content_sha256": manifest["source_content_sha256"],
            }
        ),
        content_type="application/json",
    )
    for _attempt in range(3):
        if queue["last_content_sha256"] != manifest["source_content_sha256"]:
            return False
        captured_day = manifest["captured_through"][:10]
        if queue["last_capture_day"] is not None and captured_day < queue["last_capture_day"]:
            raise ValueError("older MTBS capture cannot replace newer stage metadata")
        queue["last_capture_day"] = captured_day
        if storage.compare_and_swap(
            QUEUE_KEY,
            canonical_bytes(queue),
            expected_etag=None if stored is None else stored.etag,
            content_type="application/json",
        ):
            return True
        stored, queue = read_queue(storage)
    raise ValueError("unchanged MTBS check remained contended")


def stage_prepared(  # noqa: PLR0912 - ordered artifact and queue admission checks
    storage: AvailabilityStorage, *, capture: Path, prepared: Path, manifest_sha256: str
) -> dict[str, object]:
    """Reproduce and archive a candidate before CAS-enqueuing it; never write serving partitions."""
    identity = checked_sha(manifest_sha256)
    with tempfile.TemporaryDirectory(prefix="mtbs-stage-verify-") as temporary:
        reproduced = cast("dict[str, Any]", prepare_capture(capture, identity, Path(temporary) / "prepared"))
    with (prepared / "preparation.json").open("rb") as handle:
        preparation_body = handle.read(MAX_MANIFEST_BYTES + 1)
    if len(preparation_body) > MAX_MANIFEST_BYTES:
        raise ValueError("MTBS preparation receipt exceeds its byte cap")
    if preparation_body != canonical_bytes(reproduced):
        raise ValueError("prepared MTBS artifacts do not reproduce from immutable source evidence")
    with (capture / "manifest.json").open("rb") as handle:
        manifest_body = handle.read(MAX_MANIFEST_BYTES + 1)
    if len(manifest_body) > MAX_MANIFEST_BYTES:
        raise ValueError("MTBS source manifest exceeds its byte cap")
    manifest = validate_source_manifest(json.loads(manifest_body))
    if digest(manifest_body) != identity:
        raise ValueError("MTBS source manifest changed before staging")
    if reproduced["descriptor"] != descriptor_from_manifest(manifest_body, expected_sha256=identity).to_wire():
        raise ValueError("MTBS prepared descriptor differs from governed serving admission")
    references = [(capture / "blobs", receipt) for receipt in manifest["responses"]]
    references.extend((prepared / "blobs", receipt) for receipt in reproduced["rungs"])
    total = 0
    for directory, reference in references:
        sha = checked_sha(reference["sha256"])
        path = directory / sha
        if path.is_symlink():
            raise ValueError("staged evidence must not be a symlink")
        with path.open("rb") as handle:
            body = handle.read(reference["bytes"] + 1)
        total += len(body)
        if total > MAX_ARTIFACT_BYTES or len(body) != reference["bytes"] or digest(body) != sha:
            raise ValueError("staged MTBS evidence changed or exceeds byte budget")
        storage.put_immutable(blob_key(sha), body, content_type="application/octet-stream")
    storage.put_immutable(manifest_key(identity), manifest_body, content_type="application/json")
    storage.put_immutable(stage_key(identity), preparation_body, content_type="application/json")
    checked_at = manifest["captured_through"]
    captured_day = checked_at[:10]
    for _attempt in range(3):
        stored, queue = read_queue(storage)
        unchanged = queue["last_content_sha256"] == manifest["source_content_sha256"]
        if not unchanged and not any(entry["manifest_sha256"] == identity for entry in queue["pending"]):
            if len(queue["pending"]) >= MAX_PENDING or any(
                entry["available_day"] == manifest["available_day"] for entry in queue["pending"]
            ):
                raise ValueError("MTBS queue is full or another capture owns this publication day")
            queue["pending"].append({"manifest_sha256": identity, "available_day": manifest["available_day"]})
        if queue["last_capture_day"] is not None and captured_day < queue["last_capture_day"]:
            raise ValueError("older MTBS capture cannot replace newer stage metadata")
        queue["last_capture_day"] = captured_day
        queue["last_content_sha256"] = manifest["source_content_sha256"]
        storage.put_immutable(
            f"{ROOT}/mtbs-staging/checks/{identity}.json",
            canonical_bytes(
                {
                    "captured_through": checked_at,
                    "manifest_sha256": identity,
                    "source_content_sha256": manifest["source_content_sha256"],
                }
            ),
            content_type="application/json",
        )
        if storage.compare_and_swap(
            QUEUE_KEY,
            canonical_bytes(queue),
            expected_etag=None if stored is None else stored.etag,
            content_type="application/json",
        ):
            return {
                "status": "checked_unchanged" if unchanged else "staged",
                "manifest_sha256": identity,
                "available_day": manifest["available_day"],
                "pending": len(queue["pending"]),
            }
    raise ValueError("MTBS queue remained contended after bounded CAS attempts")


def load_stage(storage: AvailabilityStorage, identity: str) -> tuple[dict[str, Any], dict[str, Any]]:
    identity = checked_sha(identity)
    source = storage.read(manifest_key(identity), max_bytes=MAX_MANIFEST_BYTES)
    stage = storage.read(stage_key(identity), max_bytes=MAX_MANIFEST_BYTES)
    if source is None or stage is None or digest(source.payload) != identity:
        raise ValueError("MTBS stage lacks its pinned source manifest")
    manifest = validate_source_manifest(json.loads(source.payload))
    descriptor = descriptor_from_manifest(source.payload, expected_sha256=identity)
    prepared = json.loads(stage.payload)
    if prepared.get("schema") != "mtbs-current-snapshot-preparation/v1" or prepared.get("apply_authority") is not False:
        raise ValueError("invalid MTBS stage preparation receipt")
    if prepared.get("manifest") != {"sha256": identity, "bytes": len(source.payload)}:
        raise ValueError("MTBS stage is bound to a different source manifest")
    expected = {
        key: value
        for key, value in manifest.items()
        if key not in {"responses", "counts_by_year", "consistency", "source_content_sha256"}
    }
    expected["manifest_sha256"] = identity
    if prepared.get("descriptor") != expected or expected != descriptor.to_wire():
        raise ValueError("MTBS staged descriptor differs from its immutable source")
    rungs = prepared.get("rungs")
    if (
        not isinstance(rungs, list)
        or len(rungs) != len(ZOOM_TIERS)
        or {row["zoom"] for row in rungs} != set(ZOOM_TIERS)
    ):
        raise ValueError("MTBS stage lacks its full prepared ladder")
    for row in rungs:
        checked_sha(row["sha256"])
        if (
            row["rows"] != manifest["source_row_count"]
            or type(row["bytes"]) is not int
            or not 0 < row["bytes"] <= MAX_ARTIFACT_BYTES
        ):
            raise ValueError("invalid MTBS staged rung receipt")
    return manifest, prepared


def remove_published_stage(storage: AvailabilityStorage, identity: str) -> None:
    for _attempt in range(3):
        stored, queue = read_queue(storage)
        queue["pending"] = [entry for entry in queue["pending"] if entry["manifest_sha256"] != identity]
        if storage.compare_and_swap(
            QUEUE_KEY,
            canonical_bytes(queue),
            expected_etag=None if stored is None else stored.etag,
            content_type="application/json",
        ):
            return
    raise ValueError("published MTBS stage remains queued after bounded CAS attempts")
