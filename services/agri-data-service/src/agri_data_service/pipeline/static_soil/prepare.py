"""Prepare the saved twelve-file bundle without touching a remote store; see AGENTS.md."""

from __future__ import annotations

import gzip
import io
import json
import struct
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agri_data_service.pipeline.static_soil.models import (
    MAX_BUNDLE_BYTES,
    MAX_COG_BYTES,
    MAX_METADATA_BYTES,
    MAX_OBJECT_BYTES,
    PROPERTIES,
    PROPERTY_DIVISORS,
    PROPERTY_UNITS,
    AssetIdentity,
    SoilPropertyAsset,
    StaticSoilCandidate,
    canonical_bytes,
    digest,
)
from agri_data_service.pipeline.static_soil.raster import inspect_grid, memory_raster, validate_tags

if TYPE_CHECKING:
    from pathlib import Path

PMTILES_HEADER_BYTES = 127
PMTILES_PNG = 2
PMTILES_UNCOMPRESSED = 1
PMTILES_GZIP = 2
TILE_BOUND_SCALE = 10_000_000


def json_object(value: object) -> dict[str, object]:
    """Narrow untrusted metadata without permissive coercion."""
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("soil evidence requires a JSON object")
    return dict(value)


def json_array(value: object) -> list[object]:
    """Require a JSON array, retaining each untrusted value."""
    if not isinstance(value, list):
        raise ValueError("soil evidence requires a JSON array")
    return list(value)


