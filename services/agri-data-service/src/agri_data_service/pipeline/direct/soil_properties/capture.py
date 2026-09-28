"""`capture`: HEAD the thirty pins, refuse on drift, then save each native Homolosine window (CONTRACT C8).

Resumable per file: a GeoTIFF whose receipt matches its pin and its bytes is reused. See
`pipeline/direct/soil_properties/AGENTS.md`, "Capture".
"""

from __future__ import annotations

import asyncio
import functools
import json
import math
import time
from typing import TYPE_CHECKING, Any, Final

import httpx
import numpy as np
import rasterio  # type: ignore[import-untyped]
from rasterio.errors import RasterioIOError  # type: ignore[import-untyped]
from rasterio.warp import transform_bounds  # type: ignore[import-untyped]
from rasterio.windows import Window, from_bounds  # type: ignore[import-untyped]

from agri_data_service.pipeline.direct.soil_properties.products import (
    ISRIC_DEPTH_LABELS,
    LATTICE_EAST,
    LATTICE_NORTH,
    LATTICE_SOUTH,
    LATTICE_WEST,
    RELEASE_DAY,
    RELEASE_ID,
    SOURCE_FILE_PINS,
    SOURCE_RELEASE,
    SourceFilePin,
    latest_pin,
)
from agri_data_service.pipeline.direct.soil_properties.source import (
    RetryPolicy,
    canonical_json,
    fail,
    file_sha256,
    manifest_digest,
    probe_pins,
    refuse_on_drift,
    require_clean_proj_environment,
    retrying,
)

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

CAPTURE_MANIFEST_NAME: Final = "capture-manifest.json"
CAPTURE_MARGIN_METERS: Final = 2_000
METERS_PER_DEGREE_LATITUDE: Final = 110_574.0
METERS_PER_DEGREE_LONGITUDE_AT_EQUATOR: Final = 111_320.0
TRANSFORM_DENSIFY_POINTS: Final = 64
TILE_SIZE: Final = 512
#: GDAL /vsicurl settings: the VRT names .tif tiles; never list remote directories.
VSICURL_OPTIONS: Final[dict[str, str]] = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".vrt,.tif",
    "GDAL_HTTP_MAX_RETRY": "4",
    "GDAL_HTTP_RETRY_DELAY": "5",
}
_RECEIPT_SUFFIX: Final = ".receipt.json"


def capture_file_name(pin: SourceFilePin) -> str:
    """`ocd_0-5cm_mean_homolosine.tif`: the native-window GeoTIFF for one pin (C8 `path`)."""
    return f"{pin.property_code}_{ISRIC_DEPTH_LABELS[pin.depth_interval]}_mean_homolosine.tif"


def capture_bounds_degrees(margin_meters: float = CAPTURE_MARGIN_METERS) -> tuple[float, float, float, float]:
    """The pinned lattice envelope widened by the margin, sized per axis at its widest (northern) edge."""
    latitude_margin = margin_meters / METERS_PER_DEGREE_LATITUDE
    northern_cosine = math.cos(math.radians(LATTICE_NORTH))
    longitude_margin = margin_meters / (METERS_PER_DEGREE_LONGITUDE_AT_EQUATOR * northern_cosine)
    return (
        LATTICE_WEST - longitude_margin,
        LATTICE_SOUTH - latitude_margin,
        LATTICE_EAST + longitude_margin,
        LATTICE_NORTH + latitude_margin,
    )


def native_window(dataset: Any, bounds: tuple[float, float, float, float]) -> Any:
    """The native-grid window covering `bounds` (EPSG:4326), rounded OUTWARD and clamped to the raster."""
    native = transform_bounds("EPSG:4326", dataset.crs, *bounds, densify_pts=TRANSFORM_DENSIFY_POINTS)
    window = from_bounds(*native, transform=dataset.transform)
    column = math.floor(window.col_off)
    row = math.floor(window.row_off)
    outward = Window(
        column,
        row,
        math.ceil(window.col_off + window.width) - column,
        math.ceil(window.row_off + window.height) - row,
    )
    return outward.intersection(Window(0, 0, dataset.width, dataset.height))


def window_record(dataset: Any, window: Any) -> dict[str, Any]:
    """The C8 `window` object: the native CRS and the integer window, plus the margin it was cut with."""
    return {
        "crs": dataset.crs.to_wkt(),
        "col_off": int(window.col_off),
        "row_off": int(window.row_off),
        "width": int(window.width),
        "height": int(window.height),
        "margin_m": CAPTURE_MARGIN_METERS,
    }


