"""Capture bounded, year-locked USDA classification TIFFs with replayable receipts."""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import httpx
import rasterio  # type: ignore[import-untyped]
from rasterio.warp import transform_bounds  # type: ignore[import-untyped]

from agri_data_service.pipeline.direct.crop_cover.products import (
    BASE_CELL_METRES,
    CDL_SERVICE,
    RELEASE_DAYS,
    TILE_METRES,
    capture_envelope,
    metadata_url,
    native_resolution,
)
from agri_data_service.pipeline.errors import PipelineOperationError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

MAX_TILE_BYTES = 64 * 1024 * 1024
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_CAPTURE_BYTES = 4 * 1024 * 1024 * 1024
MAX_TILES = 1600
MAX_IMAGE_PIXELS = 4000
KNOWN_RESOLUTIONS = (10, 30)
MAX_CAPTURE_WORKERS = 8
MAX_CAPTURE_SECONDS = 14400
PROJECTED_EPSG = 5070
PIXEL_SUPPORT_TOLERANCE = 1e-6


class CaptureByteBudget:
    """Bound concurrent source downloads by their actual decoded byte count."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._spent = 0

    def account(self, count: int) -> None:
        with self._lock:
            self._spent += count
            if self._spent > MAX_CAPTURE_BYTES:
                raise fail("Regional capture exceeded its total byte ceiling", code="source_budget_exhausted")


def fail(message: str, *, code: str = "source_invalid") -> PipelineOperationError:
    """Classify source and replay failures under the shared pipeline contract."""
    return PipelineOperationError(message, code=code, lane="crop-cover", stage="capture")


def canonical_bytes(value: object) -> bytes:
    """Serialize capture evidence deterministically."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256(payload: bytes) -> str:
    """Name exact source bytes."""
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class CaptureConfig:
    """Bound one annual regional capture, never a nationwide raster download."""

    output: Path
    year: int = 2025
    bbox: tuple[float, float, float, float] = field(default_factory=capture_envelope)
    resolution_m: int = 30
    workers: int = 4
    time_budget_seconds: int = 3600

    def validate(self) -> None:
        """Refuse unsupported resolution, geography or unbounded work before HTTP."""
        if self.year not in RELEASE_DAYS:
            raise fail("Only admitted annual products 2022-2025 may be captured")
        if self.resolution_m not in KNOWN_RESOLUTIONS:
            raise fail("Analysis resolution must be 10 or 30 metres")
        if self.resolution_m < native_resolution(self.year):
            raise fail("A 30-metre source must not be upsampled to pretend to have 10-metre detail")
        west, south, east, north = self.bbox
        region_west, region_south, region_east, region_north = capture_envelope()
        if not (region_west <= west < east <= region_east and region_south <= south < north <= region_north):
            raise fail("Capture must remain within the region's admitted crop envelope")
        if not 1 <= self.workers <= MAX_CAPTURE_WORKERS or not 1 <= self.time_budget_seconds <= MAX_CAPTURE_SECONDS:
            raise fail("workers must be 1-8 and time budget 1-14400 seconds")


def _get(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, str] | None,
    cap: int,
    account: Callable[[int], None] | None = None,
) -> bytes:
    """Bound decoded HTTP bytes and refuse error documents masquerading as source pixels."""
    chunks: list[bytes] = []
    total = 0
    with client.stream("GET", url, params=params) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > cap:
                raise fail(f"Source response exceeded {cap} bytes", code="source_budget_exhausted")
            if account is not None:
                account(len(chunk))
            chunks.append(chunk)
    return b"".join(chunks)


def _json_get(client: httpx.Client, suffix: str, **params: str) -> tuple[dict[str, Any], bytes]:
    raw = _get(client, CDL_SERVICE + suffix, params={"f": "json", **params}, cap=MAX_METADATA_BYTES)
    value = json.loads(raw)
    if not isinstance(value, dict) or "error" in value:
        raise fail(f"USDA refused metadata operation {suffix}: {value}")
    return value, raw


