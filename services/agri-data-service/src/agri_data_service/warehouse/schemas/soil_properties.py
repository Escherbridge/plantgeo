"""Parquet schema for the `soil-properties` lane: ISRIC SoilGrids v2.0 mapped values on a 0.005-degree lattice.

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.

One row per lattice cell, keyed by the cell's south-west ORIGIN; values are the published SoilGrids
model estimates, never measurements. Rationale (float64 values, the all-thirty row rule, unbanded
`mean` rungs): see `pipeline/direct/soil_properties/AGENTS.md`.
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

SOIL_PROPERTIES_STREAM: Final = "soil-properties"

#: ISRIC property codes, in the order their value columns are laid out.
SOIL_PROPERTY_CODES: Final[tuple[str, ...]] = (
    "phh2o",
    "soc",
    "nitrogen",
    "bdod",
    "cec",
    "ocd",
    "clay",
    "sand",
    "silt",
    "cfvo",
)

#: Depth intervals as column suffix parts (`0_5` -> `<p>_0_5cm`); ISRIC spells them `0-5cm`.
SOIL_DEPTH_INTERVALS: Final[tuple[str, ...]] = ("0_5", "5_15", "15_30")

SOIL_PROPERTIES_KEY_COLUMNS: Final[tuple[str, ...]] = ("cell_longitude", "cell_latitude")
SOIL_PROPERTIES_CONSTANT_COLUMNS: Final[tuple[str, ...]] = ("source_release", "source_manifest_sha256", "release_day")


def soil_value_column(property_code: str, depth_interval: str) -> str:
    """Name one value column: `clay` at `15_30` is `clay_15_30cm`."""
    if property_code not in SOIL_PROPERTY_CODES or depth_interval not in SOIL_DEPTH_INTERVALS:
        raise ValueError(f"no soil-properties column for property {property_code!r} at depth {depth_interval!r}")
    return f"{property_code}_{depth_interval}cm"


#: The thirty value columns, property-major then depth.
SOIL_PROPERTIES_VALUE_COLUMNS: Final[tuple[str, ...]] = tuple(
    soil_value_column(property_code, depth_interval)
    for property_code in SOIL_PROPERTY_CODES
    for depth_interval in SOIL_DEPTH_INTERVALS
)

SOIL_PROPERTIES_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=SOIL_PROPERTIES_STREAM,
        arrow_schema=pa.schema(
            [
                pa.field("cell_longitude", pa.float64(), nullable=False),
                pa.field("cell_latitude", pa.float64(), nullable=False),
                *(pa.field(column, pa.float64(), nullable=False) for column in SOIL_PROPERTIES_VALUE_COLUMNS),
                pa.field("source_release", pa.string(), nullable=False),
                pa.field("source_manifest_sha256", pa.string(), nullable=False),
                pa.field("release_day", pa.date32(), nullable=False),
            ],
        ),
        # Latitude-major, so row-group latitude statistics prune a point read.
        sort_columns=("cell_latitude", "cell_longitude"),
    ),
)

# Grid re-flooring with no non-spatial key: every value is an unweighted mean of its valid base cells.
SOIL_PROPERTIES_DERIVATION: Final = register_tier_derivation(
    TierDerivation(
        stream=SOIL_PROPERTIES_STREAM,
        strategy=GridAggregation(
            longitude_column="cell_longitude",
            latitude_column="cell_latitude",
            key_columns=(),
            aggregations=(
                *(ColumnAggregation(column, "mean") for column in SOIL_PROPERTIES_VALUE_COLUMNS),
                *(ColumnAggregation(column, "first") for column in SOIL_PROPERTIES_CONSTANT_COLUMNS),
            ),
        ),
    ),
)

__all__ = [
    "SOIL_DEPTH_INTERVALS",
    "SOIL_PROPERTIES_CONSTANT_COLUMNS",
    "SOIL_PROPERTIES_DERIVATION",
    "SOIL_PROPERTIES_KEY_COLUMNS",
    "SOIL_PROPERTIES_SCHEMA",
    "SOIL_PROPERTIES_STREAM",
    "SOIL_PROPERTIES_VALUE_COLUMNS",
    "SOIL_PROPERTY_CODES",
    "soil_value_column",
]
