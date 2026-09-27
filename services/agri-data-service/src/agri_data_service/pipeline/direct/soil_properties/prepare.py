"""`prepare`: sample each captured window at every 0.005-degree lattice cell centre and assemble the z13 table.

Nearest resampling picks the native Homolosine pixel CONTAINING each centre, so every stored number is one
ISRIC published. No database or object-store access. See `pipeline/direct/soil_properties/AGENTS.md`,
"Prepare".
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from typing import TYPE_CHECKING, Any, Final

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import rasterio  # type: ignore[import-untyped]
from rasterio.crs import CRS  # type: ignore[import-untyped]
from rasterio.transform import Affine  # type: ignore[import-untyped]
from rasterio.warp import Resampling, reproject  # type: ignore[import-untyped]

from agri_data_service.pipeline.direct.soil_properties.capture import read_capture_manifest
from agri_data_service.pipeline.direct.soil_properties.products import (
    CELL_CENTRE_OFFSET_DEGREES,
    ISRIC_DEPTH_LABELS,
    LATTICE_CELL_COUNT,
    LATTICE_COLUMNS,
    LATTICE_DEGREES,
    LATTICE_NORTH,
    LATTICE_ROWS,
    LATTICE_WEST,
    RELEASE_DAY,
    SOURCE_RELEASE,
)
from agri_data_service.pipeline.direct.soil_properties.source import fail, require_clean_proj_environment
from agri_data_service.warehouse.parquet.schema import observed_stream_schema
from agri_data_service.warehouse.schemas.soil_properties import (
    SOIL_PROPERTIES_STREAM,
    SOIL_PROPERTIES_VALUE_COLUMNS,
    soil_value_column,
)

if TYPE_CHECKING:
    import argparse
    from collections.abc import Mapping
    from pathlib import Path

    import numpy.typing as npt

PREPARED_DIRECTORY: Final = "prepared"
PREPARED_TABLE_NAME: Final = "soil-properties-z13.parquet"
PREPARE_REPORT_NAME: Final = "prepare-report.json"
#: Lattice coordinates are exact in thousandths of a degree: origin = (west + 5 * index) / 1000.
_THOUSANDTHS: Final = 1000
_PITCH_THOUSANDTHS: Final = round(LATTICE_DEGREES * _THOUSANDTHS)
_WEST_THOUSANDTHS: Final = round(LATTICE_WEST * _THOUSANDTHS)
_NORTH_THOUSANDTHS: Final = round(LATTICE_NORTH * _THOUSANDTHS)
#: The fill a warp leaves where no source pixel lands; outside int16, so it can never be a value.
MISSING_VALUE: Final = np.int32(np.iinfo(np.int32).min)
EARTH_RADIUS_METERS: Final = 6_371_008.8
PROBE_RADIUS_METERS: Final = 1_000
#: Points the report re-checks (DESIGN 2.6): Pullman had no cell at 0.0025 deg.
PROBE_POINTS: Final[Mapping[str, tuple[float, float]]] = {
    "boise": (-116.2, 43.6),
    "pullman": (-117.0, 46.73),
    "corvallis": (-123.26, 44.56),
}
_PROBE_REACH_CELLS: Final = 3


def lattice_transform() -> Any:
    """Affine of the 2,800 x 1,400 lattice: pixel (column, row) covers the cell whose SW origin it keys."""
    return Affine(LATTICE_DEGREES, 0.0, LATTICE_WEST, 0.0, -LATTICE_DEGREES, LATTICE_NORTH)


def sample_nearest(
    source: Path, *, destination_transform: Any, width: int, height: int, crs: Any = None
) -> npt.NDArray[np.int32]:
    """Nearest-resample one raster onto a grid: each cell takes the source pixel containing its centre.

    Cells no valid source pixel reaches hold `MISSING_VALUE`, as do cells that land on source nodata.
    """
    with rasterio.open(source) as dataset:
        values = dataset.read(1).astype(np.int32)
        nodata = dataset.nodata
        if nodata is not None:
            values[values == np.int32(nodata)] = MISSING_VALUE
        destination = np.full((height, width), MISSING_VALUE, dtype=np.int32)
        reproject(
            source=values,
            destination=destination,
            src_transform=dataset.transform,
            src_crs=dataset.crs,
            src_nodata=MISSING_VALUE,
            dst_transform=destination_transform,
            dst_crs=crs or CRS.from_epsg(4326),
            dst_nodata=MISSING_VALUE,
            resampling=Resampling.nearest,
            # Exact per-pixel transform: the approximate one can pick a neighbouring native pixel near an edge.
            tolerance=0,
        )
    return destination


def lattice_origins(rows: npt.NDArray[np.intp], columns: npt.NDArray[np.intp]) -> tuple[Any, Any]:
    """SW origins of lattice cells, exact to the thousandth: (west + 5c) / 1000 and (north - 5(r + 1)) / 1000."""
    longitudes = (_WEST_THOUSANDTHS + _PITCH_THOUSANDTHS * columns.astype(np.int64)) / _THOUSANDTHS
    latitudes = (_NORTH_THOUSANDTHS - _PITCH_THOUSANDTHS * (rows.astype(np.int64) + 1)) / _THOUSANDTHS
    return longitudes.astype(np.float64), latitudes.astype(np.float64)


def assemble_table(sampled: Mapping[str, npt.NDArray[np.int32]], *, manifest_sha256: str) -> pa.Table:
    """The z13 table: one row per cell holding ALL thirty values, latitude-major then longitude."""
    missing = [column for column in SOIL_PROPERTIES_VALUE_COLUMNS if column not in sampled]
    if missing:
        raise fail(f"prepare has no sample for {missing}", stage="prepare")
    valid = np.ones(next(iter(sampled.values())).shape, dtype=bool)
    for column in SOIL_PROPERTIES_VALUE_COLUMNS:
        valid &= sampled[column] != MISSING_VALUE
    rows, columns = np.nonzero(valid)
    longitudes, latitudes = lattice_origins(rows, columns)
    order = np.lexsort((longitudes, latitudes))
    count = int(order.size)
    values = [
        pa.array(sampled[column][rows, columns][order].astype(np.float64), type=pa.float64())
        for column in SOIL_PROPERTIES_VALUE_COLUMNS
    ]
    return pa.Table.from_arrays(
        [
            pa.array(longitudes[order], type=pa.float64()),
            pa.array(latitudes[order], type=pa.float64()),
            *values,
            pa.repeat(pa.scalar(SOURCE_RELEASE, type=pa.string()), count),
            pa.repeat(pa.scalar(manifest_sha256, type=pa.string()), count),
            pa.repeat(pa.scalar(RELEASE_DAY, type=pa.date32()), count),
        ],
        schema=observed_stream_schema(SOIL_PROPERTIES_STREAM).arrow_schema,
    )


def _haversine_meters(longitude_a: float, latitude_a: float, longitude_b: float, latitude_b: float) -> float:
    """Haversine distance on a 6,371,008.8 m sphere (CONTRACT C2)."""
    phi_a, phi_b = math.radians(latitude_a), math.radians(latitude_b)
    chord = (
        math.sin((phi_b - phi_a) / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(math.radians(longitude_b - longitude_a) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_METERS * math.asin(min(1.0, math.sqrt(chord)))


def probe_point(valid: npt.NDArray[np.bool_], longitude: float, latitude: float) -> dict[str, Any]:
    """Whether a valid cell centre lies within 1,000 m of a point, and the nearest one's distance."""
    column = math.floor((longitude - LATTICE_WEST) / LATTICE_DEGREES)
    row = math.floor((LATTICE_NORTH - latitude) / LATTICE_DEGREES)
    best: float | None = None
    for row_index in range(row - _PROBE_REACH_CELLS, row + _PROBE_REACH_CELLS + 1):
        for column_index in range(column - _PROBE_REACH_CELLS, column + _PROBE_REACH_CELLS + 1):
            inside = 0 <= row_index < valid.shape[0] and 0 <= column_index < valid.shape[1]
            if not inside or not valid[row_index, column_index]:
                continue
            centre_longitude = LATTICE_WEST + column_index * LATTICE_DEGREES + CELL_CENTRE_OFFSET_DEGREES
            centre_latitude = LATTICE_NORTH - (row_index + 1) * LATTICE_DEGREES + CELL_CENTRE_OFFSET_DEGREES
            distance = _haversine_meters(longitude, latitude, centre_longitude, centre_latitude)
            best = distance if best is None else min(best, distance)
    within = best is not None and best <= PROBE_RADIUS_METERS
    return {"cell_within_1000_m": within, "nearest_centre_m": None if best is None else round(best)}


