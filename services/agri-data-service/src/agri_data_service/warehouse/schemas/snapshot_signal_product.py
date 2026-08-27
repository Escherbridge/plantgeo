"""Canonical registrations for immutable snapshot-derived signal products."""

from __future__ import annotations

from dataclasses import replace
from typing import Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.warehouse.parquet.schema import (
    SIGNAL_PLANE_SCHEMA,
    SIGNAL_PLANE_TIER_DERIVATION,
    ParquetStreamSchema,
    register_stream_schema,
)
from agri_data_service.warehouse.parquet.tiers import (
    ColumnAggregation,
    GridAggregation,
    TierDerivation,
    register_tier_derivation,
)

SOURCE_MANIFEST_SHA256: Final = "465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f"

SNAPSHOT_LINEAGE_FIELDS: Final[tuple[pa.Field, ...]] = (
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
)

SNAPSHOT_LINEAGE_GRAIN: Final = (
    "support_key",
    "signal_name",
    "normalized_unit",
    "source_key",
    "cell_id",
    "observed_day",
)

SNAPSHOT_LINEAGE_KEY_COLUMNS: Final = (
    "support_key",
    "signal_name",
    "normalized_unit",
    "observed_day",
    "source_key",
    "source_snapshot_id",
    "source_manifest_sha256",
    "precedence_contract",
)

SNAPSHOT_LINEAGE_AGGREGATIONS: Final = (
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
)

SNAPSHOT_LINEAGE_BASE_NON_NULL_COLUMNS: Final = (
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
)

SOIL_TEMPERATURE_FIELDS: Final[tuple[pa.Field, ...]] = (
    pa.field("data_source_key", pa.string(), nullable=False),
    pa.field("source_parameter", pa.string(), nullable=False),
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
    pa.field("selected_observation_id", pa.int64(), nullable=True),
    pa.field("selected_canonical_row_sha256", pa.string(), nullable=True),
    pa.field("selected_source_release_id", pa.string(), nullable=True),
    pa.field("selected_release_retrieved_at", pa.timestamp("us", tz="UTC"), nullable=True),
    pa.field("physical_candidate_count", pa.int64(), nullable=False),
    pa.field("lineage_sha256", pa.string(), nullable=False),
    pa.field("input_manifest_sha256", pa.string(), nullable=False),
)

SOIL_TEMPERATURE_GRAIN: Final = (
    "data_source_key",
    "source_parameter",
    "support_key",
    "signal_name",
    "normalized_unit",
    "cell_id",
    "observed_day",
)

SOIL_TEMPERATURE_KEY_COLUMNS: Final = (
    "data_source_key",
    "source_parameter",
    "support_key",
    "signal_name",
    "normalized_unit",
    "observed_day",
)

SOIL_TEMPERATURE_AGGREGATIONS: Final = (
    ColumnAggregation("cell_id", "null"),
    ColumnAggregation("normalized_value", "mean"),
    ColumnAggregation("observation_count", "sum"),
    ColumnAggregation("newest_observed_at", "max"),
    ColumnAggregation("coverage_fraction", "mean"),
    ColumnAggregation("allowed_client_exposure", "all"),
    ColumnAggregation("selected_observation_id", "null"),
    ColumnAggregation("selected_canonical_row_sha256", "null"),
    ColumnAggregation("selected_source_release_id", "null"),
    ColumnAggregation("selected_release_retrieved_at", "null"),
    ColumnAggregation("physical_candidate_count", "sum"),
    ColumnAggregation("lineage_sha256", "sha256-lines"),
    ColumnAggregation("input_manifest_sha256", "first"),
)

SOIL_TEMPERATURE_BASE_NON_NULL_COLUMNS: Final = (
    "cell_id",
    "selected_observation_id",
    "selected_canonical_row_sha256",
    "selected_source_release_id",
    "selected_release_retrieved_at",
)


def register_signal_plane_product(stream: str) -> tuple[ParquetStreamSchema, TierDerivation]:
    """Register a physical stream that changes only the frozen signal-plane name."""
    schema = register_stream_schema(replace(SIGNAL_PLANE_SCHEMA, name=stream))
    derivation = register_tier_derivation(replace(SIGNAL_PLANE_TIER_DERIVATION, stream=stream))
    return schema, derivation


def register_snapshot_lineage_product(stream: str) -> tuple[ParquetStreamSchema, TierDerivation]:
    """Register one 33-field snapshot-breakdown stream and its lineage-aware grid ladder."""
    schema = register_stream_schema(
        ParquetStreamSchema(
            name=stream,
            arrow_schema=pa.schema(
                SNAPSHOT_LINEAGE_FIELDS,
                metadata={
                    b"plantgeo_contract": f"{stream}.snapshot-breakdown.v1".encode(),
                    b"source_manifest_sha256": SOURCE_MANIFEST_SHA256.encode(),
                },
            ),
            sort_columns=SNAPSHOT_LINEAGE_GRAIN,
        )
    )
    derivation = register_tier_derivation(
        TierDerivation(
            stream=stream,
            strategy=GridAggregation(
                longitude_column="cell_longitude",
                latitude_column="cell_latitude",
                key_columns=SNAPSHOT_LINEAGE_KEY_COLUMNS,
                aggregations=SNAPSHOT_LINEAGE_AGGREGATIONS,
            ),
            base_non_null_columns=SNAPSHOT_LINEAGE_BASE_NON_NULL_COLUMNS,
        )
    )
    return schema, derivation


def register_soil_temperature_product(stream: str) -> tuple[ParquetStreamSchema, TierDerivation]:
    """Register one 21-field soil-temperature stream from the immutable four-lane bundle."""
    schema = register_stream_schema(
        ParquetStreamSchema(
            name=stream,
            arrow_schema=pa.schema(
                SOIL_TEMPERATURE_FIELDS,
                metadata={b"plantgeo_contract": b"plantgeo.signal-product-lane.v1"},
            ),
            sort_columns=SOIL_TEMPERATURE_GRAIN,
        )
    )
    derivation = register_tier_derivation(
        TierDerivation(
            stream=stream,
            strategy=GridAggregation(
                longitude_column="cell_longitude",
                latitude_column="cell_latitude",
                key_columns=SOIL_TEMPERATURE_KEY_COLUMNS,
                aggregations=SOIL_TEMPERATURE_AGGREGATIONS,
            ),
            base_non_null_columns=SOIL_TEMPERATURE_BASE_NON_NULL_COLUMNS,
        )
    )
    return schema, derivation


__all__ = [
    "SNAPSHOT_LINEAGE_AGGREGATIONS",
    "SNAPSHOT_LINEAGE_BASE_NON_NULL_COLUMNS",
    "SNAPSHOT_LINEAGE_FIELDS",
    "SNAPSHOT_LINEAGE_GRAIN",
    "SNAPSHOT_LINEAGE_KEY_COLUMNS",
    "SOIL_TEMPERATURE_AGGREGATIONS",
    "SOIL_TEMPERATURE_BASE_NON_NULL_COLUMNS",
    "SOIL_TEMPERATURE_FIELDS",
    "SOIL_TEMPERATURE_GRAIN",
    "SOIL_TEMPERATURE_KEY_COLUMNS",
    "SOURCE_MANIFEST_SHA256",
    "register_signal_plane_product",
    "register_snapshot_lineage_product",
    "register_soil_temperature_product",
]