def read_bounded(path: Path, limit: int = MAX_OBJECT_BYTES) -> bytes:
    """Read a complete regular file with a strict byte cap."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("soil evidence must be a bounded regular file")
    with path.open("rb") as handle:
        payload = handle.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("soil evidence exceeded its byte cap during the read")
    return payload


def _identity(payload: bytes) -> AssetIdentity:
    identity = digest(payload)
    return AssetIdentity(sha256=identity, byte_count=len(payload), key=f"static-soil/objects/sha256/{identity}")


def inspect_pmtiles(payload: bytes) -> dict[str, object]:
    """Inspect bounded v3 header and JSON metadata, without decoding imagery."""
    if len(payload) < PMTILES_HEADER_BYTES or payload[:8] != b"PMTiles\x03":
        raise ValueError("soil imagery requires a PMTiles v3 archive")
    offset, length = struct.unpack_from("<QQ", payload, 24)
    if offset < PMTILES_HEADER_BYTES or length > MAX_METADATA_BYTES or offset + length > len(payload):
        raise ValueError("soil PMTiles metadata lies outside its bounded archive")
    if payload[99] != PMTILES_PNG or payload[100:102] != bytes((0, 10)):
        raise ValueError("saved soil PMTiles must contain PNG imagery at zooms 0 through 10")
    metadata = payload[offset : offset + length]
    if payload[97] == PMTILES_GZIP:
        with gzip.GzipFile(fileobj=io.BytesIO(metadata)) as handle:
            metadata = handle.read(MAX_METADATA_BYTES + 1)
    elif payload[97] != PMTILES_UNCOMPRESSED:
        raise ValueError("unsupported soil PMTiles metadata compression")
    if len(metadata) > MAX_METADATA_BYTES:
        raise ValueError("decoded soil PMTiles metadata exceeds its byte budget")
    parsed = json_object(json.loads(metadata))
    return {
        "unit": parsed.get("unit"),
        "color_ramp": parsed.get("colorRamp"),
        "addressed_tiles": struct.unpack_from("<Q", payload, 72)[0],
        "tile_bounds": tuple(value / TILE_BOUND_SCALE for value in struct.unpack_from("<iiii", payload, 102)),
    }


def _source_entries(payload: bytes) -> dict[str, dict[str, object]]:
    manifest = json_object(json.loads(payload))
    if manifest.get("sourceRelease") != "v2.0" or manifest.get("license") != "CC-BY 4.0":
        raise ValueError("unexpected saved SoilGrids release or license")
    entries = [json_object(item) for item in json_array(manifest.get("artifacts"))]
    if tuple(item.get("property") for item in entries) != PROPERTIES:
        raise ValueError("source manifest must declare exactly the six ordered soil properties")
    return dict(zip(PROPERTIES, entries, strict=True))


def _receipts(payload: bytes) -> dict[tuple[str, str], dict[str, object]]:
    rows = [json_object(item) for item in json_array(json.loads(payload))]
    result: dict[tuple[str, str], dict[str, object]] = {}
    for row in rows:
        name, form = row.get("property"), row.get("format")
        if not isinstance(name, str) or not isinstance(form, str) or (name, form) in result:
            raise ValueError("duplicate or invalid soil full-object receipt")
        result[name, form] = row
    if set(result) != {(name, form) for name in PROPERTIES for form in ("cog", "pmtiles")}:
        raise ValueError("soil receipts must cover all twelve exact local objects")
    sizes = [row.get("bytes") for row in rows]
    if any(type(size) is not int or not 0 < size <= MAX_OBJECT_BYTES for size in sizes):
        raise ValueError("soil receipt declares an invalid object byte budget")
    total = sum(size for size in sizes if isinstance(size, int))
    cog_total = sum(
        row["bytes"] for (_, form), row in result.items() if form == "cog" and isinstance(row["bytes"], int)
    )
    if total > MAX_BUNDLE_BYTES or cog_total > MAX_COG_BYTES:
        raise ValueError("soil receipt exceeds its complete-object byte budget")
    return result


def _check_receipt(payload: bytes, receipt: dict[str, object]) -> None:
    if type(receipt.get("bytes")) is not int or receipt["bytes"] != len(payload):
        raise ValueError("soil full-object byte length disagrees with preserved receipt")
    if receipt.get("sha256") != digest(payload):
        raise ValueError("soil full-object hash disagrees with preserved receipt")


def _property_asset(name: str, cog: bytes, tiles: bytes) -> SoilPropertyAsset:
    index = PROPERTIES.index(name)
    with memory_raster(cog) as memory, memory.open() as source:
        grid = inspect_grid(source)
        validate_tags(source, property_name=name, divisor=PROPERTY_DIVISORS[index], unit=PROPERTY_UNITS[index])
    metadata = inspect_pmtiles(tiles)
    return SoilPropertyAsset.model_validate_json(
        canonical_bytes(
            {
                **metadata,
                "property_name": name,
                "scale_divisor": PROPERTY_DIVISORS[index],
                "cog": _identity(cog).model_dump(),
                "pmtiles": _identity(tiles).model_dump(),
                "grid": grid.model_dump(),
                "source_url": f"https://files.isric.org/soilgrids/latest/data/{name}/",
            }
        )
    )


def _verified_inputs(
    source_root: Path, entries: dict[str, dict[str, object]], receipts: dict[tuple[str, str], dict[str, object]]
) -> tuple[tuple[SoilPropertyAsset, ...], dict[str, bytes]]:
    assets: list[SoilPropertyAsset] = []
    blobs: dict[str, bytes] = {}
    for name in PROPERTIES:
        filename = f"{name}_0-5cm_mean_4326.tif"
        if entries[name].get("file") != filename:
            raise ValueError("saved COG filename differs from its declared property")
        cog_receipt = receipts[name, "cog"]
        tile_receipt = receipts[name, "pmtiles"]
        cog_limit, tile_limit = cog_receipt["bytes"], tile_receipt["bytes"]
        if not isinstance(cog_limit, int) or not isinstance(tile_limit, int):
            raise ValueError("soil receipt byte lengths must be integers")
        cog = read_bounded(source_root / filename, cog_limit)
        tiles = read_bounded(source_root / "pmtiles" / f"{name}_0-5cm_mean.pmtiles", tile_limit)
        _check_receipt(cog, receipts[name, "cog"])
        _check_receipt(tiles, receipts[name, "pmtiles"])
        if entries[name].get("checksumSha256") != digest(cog) or entries[name].get("sizeBytes") != len(cog):
            raise ValueError("COG bytes disagree with the saved source manifest")
        assets.append(_property_asset(name, cog, tiles))
        blobs[digest(cog)] = cog
        blobs[digest(tiles)] = tiles
    return tuple(assets), blobs


def prepare_saved_soil(
    *,
    source_root: Path,
    hash_receipt: Path,
    expected_source_sha256: str,
    expected_receipt_sha256: str,
    output: Path,
) -> StaticSoilCandidate:
    """Verify all saved bytes, then preserve an exclusive local candidate bundle."""
    source_manifest = read_bounded(source_root / "manifest.json", MAX_METADATA_BYTES)
    receipt = read_bounded(hash_receipt, MAX_METADATA_BYTES)
    if digest(source_manifest) != expected_source_sha256 or digest(receipt) != expected_receipt_sha256:
        raise ValueError("soil source or independent receipt pin disagrees")
    assets, blobs = _verified_inputs(source_root, _source_entries(source_manifest), _receipts(receipt))
    candidate = StaticSoilCandidate(
        prepared_at=datetime.now(UTC),
        source_manifest_sha256=digest(source_manifest),
        asset_receipt_sha256=digest(receipt),
        assets=assets,
    )
    output.mkdir(parents=True, exist_ok=False)
    (output / "objects").mkdir()
    blobs[digest(source_manifest)] = source_manifest
    blobs[digest(receipt)] = receipt
    for identity, payload in blobs.items():
        with (output / "objects" / identity).open("xb") as handle:
            handle.write(payload)
    candidate_bytes = canonical_bytes(candidate.model_dump(mode="json"))
    with (output / "candidate.json").open("xb") as handle:
        handle.write(candidate_bytes)
    with (output / "candidate.sha256").open("x", encoding="ascii") as handle:
        handle.write(digest(candidate_bytes) + "\n")
    return candidate
