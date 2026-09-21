"""Count classified equal-area pixels and preserve class areas at every map rung."""

from __future__ import annotations

import json
import struct
from collections import defaultdict
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import rasterio  # type: ignore[import-untyped]
from rasterio.warp import transform  # type: ignore[import-untyped]

from agri_data_service.pipeline.direct.crop_cover.products import (
    BASE_CELL_METRES,
    CROP_CODES,
    GRID_METRES,
    NO_DATA_CODES,
)
from agri_data_service.pipeline.direct.crop_cover.source import fail, read_manifest, sha256, verify_tiff
from agri_data_service.warehouse.schemas.crop_cover import CROP_COVER_SCHEMA

if TYPE_CHECKING:
    from pathlib import Path

    from numpy.typing import NDArray

HECTARE_M2 = 10000
CLASS_COUNT = 256
PIXEL_COUNT_TOLERANCE = 1e-5
type CellCoordinates = tuple[int, int]
type PixelCounts = NDArray[np.uint32]


def _pixel_counts(path: Path, manifest: dict[str, Any]) -> dict[CellCoordinates, PixelCounts]:
    """Count every exported classified pixel once; no random samples or polygon claims."""
    counts: dict[CellCoordinates, PixelCounts] = {}
    resolution = manifest["analysis_resolution_m"]
    block_size = BASE_CELL_METRES // resolution
    named_codes = {int(code) for code in manifest["class_names"]} | NO_DATA_CODES
    for tile in manifest["tiles"]:
        tile_path = path.parent / tile["file"]
        verify_tiff(tile_path, tuple(tile["bounds"]), resolution)
        west, _south, _east, north = tile["bounds"]
        with rasterio.open(tile_path) as raster:
            pixels = raster.read(1)
            unknown = set(map(int, np.unique(pixels))) - named_codes
            if unknown:
                raise fail(f"Source contains unlabelled class codes {sorted(unknown)}")
            for row in range(0, raster.height, block_size):
                for column in range(0, raster.width, block_size):
                    histogram = np.bincount(
                        pixels[row : row + block_size, column : column + block_size].ravel(),
                        minlength=CLASS_COUNT,
                    ).astype(np.uint32)
                    histogram[list(NO_DATA_CODES)] = 0
                    if not np.any(histogram):
                        continue
                    grid_x = (west + column * resolution) // BASE_CELL_METRES
                    grid_y = (north - (row + block_size) * resolution) // BASE_CELL_METRES
                    if (grid_x, grid_y) in counts:
                        raise fail("Capture tiles overlap a grid cell")
                    counts[grid_x, grid_y] = histogram
    if not counts:
        raise fail("A source without classified pixels is unavailable, never a fabricated empty annual release")
    return counts


def _geometries(coordinates: list[CellCoordinates], cell_m: int) -> list[bytes]:
    """Transform projected grid polygons together and encode ordinary EPSG:4326 WKB."""
    corners = ((0, 0), (0.5, 0), (1, 0), (1, 0.5), (1, 1), (0.5, 1), (0, 1), (0, 0.5), (0, 0))
    projected = [((x + dx) * cell_m, (y + dy) * cell_m) for x, y in coordinates for dx, dy in corners]
    longitude, latitude = transform("EPSG:5070", "EPSG:4326", *zip(*projected, strict=True))
    vertices = len(corners)
    header = struct.pack("<BIII", 1, 3, 1, vertices)
    return [
        header
        + b"".join(struct.pack("<dd", longitude[index], latitude[index]) for index in range(start, start + vertices))
        for start in range(0, len(projected), vertices)
    ]


