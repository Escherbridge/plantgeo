"""Annual USDA CDL area estimates over an equal-area grid; see crop_cover/AGENTS.md."""

from typing import Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, register_stream_schema
from agri_data_service.warehouse.parquet.tiers import (
    GeometrySimplification,
    TierDerivation,
    register_tier_derivation,
)

CROP_COVER_STREAM: Final = "crop-cover"
CROP_COVER_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=CROP_COVER_STREAM,
        arrow_schema=pa.schema(
            [
                pa.field("feature_id", pa.string(), nullable=False),
                pa.field("observed_year", pa.int16(), nullable=False),
                pa.field("release_day", pa.date32(), nullable=False),
                pa.field("source", pa.string(), nullable=False),
                pa.field("source_url", pa.string(), nullable=False),
                pa.field("source_resolution_m", pa.float64(), nullable=False),
                pa.field("analysis_resolution_m", pa.float64(), nullable=False),
                pa.field("aggregation_cell_m", pa.int32(), nullable=False),
                pa.field("grid_x", pa.int32(), nullable=False),
                pa.field("grid_y", pa.int32(), nullable=False),
                pa.field("estimation_method", pa.string(), nullable=False),
                pa.field("dominant_crop_code", pa.int16(), nullable=True),
                pa.field("dominant_crop_name", pa.string(), nullable=True),
                pa.field("crop_fraction", pa.float64(), nullable=False),
                pa.field("classified_fraction", pa.float64(), nullable=False),
                pa.field("crop_area_ha", pa.float64(), nullable=False),
                pa.field("cell_area_ha", pa.float64(), nullable=False),
                pa.field("class_areas_json", pa.string(), nullable=False),
                pa.field("class_names_json", pa.string(), nullable=False),
                pa.field("geometry_wkb", pa.binary(), nullable=False),
                pa.field("source_sha256", pa.string(), nullable=False),
                pa.field("ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
            ],
        ),
        sort_columns=("observed_year", "feature_id"),
    ),
)

# The writer supplies count-preserving aggregation; generic geometry repair retains all facts.
CROP_COVER_DERIVATION: Final = register_tier_derivation(
    TierDerivation(
        stream=CROP_COVER_STREAM,
        strategy=GeometrySimplification(geometry_column="geometry_wkb", min_area_tier_squares=None),
    ),
)
