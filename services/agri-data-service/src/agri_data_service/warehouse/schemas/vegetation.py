"""Parquet schema for the `vegetation` lane: governed Sentinel-2 L2A NDVI cell-day means.

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.
See `docs/lanes/vegetation.md` for the source system, cadence, and grain evidence this schema is
built from.
"""

from __future__ import annotations

from typing import Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, register_stream_schema
from agri_data_service.warehouse.parquet.tiers import (
    ColumnAggregation,
    GridAggregation,
    TierDerivation,
    register_tier_derivation,
)

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
                # NULLABLE because the coarse rungs null it:
                # a coarse cell spans many source cells and can honestly name none of them
                # (see this module's TierDerivation). The base z13 rung always carries it.
                pa.field("cell_id", pa.string(), nullable=True),
                pa.field("grid_name", pa.string(), nullable=False),
                pa.field("metric_name", pa.string(), nullable=False),
                pa.field("metric_unit", pa.string(), nullable=False),
                pa.field("observed_day", pa.date32(), nullable=False),
                pa.field("metric_value", pa.float64(), nullable=False),
                # NULLABLE because the coarse rungs null it:
                # a checksum identifies ONE observation's payload; a merged cell has no single payload to hash
                # (see this module's TierDerivation). The base z13 rung always carries it.
                pa.field("observation_checksum", pa.string(), nullable=True),
                pa.field("data_available_at", pa.timestamp("us", tz="UTC"), nullable=False),
                pa.field("release_count", pa.int64(), nullable=False),
                pa.field("allowed_client_exposure", pa.bool_(), nullable=False),
                # Cell position from `agri.spatial_cell.centroid`, the representative point of the
                # Sentinel-2 L2A grid this cell belongs to (agri/tables/spatial_cell.sql). Populated
                # on 1,965 of 1,965 production rows (enrichment reference, measured 2026-08-23).
                # Holds the cell ORIGIN's centroid, not any single observation's location.
                pa.field("cell_longitude", pa.float64(), nullable=False),
                pa.field("cell_latitude", pa.float64(), nullable=False),
            ]
        ),
        sort_columns=VEGETATION_PLANE_GRAIN,
    )
)

VEGETATION_PLANE_TIER_DERIVATION: Final = register_tier_derivation(
    TierDerivation(
        stream=VEGETATION_PLANE_STREAM,
        strategy=GridAggregation(
            longitude_column="cell_longitude",
            latitude_column="cell_latitude",
            # Coarse grain: one row per day per coarsened grid cell. The four descriptive columns
            # (grid_name, metric_name, metric_unit, allowed_client_exposure) are constant across
            # the whole lane (all sentinel2-ndvi-0p25deg, all ndvi, all unitless, all exposed) so
            # 'first' honestly picks the one value they carry. cell_id and observation_checksum are
            # unique to one base cell and no merged row can honestly name one.
            key_columns=("observed_day",),
            aggregations=(
                ColumnAggregation("cell_id", "null"),  # unique to one base cell, no honest merge
                ColumnAggregation("grid_name", "first"),  # constant: sentinel2-ndvi-0p25deg
                ColumnAggregation("metric_name", "first"),  # constant: ndvi
                ColumnAggregation("metric_unit", "first"),  # constant: unitless
                ColumnAggregation("metric_value", "mean"),  # intensive measurement: NDVI average
                ColumnAggregation("observation_checksum", "null"),  # unique to one governed observation
                ColumnAggregation("data_available_at", "max"),  # newest availability across cells
                ColumnAggregation("release_count", "sum"),  # additive: total releases in merged cells
                ColumnAggregation("allowed_client_exposure", "all"),  # gate: coarse cell exposed only if all were
            ),
        ),
    )
)
