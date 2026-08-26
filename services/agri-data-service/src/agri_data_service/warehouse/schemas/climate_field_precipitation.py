"""Dedicated precipitation Parquet schema and zoom-ladder derivation."""

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

CLIMATE_FIELD_PRECIPITATION_STREAM: Final = "climate-field-precipitation"

CLIMATE_FIELD_PRECIPITATION_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=CLIMATE_FIELD_PRECIPITATION_STREAM,
        arrow_schema=pa.schema(
            [
                pa.field("support_key", pa.string(), nullable=False),
                pa.field("signal_name", pa.string(), nullable=False),
                pa.field("normalized_unit", pa.string(), nullable=False),
                pa.field("cell_id", pa.string(), nullable=True),
                pa.field("observed_day", pa.date32(), nullable=False),
                pa.field("normalized_value", pa.float64(), nullable=False),
                pa.field("observation_count", pa.int64(), nullable=False),
                pa.field("newest_observed_at", pa.timestamp("us", tz="UTC"), nullable=False),
                pa.field("coverage_fraction", pa.float64(), nullable=True),
                pa.field("allowed_client_exposure", pa.bool_(), nullable=True),
                pa.field("cell_longitude", pa.float64(), nullable=False),
                pa.field("cell_latitude", pa.float64(), nullable=False),
                pa.field("source_key", pa.string(), nullable=False),
                pa.field("source_parameter", pa.string(), nullable=False),
                pa.field("source_snapshot_id", pa.string(), nullable=False),
                pa.field("source_manifest_sha256", pa.string(), nullable=False),
                pa.field("precedence_contract", pa.string(), nullable=False),
                pa.field("selected_source_row_id", pa.int64(), nullable=True),
                pa.field("selected_source_row_sha256", pa.string(), nullable=True),
                pa.field("selected_source_release_id", pa.string(), nullable=True),
                pa.field("selected_source_release_retrieved_at", pa.timestamp("us", tz="UTC"), nullable=True),
                pa.field("selected_source_release_payload_checksum", pa.string(), nullable=True),
                pa.field("selected_source_part_key", pa.string(), nullable=True),
                pa.field("selected_source_part_sha256", pa.string(), nullable=True),
                pa.field("selected_source_row_ordinal", pa.int64(), nullable=True),
                pa.field("input_source_row_count", pa.int64(), nullable=False),
                pa.field("input_source_row_digest", pa.string(), nullable=True),
                pa.field("input_source_row_ids", pa.list_(pa.int64()), nullable=True),
                pa.field("input_source_row_sha256s", pa.list_(pa.string()), nullable=True),
                pa.field("input_source_release_ids", pa.list_(pa.string()), nullable=True),
                pa.field("input_source_part_keys", pa.list_(pa.string()), nullable=True),
                pa.field("input_source_part_sha256s", pa.list_(pa.string()), nullable=True),
                pa.field("input_source_row_ordinals", pa.list_(pa.int64()), nullable=True),
            ],
            metadata={
                b"plantgeo_contract": b"climate-field-precipitation.snapshot-breakdown.v1",
                b"source_manifest_sha256": (
                    b"465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f"
                ),
            },
        ),
        sort_columns=(
            "support_key",
            "signal_name",
            "normalized_unit",
            "source_key",
            "cell_id",
            "observed_day",
        ),
    )
)

CLIMATE_FIELD_PRECIPITATION_TIER_DERIVATION: Final = register_tier_derivation(
    TierDerivation(
        stream=CLIMATE_FIELD_PRECIPITATION_STREAM,
        strategy=GridAggregation(
            longitude_column="cell_longitude",
            latitude_column="cell_latitude",
            key_columns=(
                "support_key",
                "signal_name",
                "normalized_unit",
                "observed_day",
                "source_key",
                "source_snapshot_id",
                "source_manifest_sha256",
                "precedence_contract",
            ),
            aggregations=(
                ColumnAggregation("cell_id", "null"),
                ColumnAggregation("source_parameter", "first"),
                ColumnAggregation("normalized_value", "mean"),
                ColumnAggregation("observation_count", "sum"),
                ColumnAggregation("newest_observed_at", "max"),
                ColumnAggregation("coverage_fraction", "mean"),
                ColumnAggregation("allowed_client_exposure", "all"),
                ColumnAggregation("selected_source_row_id", "null"),
                ColumnAggregation("selected_source_row_sha256", "null"),
                ColumnAggregation("selected_source_release_id", "null"),
                ColumnAggregation("selected_source_release_retrieved_at", "null"),
                ColumnAggregation("selected_source_release_payload_checksum", "null"),
                ColumnAggregation("selected_source_part_key", "null"),
                ColumnAggregation("selected_source_part_sha256", "null"),
                ColumnAggregation("selected_source_row_ordinal", "null"),
                ColumnAggregation("input_source_row_count", "sum"),
                ColumnAggregation("input_source_row_digest", "null"),
                ColumnAggregation("input_source_row_ids", "null"),
                ColumnAggregation("input_source_row_sha256s", "null"),
                ColumnAggregation("input_source_release_ids", "null"),
                ColumnAggregation("input_source_part_keys", "null"),
                ColumnAggregation("input_source_part_sha256s", "null"),
                ColumnAggregation("input_source_row_ordinals", "null"),
            ),
        ),
        base_non_null_columns=(
            "cell_id",
            "selected_source_row_id",
            "selected_source_row_sha256",
            "selected_source_release_id",
            "selected_source_release_retrieved_at",
            "selected_source_release_payload_checksum",
            "selected_source_part_key",
            "selected_source_part_sha256",
            "selected_source_row_ordinal",
            "input_source_row_digest",
            "input_source_row_ids",
            "input_source_row_sha256s",
            "input_source_release_ids",
            "input_source_part_keys",
            "input_source_part_sha256s",
            "input_source_row_ordinals",
        ),
    )
)

__all__ = [
    "CLIMATE_FIELD_PRECIPITATION_SCHEMA",
    "CLIMATE_FIELD_PRECIPITATION_STREAM",
    "CLIMATE_FIELD_PRECIPITATION_TIER_DERIVATION",
]