def catalog_record(catalog: dict[str, Any], year: int) -> dict[str, Any]:
    """Bind exactly one raster identifier to the requested annual native resolution."""
    resolution = native_resolution(year)
    name = f"{year}_{resolution}m_cdls"
    matches: list[dict[str, Any]] = [
        item["attributes"] for item in catalog.get("features", []) if item["attributes"].get("Name") == name
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("OBJECTID"), int):
        raise fail(f"Annual raster {name} is missing or ambiguous", code="source_unsettled")
    if catalog.get("exceededTransferLimit"):
        raise fail("USDA raster catalogue was truncated")
    return matches[0]


def planned_tiles(config: CaptureConfig) -> tuple[tuple[int, int, int, int], ...]:
    """Tile the projected regional envelope on the same 3-km support at every crop year."""
    projected = transform_bounds("EPSG:4326", "EPSG:5070", *config.bbox, densify_pts=41)
    west, south = (math.floor(value / BASE_CELL_METRES) * BASE_CELL_METRES for value in projected[:2])
    east, north = (math.ceil(value / BASE_CELL_METRES) * BASE_CELL_METRES for value in projected[2:])
    tile_m = min(TILE_METRES, MAX_IMAGE_PIXELS * config.resolution_m)
    tile_m -= tile_m % BASE_CELL_METRES
    tiles = tuple(
        (x, y, min(x + tile_m, east), min(y + tile_m, north))
        for y in range(south, north, tile_m)
        for x in range(west, east, tile_m)
    )
    if len(tiles) > MAX_TILES:
        raise fail("Regional raster tile count exceeds capture ceiling", code="source_budget_exhausted")
    return tiles


def _tile_params(bounds: tuple[int, int, int, int], config: CaptureConfig, raster_id: int) -> dict[str, str]:
    west, south, east, north = bounds
    return {
        "f": "image",
        "bbox": ",".join(map(str, bounds)),
        "bboxSR": "5070",
        "imageSR": "5070",
        "size": f"{(east - west) // config.resolution_m},{(north - south) // config.resolution_m}",
        "format": "tiff",
        "pixelType": "U8",
        "interpolation": "RSP_NearestNeighbor",
        "compression": "LZ77",
        "adjustAspectRatio": "false",
        "renderingRule": json.dumps({"rasterFunction": "None"}),
        "mosaicRule": json.dumps(
            {"mosaicMethod": "esriMosaicLockRaster", "lockRasterIds": [raster_id], "mosaicOperation": "MT_FIRST"}
        ),
    }


def verify_tiff(path: Path, bounds: tuple[int, int, int, int], resolution: int) -> None:
    """Refuse rendered RGB, reprojection drift, masked class ambiguity and malformed TIFFs."""
    with rasterio.open(path) as raster:
        west, south, east, north = bounds
        if raster.count != 1 or raster.dtypes != ("uint8",):
            raise fail("Expected one unsigned-byte CDL classification band, never rendered colours")
        if raster.crs is None or raster.crs.to_epsg() != PROJECTED_EPSG:
            raise fail("CDL export must carry EPSG:5070 equal-area coordinates")
        expected = (resolution, 0, west, 0, -resolution, north)
        if any(
            abs(actual - target) > PIXEL_SUPPORT_TOLERANCE
            for actual, target in zip(tuple(raster.transform)[:6], expected, strict=True)
        ):
            raise fail("CDL export shifted its requested pixel support")
        if raster.width != (east - west) // resolution or raster.height != (north - south) // resolution:
            raise fail("CDL export dimensions do not match the requested resolution")
        if raster.nodata not in (None, 0, 255):
            raise fail(f"Unrecognized CDL NoData value {raster.nodata}")


