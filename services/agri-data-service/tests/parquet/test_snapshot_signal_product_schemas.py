"""Autoload and pin the completed snapshot-derived signal product contracts."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Final

import duckdb
import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.warehouse.parquet import tiers as tier_contracts
from agri_data_service.warehouse.parquet.schema import (
    SIGNAL_PLANE_SCHEMA,
    SIGNAL_PLANE_TIER_DERIVATION,
    get_stream_schema,
)
from agri_data_service.warehouse.parquet.tiers import (
    ColumnAggregation,
    GridAggregation,
    TierDerivation,
    derive_tier,
    tier_derivation,
    validate_derivation_against_schema,
)

SOURCE_MANIFEST_SHA256: Final = "465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f"

SIGNAL_CLONE_STREAMS: Final = (
    "soil-field-vpd",
    "climate-field-air-temperature-mean",
    "climate-field-air-temperature-max",
    "climate-field-air-temperature-min",
    "climate-field-wind-speed",
)

SNAPSHOT_LINEAGE_STREAMS: Final = (
    "climate-field-relative-humidity",
    "climate-field-shortwave-radiation",
    "soil-field-moisture-0-7cm",
    "soil-field-moisture-7-28cm",
    "soil-field-moisture-28-100cm",
    "climate-field-precipitation",
)

SOIL_TEMPERATURE_STREAMS: Final = (
    "soil-temperature-0-to-7cm",
    "soil-temperature-7-to-28cm",
    "soil-temperature-28-to-100cm",
    "soil-temperature-100-to-255cm",
)

ALL_SNAPSHOT_PRODUCT_STREAMS: Final = SIGNAL_CLONE_STREAMS + SNAPSHOT_LINEAGE_STREAMS + SOIL_TEMPERATURE_STREAMS

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


def _field_contract(schema: pa.Schema) -> tuple[tuple[str, pa.DataType, bool], ...]:
    return tuple((field.name, field.type, field.nullable) for field in schema)


EXPECTED_SNAPSHOT_LINEAGE_FIELDS: Final = _field_contract(
    pa.schema(
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
        ]
    )
)

EXPECTED_SOIL_TEMPERATURE_FIELDS: Final = _field_contract(
    pa.schema(
        [
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
        ]
    )
)


@pytest.mark.parametrize("stream", ALL_SNAPSHOT_PRODUCT_STREAMS)
def test_every_completed_snapshot_product_autoloads_for_readers(stream: str) -> None:
    schema = get_stream_schema(stream)

    assert schema.name == stream
    assert tier_derivation(stream).stream == stream
    assert validate_derivation_against_schema(stream) == ()


@pytest.mark.parametrize("stream", SIGNAL_CLONE_STREAMS)
def test_signal_clone_streams_change_only_the_frozen_stream_name(stream: str) -> None:
    schema = get_stream_schema(stream)

    assert schema == replace(SIGNAL_PLANE_SCHEMA, name=stream)
    assert schema.arrow_schema is SIGNAL_PLANE_SCHEMA.arrow_schema
    assert tier_derivation(stream) == replace(SIGNAL_PLANE_TIER_DERIVATION, stream=stream)


@pytest.mark.parametrize("stream", SNAPSHOT_LINEAGE_STREAMS)
def test_snapshot_lineage_streams_pin_the_exact_33_field_contract(stream: str) -> None:
    schema = get_stream_schema(stream)

    assert _field_contract(schema.arrow_schema) == EXPECTED_SNAPSHOT_LINEAGE_FIELDS
    assert len(schema.column_names) == 33
    assert schema.sort_columns == SNAPSHOT_LINEAGE_GRAIN
    assert schema.arrow_schema.metadata == {
        b"plantgeo_contract": f"{stream}.snapshot-breakdown.v1".encode(),
        b"source_manifest_sha256": SOURCE_MANIFEST_SHA256.encode(),
    }


@pytest.mark.parametrize("stream", SNAPSHOT_LINEAGE_STREAMS)
def test_snapshot_lineage_streams_pin_the_completed_grid_derivation(stream: str) -> None:
    assert tier_derivation(stream) == TierDerivation(
        stream=stream,
        strategy=GridAggregation(
            longitude_column="cell_longitude",
            latitude_column="cell_latitude",
            key_columns=SNAPSHOT_LINEAGE_KEY_COLUMNS,
            aggregations=SNAPSHOT_LINEAGE_AGGREGATIONS,
        ),
        base_non_null_columns=SNAPSHOT_LINEAGE_BASE_NON_NULL_COLUMNS,
    )


@pytest.mark.parametrize("stream", SOIL_TEMPERATURE_STREAMS)
def test_soil_temperature_streams_pin_the_exact_21_field_contract(stream: str) -> None:
    schema = get_stream_schema(stream)

    assert _field_contract(schema.arrow_schema) == EXPECTED_SOIL_TEMPERATURE_FIELDS
    assert len(schema.column_names) == 21
    assert schema.sort_columns == SOIL_TEMPERATURE_GRAIN
    assert schema.arrow_schema.metadata == {b"plantgeo_contract": b"plantgeo.signal-product-lane.v1"}


@pytest.mark.parametrize("stream", SOIL_TEMPERATURE_STREAMS)
def test_soil_temperature_streams_pin_the_completed_grid_derivation(stream: str) -> None:
    assert tier_derivation(stream) == TierDerivation(
        stream=stream,
        strategy=GridAggregation(
            longitude_column="cell_longitude",
            latitude_column="cell_latitude",
            key_columns=SOIL_TEMPERATURE_KEY_COLUMNS,
            aggregations=SOIL_TEMPERATURE_AGGREGATIONS,
        ),
        base_non_null_columns=SOIL_TEMPERATURE_BASE_NON_NULL_COLUMNS,
    )


def _soil_temperature_row(*, cell_id: str, longitude: float, lineage: str, value: float) -> dict[str, object]:
    return {
        "data_source_key": "open-meteo-era5-land-archive",
        "source_parameter": "soil_temperature_0_to_7cm_mean",
        "support_key": "era5-land-0.1deg",
        "signal_name": "soil_temperature_level_1",
        "normalized_unit": "C",
        "cell_id": cell_id,
        "observed_day": date(2026, 8, 1),
        "normalized_value": value,
        "observation_count": 1,
        "newest_observed_at": datetime(2026, 8, 1, 12, tzinfo=UTC),
        "coverage_fraction": 1.0,
        "allowed_client_exposure": False,
        "cell_longitude": longitude,
        "cell_latitude": 43.01,
        "selected_observation_id": 1,
        "selected_canonical_row_sha256": "c" * 64,
        "selected_source_release_id": "release",
        "selected_release_retrieved_at": datetime(2026, 8, 1, 13, tzinfo=UTC),
        "physical_candidate_count": 1,
        "lineage_sha256": lineage,
        "input_manifest_sha256": SOURCE_MANIFEST_SHA256,
    }


def _expected_lineage_digest(values: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for value in sorted(values):
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def test_soil_temperature_polars_derivation_matches_the_completed_lineage_digest() -> None:
    stream = SOIL_TEMPERATURE_STREAMS[0]
    schema = get_stream_schema(stream).arrow_schema
    lineages = ("b" * 64, "a" * 64)
    table = pa.Table.from_pylist(
        [
            _soil_temperature_row(cell_id="cell-b", longitude=-116.01, lineage=lineages[0], value=3.0),
            _soil_temperature_row(cell_id="cell-a", longitude=-116.02, lineage=lineages[1], value=1.0),
        ],
        schema=schema,
    )
    frame = pl.from_arrow(table)
    assert isinstance(frame, pl.DataFrame)

    derived = derive_tier(frame, stream=stream, tier=5)

    assert derived.height == 1
    assert derived["lineage_sha256"][0] == _expected_lineage_digest(lineages)
    assert derived["physical_candidate_count"][0] == 2
    assert derived["normalized_value"][0] == 2.0
    assert derived["selected_observation_id"][0] is None


def test_duckdb_sha256_lines_form_matches_the_completed_lineage_digest() -> None:
    lineages = ("b" * 64, "a" * 64)
    expression = tier_contracts._DUCKDB_AGGREGATES["sha256-lines"].format(column="lineage")
    connection = duckdb.connect(database=":memory:")
    try:
        actual = connection.execute(
            f"SELECT {expression} FROM (VALUES (?), (?)) AS source(lineage)",
            list(lineages),
        ).fetchone()
    finally:
        connection.close()

    assert actual is not None
    assert actual[0] == _expected_lineage_digest(lineages)
