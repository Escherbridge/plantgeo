"""Durable content-addressed source graphs for exact annual crop replay."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from agri_data_service.pipeline.direct.crop_cover.source import (
    MAX_CAPTURE_BYTES,
    MAX_METADATA_BYTES,
    MAX_TILE_BYTES,
    CaptureConfig,
    fail,
    planned_tiles,
    read_manifest,
    sha256,
)

if TYPE_CHECKING:
    from pathlib import Path

    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage

ARCHIVE_ROOT = "layer=crop-cover/kind=observed/availability/source-captures"
SHA256_HEX_LENGTH = 64


def manifest_key(identity: str) -> str:
    """Require an exact lowercase SHA identity before naming an archived source graph."""
    if len(identity) != SHA256_HEX_LENGTH or any(character not in "0123456789abcdef" for character in identity):
        raise fail("Malformed crop source manifest SHA-256")
    return f"{ARCHIVE_ROOT}/manifests/{identity}.json"


def _members(manifest: dict[str, Any]) -> list[tuple[str, str, int, str]]:
    members = [(tile["file"], tile["sha256"], MAX_TILE_BYTES, "image/tiff") for tile in manifest["tiles"]]
    for name in ("catalog", "legend", "metadata"):
        extension, content_type = (".html", "text/html") if name == "metadata" else (".json", "application/json")
        members.append((name + extension, manifest[name + "_sha256"], MAX_METADATA_BYTES, content_type))
    return members


def archive_capture(storage: AvailabilityStorage, path: Path) -> str:
    """Close an immutable remote graph only after all verified raw source bytes are durable."""
    manifest = read_manifest(path)
    payload = path.read_bytes()
    key = manifest_key(sha256(payload))

    def upload(member: tuple[str, str, int, str]) -> None:
        filename, identity, _cap, content_type = member
        body = (path.parent / filename).read_bytes()
        if sha256(body) != identity:
            raise fail("Source bytes changed while archiving a crop capture")
        storage.put_immutable(f"{ARCHIVE_ROOT}/blobs/{identity}", body, content_type=content_type)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(upload, _members(manifest)))
    storage.put_immutable(key, payload, content_type="application/json")
    return key


def replay_capture(storage: AvailabilityStorage, identity: str, directory: Path) -> Path:
    """Restore and verify the original source graph without contacting the mutable USDA service."""
    key = manifest_key(identity)
    stored = storage.read(key, max_bytes=MAX_METADATA_BYTES)
    if stored is None or sha256(stored.payload) != identity:
        raise fail("Archived crop source manifest is missing or fails its SHA-256 receipt")
    manifest: dict[str, Any] = json.loads(stored.payload)
    config = CaptureConfig(
        directory, manifest["observed_year"], tuple(manifest["bbox"]), manifest["analysis_resolution_m"]
    )
    config.validate()
    if tuple(tuple(tile["bounds"]) for tile in manifest["tiles"]) != planned_tiles(config):
        raise fail("Archived crop capture has an incomplete or foreign tile inventory")
    if sum(tile["bytes"] for tile in manifest["tiles"]) > MAX_CAPTURE_BYTES:
        raise fail("Archived crop capture exceeds the full-region byte ceiling")
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "manifest.json"
    if destination.exists():
        if sha256(destination.read_bytes()) != identity:
            raise fail("Replay directory already holds a different immutable source capture")
        read_manifest(destination)
        return destination

    def restore(member: tuple[str, str, int, str]) -> None:
        filename, expected_sha, cap, _content_type = member
        target = directory / filename
        if target.name != filename or not target.resolve().is_relative_to(directory.resolve()):
            raise fail("Archived crop member escapes its replay directory")
        if target.exists() and target.stat().st_size <= cap and sha256(target.read_bytes()) == expected_sha:
            return
        blob = storage.read(f"{ARCHIVE_ROOT}/blobs/{expected_sha}", max_bytes=cap)
        if blob is None or sha256(blob.payload) != expected_sha:
            raise fail(f"Archived crop source member {filename} is missing or fails its SHA-256 receipt")
        temporary = target.with_suffix(".partial")
        temporary.write_bytes(blob.payload)
        temporary.replace(target)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(restore, _members(manifest)))
    temporary_manifest = directory / "replay-manifest.json"
    temporary_manifest.write_bytes(stored.payload)
    read_manifest(temporary_manifest)
    temporary_manifest.replace(destination)
    return destination