def _capture_tile(
    bounds: tuple[int, int, int, int],
    config: CaptureConfig,
    raster_id: int,
    deadline: float,
    budget: CaptureByteBudget,
) -> dict[str, Any]:
    if time.monotonic() >= deadline:
        raise fail("Capture time budget exhausted; verified tiles remain resumable", code="source_budget_exhausted")
    params = _tile_params(bounds, config, raster_id)
    key = sha256(canonical_bytes(params))
    path = config.output / f"tile-{key}.tif"
    receipt_path = path.with_suffix(".json")
    if path.exists() and receipt_path.exists():
        budget.account(path.stat().st_size)
        previous = json.loads(receipt_path.read_text())
        if previous.get("params") != params or not previous.get("captured_at"):
            raise fail("Cached tile has no matching request or source-capture timestamp")
        if path.stat().st_size > MAX_TILE_BYTES or sha256(path.read_bytes()) != previous["sha256"]:
            raise fail(f"Cached tile failed its byte receipt: {path.name}")
    else:
        with httpx.Client(timeout=120, follow_redirects=False) as client:
            raw = _get(client, CDL_SERVICE + "/exportImage", params=params, cap=MAX_TILE_BYTES, account=budget.account)
        temporary = path.with_suffix(".partial")
        temporary.write_bytes(raw)
        verify_tiff(temporary, bounds, config.resolution_m)
        temporary.replace(path)
        receipt_path.write_bytes(
            canonical_bytes(
                {
                    "sha256": sha256(raw),
                    "params": params,
                    "captured_at": datetime.now(UTC).isoformat(),
                }
            )
        )
    verify_tiff(path, bounds, config.resolution_m)
    return {"file": path.name, "bounds": bounds, "sha256": sha256(path.read_bytes()), "bytes": path.stat().st_size}


def capture(config: CaptureConfig) -> Path:
    """Capture one complete regional annual release before writing its closing manifest."""
    config.validate()
    config.output.mkdir(parents=True, exist_ok=True)
    manifest_path = config.output / "manifest.json"
    if manifest_path.exists():
        manifest = read_manifest(manifest_path)
        if (manifest["observed_year"], tuple(manifest["bbox"]), manifest["analysis_resolution_m"]) != (
            config.year,
            config.bbox,
            config.resolution_m,
        ):
            raise fail("Existing capture has different year, bbox or analysis resolution")
        return manifest_path
    journal_path = config.output / "capture-start.json"
    identity = {"year": config.year, "bbox": list(config.bbox), "resolution_m": config.resolution_m}
    if journal_path.exists():
        journal = json.loads(journal_path.read_bytes())
        if journal["identity"] != identity:
            raise fail("Partial capture directory belongs to a different annual support")
        started_at = datetime.fromisoformat(journal["started_at"])
    else:
        started_at = datetime.now(UTC)
        journal_path.write_bytes(canonical_bytes({"identity": identity, "started_at": started_at.isoformat()}))
    deadline = time.monotonic() + config.time_budget_seconds
    with httpx.Client(timeout=60, follow_redirects=False) as client:
        catalog, catalog_bytes = _json_get(
            client, "/query", where="1=1", outFields="OBJECTID,Name", returnGeometry="false"
        )
        record = catalog_record(catalog, config.year)
        legend, legend_bytes = _json_get(client, "/rasterAttributeTable")
        metadata = _get(client, metadata_url(config.year), params=None, cap=MAX_METADATA_BYTES)
    match = re.search(rb'<meta\s+name="dc.date"\s+content="(\d{8})"', metadata)
    publication = date.fromisoformat(match[1].decode()) if match else None
    if publication != RELEASE_DAYS[config.year]:
        raise fail("Official publication date changed or is missing; annual admission requires review")
    tiles = planned_tiles(config)
    budget = CaptureByteBudget()
    with ThreadPoolExecutor(max_workers=config.workers) as pool:
        receipts = list(
            pool.map(lambda bounds: _capture_tile(bounds, config, record["OBJECTID"], deadline, budget), tiles)
        )
    if sum(receipt["bytes"] for receipt in receipts) > MAX_CAPTURE_BYTES:
        raise fail("Regional capture exceeded its total byte ceiling", code="source_budget_exhausted")
    names = {
        str(item["attributes"]["Value"]): item["attributes"]["Class_Names"]
        for item in legend["features"]
        if item["attributes"].get("Class_Names")
    }
    manifest = {
        "format": "plantgeo-cdl-capture/v1",
        "observed_year": config.year,
        "release_day": publication.isoformat(),
        "source": "usda-cdl",
        "source_url": CDL_SERVICE,
        "metadata_url": metadata_url(config.year),
        "bbox": config.bbox,
        "raster": record,
        "source_resolution_m": native_resolution(config.year),
        "analysis_resolution_m": config.resolution_m,
        "coverage": "EPSG:5070 grid-aligned envelope containing bbox; US data only; adjoining-state fringes included",
        "started_at": started_at.isoformat(),
        "captured_at": datetime.now(UTC).isoformat(),
        "catalog_sha256": sha256(catalog_bytes),
        "legend_sha256": sha256(legend_bytes),
        "metadata_sha256": sha256(metadata),
        "class_names": names,
        "tiles": receipts,
    }
    for filename, payload in (
        ("catalog.json", catalog_bytes),
        ("legend.json", legend_bytes),
        ("metadata.html", metadata),
    ):
        (config.output / filename).write_bytes(payload)
    manifest_path.write_bytes(canonical_bytes(manifest))
    return manifest_path


