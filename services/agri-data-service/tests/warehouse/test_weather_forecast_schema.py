"""The `weather-forecast` release-series schema: grain, registration, and the never-null-as-zero rule."""

from __future__ import annotations

import importlib

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.warehouse.parquet.schema import (
    ParquetStreamSchema,
    StreamSchemaConflictError,
    get_stream_schema,
    observed_stream_schema,
    register_stream_schema,
    registered_stream_names,
    stream_schema_module,
)
from agri_data_service.warehouse.schemas.weather_forecast import (
    MISSING_REASONS,
    SUPPORT_KINDS,
    VARIABLES,
    WEATHER_FORECAST_GRAIN,
    WEATHER_FORECAST_SCHEMA,
    WEATHER_FORECAST_STREAM,
    Variable,
)

EXPECTED_GRAIN = ("cell_id", "valid_time", "variable")

EXPECTED_COLUMNS_IN_ORDER = (
    "run_id",
    "model_init_time",
    "provider_issue_time",
    "fetched_at",
    "admitted_at",
    "published_at",
    "valid_time",
    "interval_start",
    "interval_end",
    "lead_hours",
    "cell_id",
    "latitude",
    "longitude",
    "variable",
    "value",
    "unit",
    "statistic",
    "support",
    "missing_reason",
)


def test_stream_name_is_the_layer_slug() -> None:
    assert WEATHER_FORECAST_STREAM == "weather-forecast"
    assert WEATHER_FORECAST_SCHEMA.name == WEATHER_FORECAST_STREAM


def test_grain_matches_the_frozen_contract() -> None:
    """`.omc/ultrapilot-20260918/W8-E-PLAN.md` section 1.4: (cell_id, valid_time, variable)."""
    assert WEATHER_FORECAST_GRAIN == EXPECTED_GRAIN
    assert WEATHER_FORECAST_SCHEMA.sort_columns == EXPECTED_GRAIN


def test_columns_are_exactly_the_frozen_set_in_order() -> None:
    assert WEATHER_FORECAST_SCHEMA.column_names == EXPECTED_COLUMNS_IN_ORDER


def test_run_id_is_constant_within_a_partition_so_it_is_not_in_the_grain() -> None:
    assert "run_id" not in WEATHER_FORECAST_GRAIN


def test_value_and_missing_reason_are_both_nullable_so_neither_stands_in_for_the_other() -> None:
    """A null `value` must be explainable; null is never used to mean zero."""
    schema = WEATHER_FORECAST_SCHEMA.arrow_schema
    assert schema.field("value").nullable
    assert schema.field("missing_reason").nullable
    assert schema.field("value").type == pa.float64()
    assert schema.field("missing_reason").type == pa.string()


def test_provenance_and_grain_columns_are_never_null() -> None:
    schema = WEATHER_FORECAST_SCHEMA.arrow_schema
    for name in ("run_id", "model_init_time", "fetched_at", "admitted_at", "published_at", "valid_time"):
        assert not schema.field(name).nullable, name
    # cell_id is part of the grain but nullable, matching every other lane's coarse-rung convention
    # (signal, weather-observations): a merged cell can honestly name no single source cell.
    assert schema.field("cell_id").nullable


def test_accumulation_interval_columns_are_nullable() -> None:
    schema = WEATHER_FORECAST_SCHEMA.arrow_schema
    assert schema.field("interval_start").nullable
    assert schema.field("interval_end").nullable


def test_lead_hours_is_int16() -> None:
    assert WEATHER_FORECAST_SCHEMA.arrow_schema.field("lead_hours").type == pa.int16()


def test_timestamps_are_microsecond_utc() -> None:
    timestamp_columns = (
        "model_init_time",
        "provider_issue_time",
        "fetched_at",
        "admitted_at",
        "published_at",
        "valid_time",
        "interval_start",
        "interval_end",
    )
    for name in timestamp_columns:
        field_type = WEATHER_FORECAST_SCHEMA.arrow_schema.field(name).type
        assert field_type == pa.timestamp("us", tz="UTC"), name


