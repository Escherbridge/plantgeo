"""The Parquet schema registry, and the ten-column signal plane it ships registered."""

from __future__ import annotations

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.paths import PartitionPathError
from agri_data_service.warehouse.parquet.schema import (
    SIGNAL_PLANE_GRAIN,
    SIGNAL_PLANE_SCHEMA,
    SIGNAL_PLANE_STREAM,
    ParquetStreamSchema,
    StreamSchemaConflictError,
    StreamSchemaError,
    get_stream_schema,
    register_stream_schema,
    registered_stream_names,
    stream_schema_module,
)

SIGNAL_PLANE_COLUMN_COUNT = 10

EXPECTED_SIGNAL_PLANE_FIELDS = (
    ("support_key", pa.string(), False),
    ("signal_name", pa.string(), False),
    ("normalized_unit", pa.string(), False),
    ("cell_id", pa.string(), False),
    ("observed_day", pa.date32(), False),
    ("normalized_value", pa.float64(), False),
    ("observation_count", pa.int64(), False),
    ("newest_observed_at", pa.timestamp("us", tz="UTC"), False),
    ("coverage_fraction", pa.float64(), True),
    ("allowed_client_exposure", pa.bool_(), True),
)


def test_signal_plane_schema_is_the_measured_ten_columns_in_order() -> None:
    """RUNBOOK section 0.22.4 froze these ten columns, their order, and their types."""
    actual = tuple((field.name, field.type, field.nullable) for field in SIGNAL_PLANE_SCHEMA.arrow_schema)

    assert actual == EXPECTED_SIGNAL_PLANE_FIELDS
    assert len(actual) == SIGNAL_PLANE_COLUMN_COUNT


def test_signal_plane_schema_does_not_carry_the_three_dropped_aggregates() -> None:
    """min/max/avg equalled normalized_value on every measured row and cost 3.81x. Never re-add them."""
    assert not {"min_value", "max_value", "avg_value"} & set(SIGNAL_PLANE_SCHEMA.column_names)


def test_allowed_client_exposure_is_boolean_not_string() -> None:
    """Reading this column as a string cost a failed export run; the type is pinned here."""
    assert SIGNAL_PLANE_SCHEMA.arrow_schema.field("allowed_client_exposure").type == pa.bool_()


def test_signal_plane_sorts_to_its_grain_with_zstd() -> None:
    assert SIGNAL_PLANE_SCHEMA.sort_columns == SIGNAL_PLANE_GRAIN
    assert SIGNAL_PLANE_GRAIN == (
        "support_key",
        "signal_name",
        "normalized_unit",
        "cell_id",
        "observed_day",
    )
    assert SIGNAL_PLANE_SCHEMA.compression == "zstd"


def test_signal_plane_is_registered_under_its_layer_slug() -> None:
    assert get_stream_schema(SIGNAL_PLANE_STREAM) is SIGNAL_PLANE_SCHEMA
    assert SIGNAL_PLANE_STREAM in registered_stream_names()


def test_stream_schema_module_maps_a_hyphenated_slug_to_an_importable_module() -> None:
    assert stream_schema_module("fire-detections") == "agri_data_service.warehouse.schemas.fire_detections"
    assert stream_schema_module("sensors") == "agri_data_service.warehouse.schemas.sensors"


def test_stream_schema_module_rejects_a_slug_that_is_not_a_layer_slug() -> None:
    """The registry key doubles as an object prefix and a module path; both must be safe."""
    with pytest.raises(PartitionPathError):
        stream_schema_module("../evil")


def test_unregistered_stream_names_the_module_it_expected() -> None:
    with pytest.raises(StreamSchemaError) as caught:
        get_stream_schema("no-such-lane")

    assert "agri_data_service.warehouse.schemas.no_such_lane" in str(caught.value)


def test_registering_an_identical_contract_twice_is_a_no_op() -> None:
    """Module re-import must not explode; only a genuine divergence is an error."""
    spec = ParquetStreamSchema(
        name="registry-idempotence-probe",
        arrow_schema=pa.schema([pa.field("value", pa.float64(), nullable=False)]),
        sort_columns=("value",),
    )

    assert register_stream_schema(spec) is spec
    assert register_stream_schema(spec) is spec


def test_registering_a_different_contract_under_a_taken_name_raises() -> None:
    """Last-importer-wins would let one lane silently reshape another lane's files."""
    first = ParquetStreamSchema(
        name="registry-conflict-probe",
        arrow_schema=pa.schema([pa.field("value", pa.float64(), nullable=False)]),
        sort_columns=("value",),
    )
    second = ParquetStreamSchema(
        name="registry-conflict-probe",
        arrow_schema=pa.schema([pa.field("value", pa.int64(), nullable=False)]),
        sort_columns=("value",),
    )
    register_stream_schema(first)

    with pytest.raises(StreamSchemaConflictError):
        register_stream_schema(second)


def test_a_stream_must_declare_a_grain_that_exists_in_its_schema() -> None:
    with pytest.raises(ValueError, match="absent from its schema"):
        ParquetStreamSchema(
            name="grain-probe",
            arrow_schema=pa.schema([pa.field("value", pa.float64(), nullable=False)]),
            sort_columns=("missing",),
        )
    with pytest.raises(ValueError, match="grain"):
        ParquetStreamSchema(
            name="grain-probe",
            arrow_schema=pa.schema([pa.field("value", pa.float64(), nullable=False)]),
            sort_columns=(),
        )
