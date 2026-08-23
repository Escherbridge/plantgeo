"""Parquet schema for the `vegetation` lane: governed Sentinel-2 L2A NDVI cell-day means.

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.
See `docs/lanes/vegetation.md` for the source system, cadence, and grain evidence this schema is
built from.
"""

from __future__ import annotations

from typing import Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, register_stream_schema

# --- The vegetation (Sentinel-2 NDVI) plane ----------------------------------------------------
# Source: `agri.forecast_observation` (db/agri/tables/forecast_observation.sql), reached through
# `agri.forecast_series` (db/agri/tables/forecast_series.sql) -- the governed cell-day NDVI plane
# `docs/lanes/vegetation.md` section 4 describes, and explicitly NOT `agri.signal_observation`:
# an audit that checks the wrong table falsely reports this lane dead
# (`docs/lanes/vegetation.md:136-144`). This lane registers exactly one series per spatial cell for
# its fixed `metric_name = 'ndvi'` and `source_transform_version =
# 'sentinel2-ndvi-daily-cell-mean-v1'` (`execution/vegetation_ndvi_plane.py:40,49-52`), so
# `(cell_id, observed_day)` alone is the true grain -- `grid_name`/`metric_name`/`metric_unit` are
# constant, self-describing attributes of this lane's single grid and single metric, not
# discriminators. See `pipeline/vegetation_day_export.sql` for the release-dedup this schema's
# values are already resolved through.
#
# `observation_checksum` and `data_available_at` are carried straight from
# `agri.forecast_observation` (columns of that name, `forecast_observation.sql:16,19`) rather than
# invented: the checksum is this lane's own per-row governed fingerprint, and the availability
# timestamp is what a future leakage-honest forecast lane must gate on.

VEGETATION_PLANE_STREAM: Final = "vegetation"

VEGETATION_PLANE_GRAIN: Final[tuple[str, ...]] = (
    "cell_id",
    "observed_day",
)

VEGETATION_PLANE_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=VEGETATION_PLANE_STREAM,
        arrow_schema=pa.schema(
            [
                pa.field("cell_id", pa.string(), nullable=False),
                pa.field("grid_name", pa.string(), nullable=False),
                pa.field("metric_name", pa.string(), nullable=False),
                pa.field("metric_unit", pa.string(), nullable=False),
                pa.field("observed_day", pa.date32(), nullable=False),
                pa.field("metric_value", pa.float64(), nullable=False),
                pa.field("observation_checksum", pa.string(), nullable=False),
                pa.field("data_available_at", pa.timestamp("us", tz="UTC"), nullable=False),
                pa.field("release_count", pa.int64(), nullable=False),
                pa.field("allowed_client_exposure", pa.bool_(), nullable=False),
            ]
        ),
        sort_columns=VEGETATION_PLANE_GRAIN,
    )
)