def _table(
    counts: dict[CellCoordinates, PixelCounts],
    manifest: dict[str, Any],
    *,
    cell_m: int,
    manifest_sha: str,
) -> pa.Table:
    coordinates = sorted(counts)
    geometry = _geometries(coordinates, cell_m)
    rows: list[dict[str, Any]] = []
    pixel_ha = manifest["analysis_resolution_m"] ** 2 / HECTARE_M2
    area_ha = cell_m**2 / HECTARE_M2
    names = manifest["class_names"]
    crop_codes = sorted(CROP_CODES & {int(code) for code in names})
    for (x, y), wkb in zip(coordinates, geometry, strict=True):
        histogram = counts[x, y]
        areas = {str(code): round(int(histogram[code]) * pixel_ha, 6) for code in np.flatnonzero(histogram)}
        crop_count = sum(int(histogram[code]) for code in crop_codes)
        dominant = max(crop_codes, key=lambda code: int(histogram[code])) if crop_count else None
        rows.append(
            {
                "feature_id": f"usda-cdl:{manifest['observed_year']}:{cell_m}:{x}:{y}",
                "observed_year": manifest["observed_year"],
                "release_day": date.fromisoformat(manifest["release_day"]),
                "source": "usda-cdl",
                "source_url": manifest["metadata_url"],
                "source_resolution_m": manifest["source_resolution_m"],
                "analysis_resolution_m": manifest["analysis_resolution_m"],
                "aggregation_cell_m": cell_m,
                "grid_x": x,
                "grid_y": y,
                "estimation_method": "classified_pixel_area"
                if manifest["source_resolution_m"] == manifest["analysis_resolution_m"]
                else "nearest_neighbor_resampled_pixel_area",
                "dominant_crop_code": dominant,
                "dominant_crop_name": names[str(dominant)] if dominant is not None else None,
                "crop_fraction": crop_count * pixel_ha / area_ha,
                "classified_fraction": int(histogram.sum()) * pixel_ha / area_ha,
                "crop_area_ha": round(crop_count * pixel_ha, 6),
                "cell_area_ha": area_ha,
                "class_areas_json": json.dumps(areas, sort_keys=True, separators=(",", ":")),
                "class_names_json": json.dumps(
                    {code: names[code] for code in areas}, sort_keys=True, separators=(",", ":")
                ),
                "geometry_wkb": wkb,
                "source_sha256": manifest_sha,
                "ingested_at": datetime.fromisoformat(manifest["captured_at"]),
            }
        )
    return pa.Table.from_pylist(rows, schema=CROP_COVER_SCHEMA.arrow_schema)


def build_tables(manifest_path: Path) -> dict[int, pa.Table]:
    """Replay immutable source receipts and aggregate integer counts independently for all four rungs."""
    manifest = read_manifest(manifest_path)
    manifest_sha = sha256(manifest_path.read_bytes())
    base = _pixel_counts(manifest_path, manifest)
    tables: dict[int, pa.Table] = {}
    for tier, cell_m in GRID_METRES.items():
        ratio = cell_m // BASE_CELL_METRES
        grouped: dict[CellCoordinates, PixelCounts] = defaultdict(lambda: np.zeros(CLASS_COUNT, dtype=np.uint32))
        for (x, y), histogram in base.items():
            grouped[x // ratio, y // ratio] += histogram
        tables[tier] = _table(grouped, manifest, cell_m=cell_m, manifest_sha=manifest_sha)
    return tables


def derive_table(base: pa.Table, tier: int) -> pa.Table:
    """Rebuild an exact coarser grid from persisted integer-equivalent class areas."""
    if tier not in GRID_METRES or not base.num_rows:
        raise fail("Crop derivation needs a supported rung and nonempty base data")
    source_rows = base.to_pylist()
    first = source_rows[0]
    cell_m = GRID_METRES[tier]
    manifest: dict[str, Any] = {
        "observed_year": first["observed_year"],
        "release_day": first["release_day"].isoformat(),
        "metadata_url": first["source_url"],
        "source_resolution_m": first["source_resolution_m"],
        "analysis_resolution_m": first["analysis_resolution_m"],
        "captured_at": first["ingested_at"].isoformat(),
        "class_names": {},
    }
    pixel_ha = first["analysis_resolution_m"] ** 2 / HECTARE_M2
    grouped: dict[CellCoordinates, PixelCounts] = defaultdict(lambda: np.zeros(CLASS_COUNT, dtype=np.uint32))
    identities = ("observed_year", "release_day", "source_sha256", "source_resolution_m", "analysis_resolution_m")
    for row in source_rows:
        if any(row[key] != first[key] for key in identities):
            raise fail("A coarse crop rung cannot mix annual sources or capture generations")
        source_cell_m = row["aggregation_cell_m"]
        if cell_m < source_cell_m or cell_m % source_cell_m:
            raise fail("A coarse crop cell must contain whole source cells")
        ratio = cell_m // source_cell_m
        key = row["grid_x"] // ratio, row["grid_y"] // ratio
        for code, area in json.loads(row["class_areas_json"]).items():
            count = area / pixel_ha
            if abs(count - round(count)) > PIXEL_COUNT_TOLERANCE:
                raise fail("Persisted class area is not an integer classified pixel count")
            grouped[key][int(code)] += round(count)
        manifest["class_names"].update(json.loads(row["class_names_json"]))
    return _table(grouped, manifest, cell_m=cell_m, manifest_sha=first["source_sha256"])
