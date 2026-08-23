"""The Parquet schema registry, its per-kind lookup, and the ten-column signal plane it ships registered."""

from __future__ import annotations

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.paths import PartitionPathError
from agri_data_service.warehouse.parquet.schema import (
    FORECAST_PROVENANCE_COLUMNS,
    FORECAST_PROVENANCE_FIELDS,
    FORECAST_PROVENANCE_GRAIN,
    SIGNAL_PLANE_GRAIN,
    SIGNAL_PLANE_SCHEMA,
    SIGNAL_PLANE_STREAM,
    ParquetStreamSchema,
    StreamSchemaConflictError,
    StreamSchemaError,
    forecast_stream_schema,
    get_stream_schema,
    observed_stream_schema,
    register_stream_schema,
    registered_stream_names,
    stream_schema_module,
)

SIGNAL_PLANE_COLUMN_COUNT = 10
FORECAST_PROVENANCE_COLUMN_COUNT = 6

# The exact six of `conductor/code_styleguides/layer-lanes.md` section 3, with the types every one
# of the five shipped forecasters already emits (e.g. `method/monte_carlo/sensors.py:232-238`).
EXPECTED_FORECAST_PROVENANCE_FIELDS = (
    ("forecast_run_id", pa.string(), False),
    ("random_seed", pa.int64(), False),
    ("ensemble_size", pa.int32(), False),
    ("horizon_days", pa.int16(), False),
    ("issued_on", pa.date32(), False),
    ("quantile", pa.float64(), False),
)

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


# --- The per-kind lookup: RUNBOOK section 0.28.1 decision 1 -------------------------------------


def test_provenance_is_the_exact_six_columns_the_contract_names_in_order() -> None:
    """layer-lanes.md section 3's six, typed as every shipped forecaster already emits them."""
    actual = tuple((field.name, field.type, field.nullable) for field in FORECAST_PROVENANCE_FIELDS)

    assert actual == EXPECTED_FORECAST_PROVENANCE_FIELDS
    assert len(actual) == FORECAST_PROVENANCE_COLUMN_COUNT
    assert tuple(name for name, _type, _nullable in EXPECTED_FORECAST_PROVENANCE_FIELDS) == (
        FORECAST_PROVENANCE_COLUMNS
    )


def test_provenance_is_quantile_not_draw_index() -> None:
    """Section 3 offers `quantile` OR `draw_index`; all five shipped forecasters chose `quantile`."""
    assert "quantile" in FORECAST_PROVENANCE_COLUMNS
    assert "draw_index" not in FORECAST_PROVENANCE_COLUMNS


def test_provenance_is_never_nullable() -> None:
    """A column that is unconditionally NULL is a placeholder, not provenance; a forecast row always has all six."""
    assert all(not field.nullable for field in FORECAST_PROVENANCE_FIELDS)


def test_observed_returns_the_registered_object_itself_so_files_stay_byte_identical() -> None:
    """The additive-only guarantee: nothing about the observed side may move, not even by a copy."""
    assert get_stream_schema(SIGNAL_PLANE_STREAM) is SIGNAL_PLANE_SCHEMA
    assert get_stream_schema(SIGNAL_PLANE_STREAM, "observed") is SIGNAL_PLANE_SCHEMA
    assert observed_stream_schema(SIGNAL_PLANE_STREAM) is SIGNAL_PLANE_SCHEMA


def test_deriving_the_forecast_side_does_not_disturb_the_observed_side() -> None:
    """Reading `forecast` must not mutate, re-register, or reorder what the observed writer uses."""
    before = SIGNAL_PLANE_SCHEMA.column_names

    get_stream_schema(SIGNAL_PLANE_STREAM, "forecast")

    assert SIGNAL_PLANE_SCHEMA.column_names == before
    assert get_stream_schema(SIGNAL_PLANE_STREAM) is SIGNAL_PLANE_SCHEMA
    assert SIGNAL_PLANE_SCHEMA.sort_columns == SIGNAL_PLANE_GRAIN