def write_window(dataset: Any, window: Any, destination: Path) -> None:
    """Read one native window and write it as a tiled, deflate-compressed GeoTIFF, atomically."""
    values = dataset.read(1, window=window)
    profile = {
        "driver": "GTiff",
        "dtype": values.dtype.name,
        "count": 1,
        "width": values.shape[1],
        "height": values.shape[0],
        "crs": dataset.crs,
        "transform": dataset.window_transform(window),
        "nodata": dataset.nodata,
        "compress": "deflate",
        "predictor": 2,
        "tiled": True,
        "blockxsize": TILE_SIZE,
        "blockysize": TILE_SIZE,
        "BIGTIFF": "IF_SAFER",
    }
    partial = destination.with_suffix(".partial.tif")
    with rasterio.open(partial, "w", **profile) as target:
        target.write(np.asarray(values), 1)
    partial.replace(destination)


def captured_grid(image: Path) -> dict[str, Any]:
    """The georeferenced grid one captured GeoTIFF sits on: native CRS, six-term transform and shape."""
    with rasterio.open(image) as dataset:
        return {
            "crs": dataset.crs.to_wkt(),
            "transform": [float(term) for term in tuple(dataset.transform)[:6]],
            "width": int(dataset.width),
            "height": int(dataset.height),
        }


def shared_grid(capture_dir: Path, entries: list[dict[str, Any]]) -> dict[str, Any]:
    """The one grid all captured windows share (C8 `window`), or refuse.

    Compared on the georeferenced grid, never on `col_off`/`row_off`: those are offsets into each
    VRT, and ISRIC's bdod and soc VRTs start three pixels west of the others, so one geographic
    window has two pixel offsets (see AGENTS.md, Capture).
    """
    grids = {canonical_json(captured_grid(capture_dir / entry["path"])) for entry in entries}
    if len(grids) != 1:
        raise fail("the thirty captured windows differ; the VRTs no longer share one grid", stage="capture")
    return {**json.loads(grids.pop()), "margin_m": CAPTURE_MARGIN_METERS}


def _receipt_path(capture_dir: Path, pin: SourceFilePin) -> Path:
    return capture_dir / f"{capture_file_name(pin)}{_RECEIPT_SUFFIX}"


def reusable_entry(capture_dir: Path, pin: SourceFilePin) -> dict[str, Any] | None:
    """A previously captured file's manifest entry, when its receipt matches its pin AND its bytes."""
    receipt_path = _receipt_path(capture_dir, pin)
    image = capture_dir / capture_file_name(pin)
    if not receipt_path.is_file() or not image.is_file():
        return None
    receipt: dict[str, Any] = json.loads(receipt_path.read_text(encoding="utf-8"))
    matches = (
        receipt.get("etag") == pin.etag
        and receipt.get("last_modified") == _instant(pin)
        and receipt.get("sha256") == file_sha256(image)
    )
    return receipt if matches else None


def _instant(pin: SourceFilePin) -> str:
    return pin.last_modified.strftime("%Y-%m-%dT%H:%M:%SZ")


def file_entry(pin: SourceFilePin, image: Path) -> dict[str, Any]:
    """The C8 `files[]` entry for one captured window."""
    return {
        "property": pin.property_code,
        "depth": ISRIC_DEPTH_LABELS[pin.depth_interval],
        "vrt_url": pin.vrt_url,
        "last_modified": _instant(pin),
        "etag": pin.etag,
        "path": image.name,
        "sha256": file_sha256(image),
        "bytes": image.stat().st_size,
    }


def build_manifest(entries: list[dict[str, Any]], window: dict[str, Any]) -> dict[str, Any]:
    """The C8 capture manifest, closed with `manifest_sha256` over its own canonical JSON."""
    newest = latest_pin()
    manifest: dict[str, Any] = {
        "release": SOURCE_RELEASE,
        "release_id": RELEASE_ID,
        "watermark": {
            "day": RELEASE_DAY.isoformat(),
            "instant": _instant(newest),
            "basis": f"max over {len(SOURCE_FILE_PINS)} files",
        },
        "window": window,
        "files": entries,
        "tooling": {
            "rasterio": str(rasterio.__version__),
            "gdal": str(rasterio.__gdal_version__),
            "proj_env_unset": True,
        },
    }
    manifest["manifest_sha256"] = manifest_digest(manifest)
    return manifest


