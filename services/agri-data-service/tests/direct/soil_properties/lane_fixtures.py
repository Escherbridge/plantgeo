"""Shared builders for the soil-properties pipeline tests: lane tables and small EPSG:4326 rasters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import rasterio  # type: ignore[import-untyped]
from rasterio.crs import CRS  # type: ignore[import-untyped]
from rasterio.transform import Affine  # type: ignore[import-untyped]

from agri_data_service.pipeline.direct.soil_properties.products import RELEASE_DAY
from agri_data_service.warehouse.parquet.schema import observed_stream_schema
from agri_data_service.warehouse.schemas.soil_properties import SOIL_PROPERTIES_STREAM, SOIL_PROPERTIES_VALUE_COLUMNS

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

MANIFEST_SHA256: Final = "a" * 64
NODATA: Final = -32768


def lane_table(
    cells: Sequence[tuple[float, float]],
    *,
    value: float = 100.0,
    overrides: Mapping[str, Sequence[float]] | None = None,
    manifest_sha256: str = MANIFEST_SHA256,
) -> pa.Table:
    """A z13 table in the lane's exact schema: one row per (origin longitude, origin latitude)."""
    rows = []
    for index, (longitude, latitude) in enumerate(cells):
        values: dict[str, Any] = dict.fromkeys(SOIL_PROPERTIES_VALUE_COLUMNS, value)
        for column, series in (overrides or {}).items():
            values[column] = series[index]
        rows.append(
            {
                "cell_longitude": longitude,
                "cell_latitude": latitude,
                **values,
                "source_release": "soilgrids-v2.0",
                "source_manifest_sha256": manifest_sha256,
                "release_day": RELEASE_DAY,
            }
        )
    return pa.Table.from_pylist(rows, schema=observed_stream_schema(SOIL_PROPERTIES_STREAM).arrow_schema)


def write_raster(path: Path, values: np.ndarray, *, west: float, north: float, pixel: float) -> Path:
    """A single-band int16 EPSG:4326 GeoTIFF with nodata -32768."""
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        dtype="int16",
        count=1,
        width=values.shape[1],
        height=values.shape[0],
        crs=CRS.from_epsg(4326),
        transform=Affine(pixel, 0.0, west, 0.0, -pixel, north),
        nodata=NODATA,
    ) as target:
        target.write(values.astype(np.int16), 1)
    return path


__all__ = ["MANIFEST_SHA256", "NODATA", "lane_table", "write_raster"]