def test_forecast_is_the_observed_columns_in_order_then_the_six() -> None:
    """Identical MEASUREMENT columns, per decision 1's relaxation of section 2; provenance appends."""
    forecast = get_stream_schema(SIGNAL_PLANE_STREAM, "forecast")

    assert forecast.column_names == SIGNAL_PLANE_SCHEMA.column_names + FORECAST_PROVENANCE_COLUMNS
    for name, expected_type, expected_nullable in EXPECTED_SIGNAL_PLANE_FIELDS:
        field = forecast.arrow_schema.field(name)
        assert (field.type, field.nullable) == (expected_type, expected_nullable)


def test_forecast_keeps_the_lane_slug_as_its_name_and_the_lane_codec() -> None:
    """`name` is simultaneously the registry key and the `layer=<slug>/` prefix; the kind is the partition."""
    forecast = get_stream_schema(SIGNAL_PLANE_STREAM, "forecast")

    assert forecast.name == SIGNAL_PLANE_STREAM
    assert forecast.compression == SIGNAL_PLANE_SCHEMA.compression


def test_forecast_grain_extends_the_observed_grain_so_the_pre_write_sort_leaves_no_ties() -> None:
    """A forecast partition holds one row per quantile per cell-day; the observed grain alone is not a key."""
    forecast = get_stream_schema(SIGNAL_PLANE_STREAM, "forecast")

    assert forecast.sort_columns == SIGNAL_PLANE_GRAIN + FORECAST_PROVENANCE_GRAIN
    assert FORECAST_PROVENANCE_GRAIN == ("issued_on", "horizon_days", "quantile")
    assert set(forecast.sort_columns) <= set(forecast.column_names)


def test_the_forecast_side_is_cached_not_rebuilt_per_call() -> None:
    """Every write would otherwise rebuild an Arrow schema; identity also lets callers compare cheaply."""
    assert get_stream_schema(SIGNAL_PLANE_STREAM, "forecast") is get_stream_schema(SIGNAL_PLANE_STREAM, "forecast")


def test_forecast_resolves_for_a_lane_that_has_to_be_autoloaded() -> None:
    """The per-kind lookup goes through the same autoload as the observed one, not a second mechanism."""
    forecast = get_stream_schema("fire-detections", "forecast")

    assert forecast.column_names[-FORECAST_PROVENANCE_COLUMN_COUNT:] == FORECAST_PROVENANCE_COLUMNS
    observed_names = get_stream_schema("fire-detections").column_names
    assert forecast.column_names[: -FORECAST_PROVENANCE_COLUMN_COUNT] == observed_names


def test_no_registered_lane_declares_a_provenance_column_on_its_observed_side() -> None:
    """A lane that did would make the same name mean two things across the two kinds."""
    for name in registered_stream_names():
        observed = get_stream_schema(name)
        assert not set(observed.column_names) & set(FORECAST_PROVENANCE_COLUMNS), name


def test_an_observed_schema_that_already_claims_a_provenance_name_is_refused() -> None:
    """Silently shadowing it would let a lane's own `quantile` be overwritten by the forecast run's."""
    clashing = ParquetStreamSchema(
        name="provenance-collision-probe",
        arrow_schema=pa.schema(
            [
                pa.field("cell_id", pa.string(), nullable=False),
                pa.field("quantile", pa.float64(), nullable=False),
            ]
        ),
        sort_columns=("cell_id",),
    )

    with pytest.raises(StreamSchemaConflictError, match="quantile"):
        forecast_stream_schema(clashing)


def test_an_unknown_kind_is_refused_rather_than_falling_through_to_observed() -> None:
    """A typo silently serving observed rows as a forecast is the wrong-but-plausible output to prevent."""
    with pytest.raises(PartitionPathError):
        get_stream_schema(SIGNAL_PLANE_STREAM, "predicted")  # type: ignore[arg-type]


def test_an_unregistered_stream_still_names_its_module_when_asked_for_a_forecast() -> None:
    """The forecast path must not swallow the missing-lane diagnostic the observed path gives."""
    with pytest.raises(StreamSchemaError) as caught:
        get_stream_schema("no-such-lane", "forecast")

    assert "agri_data_service.warehouse.schemas.no_such_lane" in str(caught.value)