def read_manifest(path: Path) -> dict[str, Any]:
    """Replay every bounded tile and metadata receipt before trusting a captured release."""
    if path.stat().st_size > MAX_METADATA_BYTES:
        raise fail("Capture manifest exceeds byte ceiling")
    manifest: dict[str, Any] = json.loads(path.read_bytes())
    if manifest.get("format") != "plantgeo-cdl-capture/v1":
        raise fail("Unrecognized CDL capture format")
    config = CaptureConfig(
        path.parent, manifest["observed_year"], tuple(manifest["bbox"]), manifest["analysis_resolution_m"]
    )
    config.validate()
    expected = planned_tiles(config)
    if tuple(tuple(tile["bounds"]) for tile in manifest["tiles"]) != expected:
        raise fail("Capture is truncated, duplicated or outside its declared regional support")
    if manifest["release_day"] != RELEASE_DAYS[config.year].isoformat():
        raise fail("Capture release date does not match admitted source publication")
    if manifest["source_url"] != CDL_SERVICE or manifest["metadata_url"] != metadata_url(config.year):
        raise fail("Capture source URLs disagree with the admitted annual product")
    if manifest["source_resolution_m"] != native_resolution(config.year):
        raise fail("Capture native source resolution disagrees with the annual catalogue")
    started_at = datetime.fromisoformat(manifest["started_at"])
    captured_at = datetime.fromisoformat(manifest["captured_at"])
    if started_at.utcoffset() is None or captured_at.utcoffset() is None or captured_at < started_at:
        raise fail("Capture time interval must be ordered and timezone-aware")
    if sum(tile["bytes"] for tile in manifest["tiles"]) > MAX_CAPTURE_BYTES:
        raise fail("Capture exceeds its total byte ceiling")
    _verify_members(path, manifest)
    _verify_source_claims(path, manifest, config.year)
    return manifest


def _verify_members(path: Path, manifest: dict[str, Any]) -> None:
    """Bind every local input to the closing source manifest's byte receipts."""
    for tile in manifest["tiles"]:
        tile_path = path.parent / tile["file"]
        if not tile_path.resolve().is_relative_to(path.parent.resolve()) or tile_path.name != tile["file"]:
            raise fail("Capture tile escapes its artifact directory")
        if tile_path.stat().st_size != tile["bytes"] or tile["bytes"] > MAX_TILE_BYTES:
            raise fail("Capture tile byte count changed")
        if sha256(tile_path.read_bytes()) != tile["sha256"]:
            raise fail("Capture tile failed its SHA-256 receipt")
    for name in ("catalog", "legend", "metadata"):
        suffix = ".html" if name == "metadata" else ".json"
        if sha256((path.parent / (name + suffix)).read_bytes()) != manifest[name + "_sha256"]:
            raise fail(f"Captured {name} failed its SHA-256 receipt")


def _verify_source_claims(path: Path, manifest: dict[str, Any], year: int) -> None:
    """Re-derive the annual identity, class labels and publication date from archived evidence."""
    catalog = json.loads((path.parent / "catalog.json").read_bytes())
    if manifest["raster"] != catalog_record(catalog, year):
        raise fail("Capture raster identity disagrees with archived USDA catalogue")
    legend = json.loads((path.parent / "legend.json").read_bytes())
    names = {
        str(item["attributes"]["Value"]): item["attributes"]["Class_Names"]
        for item in legend["features"]
        if item["attributes"].get("Class_Names")
    }
    if manifest["class_names"] != names:
        raise fail("Capture class names disagree with archived USDA legend")
    match = re.search(rb'<meta\s+name="dc.date"\s+content="(\d{8})"', (path.parent / "metadata.html").read_bytes())
    if match is None or date.fromisoformat(match[1].decode()) != RELEASE_DAYS[year]:
        raise fail("Archived source metadata does not prove the admitted annual publication date")