def prepare_release(options: argparse.Namespace) -> dict[str, Any]:
    """Verify the capture, sample all thirty windows onto the lattice, and write the prepared table."""
    require_clean_proj_environment("prepare")
    if options.capture_dir is None:
        raise fail("--capture-dir is required for prepare", stage="prepare", code="invalid_arguments")
    capture_dir: Path = options.capture_dir
    started = time.monotonic()
    manifest = read_capture_manifest(capture_dir)
    transform = lattice_transform()
    sampled: dict[str, npt.NDArray[np.int32]] = {}
    depth_by_label = {label: interval for interval, label in ISRIC_DEPTH_LABELS.items()}
    for entry in manifest["files"]:
        column = soil_value_column(entry["property"], depth_by_label[entry["depth"]])
        sampled[column] = sample_nearest(
            capture_dir / entry["path"], destination_transform=transform, width=LATTICE_COLUMNS, height=LATTICE_ROWS
        )
    table = assemble_table(sampled, manifest_sha256=manifest["manifest_sha256"])
    valid = np.ones((LATTICE_ROWS, LATTICE_COLUMNS), dtype=bool)
    for values in sampled.values():
        valid &= values != MISSING_VALUE
    output = capture_dir / PREPARED_DIRECTORY
    output.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, output / PREPARED_TABLE_NAME, compression="zstd")
    report = {
        "verb": "prepare",
        "status": "complete",
        "rows": table.num_rows,
        "lattice_cells": LATTICE_CELL_COUNT,
        "valid_fraction": round(table.num_rows / LATTICE_CELL_COUNT, 6),
        "manifest_sha256": manifest["manifest_sha256"],
        "prepared_table": str(output / PREPARED_TABLE_NAME),
        "prepared_bytes": (output / PREPARED_TABLE_NAME).stat().st_size,
        "column_ranges": {
            column: [int(sampled[column][valid].min()), int(sampled[column][valid].max())]
            for column in SOIL_PROPERTIES_VALUE_COLUMNS
            if valid.any()
        },
        "probe_points": {name: probe_point(valid, *point) for name, point in PROBE_POINTS.items()},
        "wall_seconds": round(time.monotonic() - started, 1),
    }
    (output / PREPARE_REPORT_NAME).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


async def run_prepare(options: argparse.Namespace) -> dict[str, Any]:
    """The `prepare` verb: CPU-bound GDAL work runs off the event loop."""
    return await asyncio.to_thread(prepare_release, options)


def read_prepared_table(capture_dir: Path) -> pa.Table:
    """Load the prepared z13 table written by `prepare`."""
    path = capture_dir / PREPARED_DIRECTORY / PREPARED_TABLE_NAME
    if not path.is_file():
        raise fail(f"{path} is missing; run `prepare` first", stage="publish", code="prepared_table_missing")
    return pq.read_table(path)


__all__ = [
    "MISSING_VALUE",
    "PREPARED_DIRECTORY",
    "PREPARED_TABLE_NAME",
    "PREPARE_REPORT_NAME",
    "assemble_table",
    "lattice_origins",
    "lattice_transform",
    "prepare_release",
    "probe_point",
    "read_prepared_table",
    "run_prepare",
    "sample_nearest",
]
