"""SSURGO native delineation schema and geometry tiers; see `warehouse/schemas/AGENTS.md`, "soil_survey.py".

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.
"""

from __future__ import annotations

from typing import Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, register_stream_schema
from agri_data_service.warehouse.parquet.tiers import (
    GeometrySimplification,
    TierDerivation,
    register_tier_derivation,
)

SOIL_SURVEY_STREAM: Final = "soil-survey"
#: One row per SSURGO delineation: `mupolygonkey`, never `mukey`.
SOIL_SURVEY_GRAIN: Final[tuple[str, ...]] = ("mupolygonkey",)

SOIL_SURVEY_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=SOIL_SURVEY_STREAM,
        arrow_schema=pa.schema(
            [
                pa.field("natural_key", pa.string(), nullable=False),
                pa.field("mupolygonkey", pa.string(), nullable=False),
                pa.field("mukey", pa.string(), nullable=False),
                pa.field("map_unit_name", pa.string(), nullable=True),
                pa.field("soil_series", pa.string(), nullable=True),
                pa.field("drainage_class", pa.string(), nullable=True),
                # Tri-state: an unranked hydric rating stays null, never false.
                pa.field("hydric_rating", pa.bool_(), nullable=True),
                pa.field("land_capability_class", pa.string(), nullable=True),
                pa.field("survey_area_symbol", pa.string(), nullable=True),
                pa.field("survey_area_vintage", pa.timestamp("us", tz="UTC"), nullable=False),
                pa.field("geometry_id", pa.string(), nullable=True),
                pa.field("last_confirmed_at", pa.timestamp("us", tz="UTC"), nullable=False),
                pa.field("release_day", pa.date32(), nullable=False),
                pa.field("geometry_wkb", pa.binary(), nullable=False),
                pa.field("producer", pa.string(), nullable=False),
                pa.field("bbox_west", pa.float64(), nullable=True),
                pa.field("bbox_south", pa.float64(), nullable=True),
                pa.field("bbox_east", pa.float64(), nullable=True),
                pa.field("bbox_north", pa.float64(), nullable=True),
                # One of valid, repaired or invalid_unrepaired (owner Q4); null only on pre-port rows.
                pa.field("geometry_quality", pa.string(), nullable=True),
            ]
        ),
        sort_columns=SOIL_SURVEY_GRAIN,
    )
)

# Simplify only: no dissolve, no area floor (it empties z0); see AGENTS.md.
SOIL_SURVEY_DERIVATION: Final = register_tier_derivation(
    TierDerivation(
        stream=SOIL_SURVEY_STREAM,
        strategy=GeometrySimplification(geometry_column="geometry_wkb", min_area_tier_squares=None),
    )
)