def test_missing_reasons_are_the_exact_four_the_plan_names() -> None:
    assert frozenset({"outside_domain", "not_generated", "upstream_failed", "stale_run"}) == MISSING_REASONS


def test_support_kinds_are_the_exact_three_the_plan_names() -> None:
    assert frozenset({"native_grid", "sampled_point", "derived_field"}) == SUPPORT_KINDS


def test_wind_keeps_the_component_split_and_the_two_derived_variables() -> None:
    """`wind_u_10m`/`wind_v_10m` are earth-relative components; direction/speed are derived, not averaged."""
    assert {"wind_u_10m", "wind_v_10m", "wind_speed_10m", "wind_direction_10m"} <= VARIABLES.keys()
    assert VARIABLES["wind_u_10m"].statistic == "instantaneous_earth_relative"
    assert VARIABLES["wind_v_10m"].statistic == "instantaneous_earth_relative"
    assert VARIABLES["wind_direction_10m"].statistic == "meteorological_from_true_north"
    assert VARIABLES["wind_speed_10m"].statistic == "derived_vector_magnitude"


def test_every_variable_declares_a_unit_and_plausible_bounds() -> None:
    for name, variable in VARIABLES.items():
        assert isinstance(variable, Variable), name
        assert variable.minimum < variable.maximum, name
        assert variable.unit, name
        assert variable.statistic, name


def test_the_synthetic_fixture_metadata_did_not_survive_the_port() -> None:
    """The codex branch this schema was ported from was fixture-only; nothing of that ships here."""
    metadata = WEATHER_FORECAST_SCHEMA.arrow_schema.metadata or {}
    assert b"admission" not in metadata
    module = importlib.import_module("agri_data_service.warehouse.schemas.weather_forecast")
    for removed_symbol in ("VERSION", "validate_run_id", "SAMPLES", "MAX_POINTS"):
        assert not hasattr(module, removed_symbol), removed_symbol


def test_stream_schema_module_maps_the_hyphenated_slug() -> None:
    assert stream_schema_module("weather-forecast") == "agri_data_service.warehouse.schemas.weather_forecast"


def test_the_module_registers_at_import_time_and_autoloads() -> None:
    """`get_stream_schema` must resolve this lane even for a caller that never imported this module."""
    resolved = get_stream_schema(WEATHER_FORECAST_STREAM)

    assert resolved is WEATHER_FORECAST_SCHEMA
    assert observed_stream_schema(WEATHER_FORECAST_STREAM) is WEATHER_FORECAST_SCHEMA
    assert WEATHER_FORECAST_STREAM in registered_stream_names()


def test_re_registering_the_identical_contract_is_a_no_op() -> None:
    assert register_stream_schema(WEATHER_FORECAST_SCHEMA) is WEATHER_FORECAST_SCHEMA


def test_no_forecast_provenance_column_collides_on_the_observed_side() -> None:
    """`layer-lanes.md` section 3's six provenance columns belong to `kind=forecast` alone."""
    forecast_provenance_columns = {
        "forecast_run_id",
        "random_seed",
        "ensemble_size",
        "horizon_days",
        "issued_on",
        "quantile",
    }
    assert not forecast_provenance_columns & set(WEATHER_FORECAST_SCHEMA.column_names)


def test_a_stream_must_declare_a_grain_that_exists_in_its_schema() -> None:
    """The dataclass guard `ParquetStreamSchema.__post_init__` still applies to this lane's shape."""
    with pytest.raises(ValueError, match="absent from its schema"):
        ParquetStreamSchema(
            name="weather-forecast-grain-probe",
            arrow_schema=pa.schema([pa.field("value", pa.float64(), nullable=False)]),
            sort_columns=("missing",),
        )


def test_registering_a_divergent_contract_under_the_same_name_raises() -> None:
    """Guards against a future edit silently reshaping already-published objects."""
    divergent = ParquetStreamSchema(
        name=WEATHER_FORECAST_STREAM,
        arrow_schema=pa.schema([pa.field("value", pa.int64(), nullable=False)]),
        sort_columns=("value",),
    )

    with pytest.raises(StreamSchemaConflictError):
        register_stream_schema(divergent)