def _capture_one(pin: SourceFilePin, capture_dir: Path, bounds: tuple[float, float, float, float]) -> dict[str, Any]:
    """Save one pin's native window and its receipt; returns the manifest entry plus the window record."""
    image = capture_dir / capture_file_name(pin)
    with rasterio.Env(**VSICURL_OPTIONS), rasterio.open(pin.vrt_url) as dataset:
        window = native_window(dataset, bounds)
        write_window(dataset, window, image)
        record = window_record(dataset, window)
    entry = {**file_entry(pin, image), "window": record}
    _receipt_path(capture_dir, pin).write_text(json.dumps(entry, sort_keys=True, indent=2), encoding="utf-8")
    return entry


def capture_release(options: argparse.Namespace, *, client: httpx.Client) -> dict[str, Any]:
    """Probe, refuse on drift, then capture every pin within the time budget; close the manifest when whole."""
    require_clean_proj_environment("capture")
    if options.capture_dir is None:
        raise fail("--capture-dir is required for capture", stage="capture", code="invalid_arguments")
    capture_dir: Path = options.capture_dir
    capture_dir.mkdir(parents=True, exist_ok=True)
    policy = RetryPolicy(options.retry_attempts, options.retry_base_seconds, options.retry_max_seconds)
    refuse_on_drift(probe_pins(client, policy), stage="capture")
    bounds = capture_bounds_degrees()
    started = time.monotonic()
    entries: list[dict[str, Any]] = []
    reused = 0
    for pin in SOURCE_FILE_PINS:
        existing = reusable_entry(capture_dir, pin)
        if existing is not None:
            entries.append(existing)
            reused += 1
            continue
        if time.monotonic() - started > options.time_budget_seconds:
            return {
                "verb": "capture",
                "status": "incomplete",
                "captured_files": len(entries),
                "expected_files": len(SOURCE_FILE_PINS),
                "detail": "time budget spent; rerun the same command to resume from the receipts",
            }
        capture_one = functools.partial(_capture_one, pin, capture_dir, bounds)
        entries.append(retrying(capture_one, policy, retryable=(RasterioIOError,)))
    files = [{key: value for key, value in entry.items() if key != "window"} for entry in entries]
    manifest = build_manifest(files, shared_grid(capture_dir, files))
    (capture_dir / CAPTURE_MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "verb": "capture",
        "status": "complete",
        "capture_dir": str(capture_dir),
        "files": len(entries),
        "reused_files": reused,
        "bytes": sum(int(entry["bytes"]) for entry in entries),
        "manifest_sha256": manifest["manifest_sha256"],
        "window": {key: value for key, value in manifest["window"].items() if key != "crs"},
    }


async def run_capture(options: argparse.Namespace) -> dict[str, Any]:
    """The `capture` verb: blocking network and GDAL work runs off the event loop."""

    def work() -> dict[str, Any]:
        with httpx.Client() as client:
            return capture_release(options, client=client)

    return await asyncio.to_thread(work)


def read_capture_manifest(capture_dir: Path, *, stage: str = "prepare") -> dict[str, Any]:
    """Load a closed capture manifest and prove its digest, its pins and every file's bytes (C8)."""
    path = capture_dir / CAPTURE_MANIFEST_NAME
    if not path.is_file():
        raise fail(f"{path} is missing; run `capture` first", stage=stage, code="capture_missing")
    manifest: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("manifest_sha256") != manifest_digest(manifest):
        raise fail(f"{path}: manifest_sha256 does not match its own canonical JSON", stage=stage)
    if manifest.get("release_id") != RELEASE_ID:
        raise fail(f"{path}: release {manifest.get('release_id')!r} is not {RELEASE_ID}", stage=stage)
    pinned = {(pin.property_code, ISRIC_DEPTH_LABELS[pin.depth_interval]): pin for pin in SOURCE_FILE_PINS}
    files = manifest.get("files", [])
    if {(entry["property"], entry["depth"]) for entry in files} != set(pinned) or len(files) != len(pinned):
        raise fail(f"{path}: the manifest does not name exactly the thirty pinned files", stage=stage)
    for entry in files:
        pin = pinned[(entry["property"], entry["depth"])]
        if entry["etag"] != pin.etag or entry["last_modified"] != _instant(pin):
            raise fail(f"{entry['path']}: captured from a file that differs from its pin", stage=stage)
        if file_sha256(capture_dir / entry["path"]) != entry["sha256"]:
            raise fail(f"{entry['path']}: bytes changed since capture (sha256 mismatch)", stage=stage)
    return manifest


__all__ = [
    "CAPTURE_MANIFEST_NAME",
    "CAPTURE_MARGIN_METERS",
    "build_manifest",
    "capture_bounds_degrees",
    "capture_file_name",
    "capture_release",
    "captured_grid",
    "native_window",
    "read_capture_manifest",
    "run_capture",
    "shared_grid",
]
