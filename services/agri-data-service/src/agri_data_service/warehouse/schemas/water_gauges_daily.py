"""Parquet schema for `water-gauges-daily`: USGS Water Data daily mean discharge, one row per gauge-day.

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface, and never a
sibling schema module. Written by the config lane `lanes/water-gauges-daily.toml`
(`pipeline/lanes/water_gauges/usgs_water_data.py`); see `pipeline/lanes/water_gauges/AGENTS.md` for
why each column is shaped as it is and `docs/lanes/water-gauges.md` for the source contract.
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

WATER_GAUGES_DAILY_STREAM: Final = "water-gauges-daily"

#: The spec §7a identity `monitoring_location_id:time:statistic_id`, as the base rung's grain.
WATER_GAUGES_DAILY_GRAIN: Final[tuple[str, ...]] = ("monitoring_location_id", "source_time", "statistic_id")

#: The literal every row carries in `source` (the legacy stream's is "USGS NWIS").
WATER_GAUGES_DAILY_SOURCE: Final = "USGS Water Data"

WATER_GAUGES_DAILY_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=WATER_GAUGES_DAILY_STREAM,
        arrow_schema=pa.schema(
            [
                # The first fourteen columns repeat the legacy `water-gauges` stream's names, types and
                # nullability, so the web reader decodes both streams into one row shape
                # (AGENTS.md "Web-compatible columns").
                # The USGS site number: `monitoring_location_id` without its `USGS-` agency prefix.
                pa.field("site_number", pa.string(), nullable=True),
                # The named day stamped at the site's STANDARD-time midnight (the legacy daily-values
                # convention, `ingest/usgs_nwis.py::parse_daily_value_series`); never read to derive a day.
                pa.field("observed_at", pa.timestamp("us", tz="UTC"), nullable=False),
                # `publisher_named_day(time)`: the served `time` string's first ten characters.
                pa.field("observed_day", pa.date32(), nullable=False),
                # From the monitoring-locations collection; null when the tile's answer lacks the site.
                pa.field("site_name", pa.string(), nullable=True),
                pa.field("latitude", pa.float64(), nullable=True),
                pa.field("longitude", pa.float64(), nullable=True),
                # Daily mean discharge, ft^3/s. A served null or the -999999 sentinel drops the row.
                pa.field("flow_cfs", pa.float64(), nullable=True),
                # No producer supplies these three; kept null so the legacy row shape holds.
                pa.field("percentile", pa.float64(), nullable=True),
                pa.field("condition", pa.string(), nullable=True),
                pa.field("trend", pa.string(), nullable=True),
                pa.field("source", pa.string(), nullable=False),
                # Always false: the config lane writes Parquet directly and links no geometry dimension.
                pa.field("geometry_linked", pa.bool_(), nullable=False),
                pa.field("data_available_at", pa.timestamp("us", tz="UTC"), nullable=True),
                # The retrieval instant of the tile answer that carried the row.
                pa.field("ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
                # Daily-values columns the legacy stream never had.
                pa.field("monitoring_location_id", pa.string(), nullable=True),
                # The daily value's `time` exactly as served (a date string, P4).
                pa.field("source_time", pa.string(), nullable=False),
                pa.field("statistic_id", pa.string(), nullable=False),
                # "Approved" or "Provisional", verbatim; a change rewrites the day (S11).
                pa.field("approval_status", pa.string(), nullable=False),
                # The served qualifier list joined with ",", or null when none.
                pa.field("qualifier", pa.string(), nullable=True),
                pa.field("time_series_id", pa.string(), nullable=True),
            ]
        ),
        sort_columns=WATER_GAUGES_DAILY_GRAIN,
    )
)

WATER_GAUGES_DAILY_TIER_DERIVATION: Final = register_tier_derivation(
    TierDerivation(
        stream=WATER_GAUGES_DAILY_STREAM,
        strategy=GridAggregation(
            longitude_column="longitude",
            latitude_column="latitude",
            # The day is the only time grain a coarse rung can hold; a gauge identity keys nothing.
            key_columns=("observed_day",),
            aggregations=(
                ColumnAggregation("site_number", "null"),
                ColumnAggregation("site_name", "null"),
                ColumnAggregation("monitoring_location_id", "null"),
                ColumnAggregation("time_series_id", "null"),
                ColumnAggregation("observed_at", "max"),
                ColumnAggregation("flow_cfs", "mean"),
                ColumnAggregation("percentile", "null"),
                ColumnAggregation("condition", "null"),
                ColumnAggregation("trend", "null"),
                ColumnAggregation("qualifier", "null"),
                ColumnAggregation("source", "first"),
                ColumnAggregation("statistic_id", "first"),
                ColumnAggregation("source_time", "max"),
                # "Provisional" sorts after "Approved": a cell is provisional when any member is.
                ColumnAggregation("approval_status", "max"),
                ColumnAggregation("geometry_linked", "all"),
                ColumnAggregation("data_available_at", "max"),
                ColumnAggregation("ingested_at", "max"),
            ),
        ),
        base_non_null_columns=("site_number", "monitoring_location_id"),
    )
)
