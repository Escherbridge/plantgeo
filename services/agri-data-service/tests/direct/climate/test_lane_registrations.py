"""The eleven POWER registrations: their nature, their floors, their lags and their refusing adapter."""

from __future__ import annotations

import re
from datetime import date

import pytest

from agri_data_service.execution.job_executor_service import (
    ACTIVE_LANES_VARIABLE,
    COMMAND_CLEANUP_MARGIN_SECONDS,
    LANE_SPECS,
    parse_activation,
)
from agri_data_service.pipeline.direct.climate.products import (
    CLIMATE_DEFAULT_TIME_BUDGET_SECONDS,
    CLIMATE_FIELD_PRODUCTS,
    CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS,
)
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY, LaneRegistryError
from agri_data_service.warehouse.parquet.schema import get_stream_schema

CLIMATE_LANE_ID = "climate-nasa-power-direct-forward"
CLIMATE_STREAM_COUNT = 11
EXPECTED_COMMAND = ("python", "-m", "agri_data_service.pipeline.direct.climate")
EXPECTED_SCHEDULE = "40 * * * *"
EXPECTED_PHASE_OFFSET_SECONDS = 2400
EXPECTED_WRITER_FLOOR = "2026-06-01"
EXPECTED_CADENCE_DAYS = 1


def test_every_climate_product_is_a_registered_lane_with_a_time_axis() -> None:
    """A stream with no registration is one nothing schedules and nothing ever reports on."""
    assert len(CLIMATE_FIELD_PRODUCTS) == CLIMATE_STREAM_COUNT

    for product in CLIMATE_FIELD_PRODUCTS:
        registration = LANE_REGISTRY[product.stream]
        assert registration.nature == "daily_series", product.stream
        assert registration.history_floor == product.history_floor, product.stream
        assert registration.publication_lag_days == product.publication_lag_days, product.stream
        assert registration.cadence_days == EXPECTED_CADENCE_DAYS, product.stream
        assert registration.watermark is None, product.stream
        assert registration.forecast_module is None, product.stream
        assert registration.forecastable is False, product.stream


def test_no_climate_lane_declares_a_writer_ceiling() -> None:
    """A ceiling divides a calendar between two writers; here there is only one, bounded by its floor."""
    for product in CLIMATE_FIELD_PRODUCTS:
        assert LANE_REGISTRY[product.stream].writer_ceiling is None, product.stream


def test_every_climate_floor_cites_the_snapshot_it_was_read_off() -> None:
    """An uncited floor is a guess that reads as a measurement; the citation is the guard."""
    for product in CLIMATE_FIELD_PRODUCTS:
        basis = LANE_REGISTRY[product.stream].floor_basis
        assert "daily_series" in basis, product.stream
        assert "SOURCE-DIRECT" in basis, product.stream
        assert product.snapshot_last_day.isoformat() in basis, product.stream
        assert "coverage_census.py" in basis or "CONSERVATIVE AND NOT MEASURED" in basis, product.stream


def test_the_shortwave_floor_and_lag_differ_from_the_meteorology_ones() -> None:
    """Its immutable history ends nine weeks earlier and its source publishes months behind."""
    shortwave = LANE_REGISTRY["climate-field-shortwave-radiation"]
    meteorology = LANE_REGISTRY["climate-field-air-temperature-mean"]

    assert shortwave.history_floor == date(2026, 6, 1)
    assert meteorology.history_floor == date(2026, 8, 7)
    assert shortwave.publication_lag_days == CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS
    assert shortwave.publication_lag_days > meteorology.publication_lag_days


@pytest.mark.asyncio
async def test_the_registered_adapter_refuses_and_names_the_writer_that_owns_the_lane() -> None:
    """These streams have no PostgreSQL producer; a generic export must fail loudly, not silently."""
    registration = LANE_REGISTRY["climate-field-precipitation"]

    with pytest.raises(LaneRegistryError, match=re.escape("pipeline.direct.climate")):
        await registration.adapter(None, None, day=date(2026, 8, 20), run_id="generic")


def test_every_climate_slug_resolves_to_the_schema_the_writer_will_conform_to() -> None:
    """A slug the writer cannot resolve a schema for would fail on its first real write, not here."""
    for product in CLIMATE_FIELD_PRODUCTS:
        schema = get_stream_schema(product.stream)
        assert schema.name == product.stream
        assert schema is product.stream_schema


def test_the_executor_carries_one_shadow_climate_lane() -> None:
    spec = LANE_SPECS[CLIMATE_LANE_ID]

    assert spec.command == EXPECTED_COMMAND
    assert spec.schedule == EXPECTED_SCHEDULE
    assert spec.phase_offset_seconds == EXPECTED_PHASE_OFFSET_SECONDS
    assert spec.migration_disposition == "source-specific"
    assert spec.publication_lag_days == CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS
    assert spec.writer_floor == EXPECTED_WRITER_FLOOR
    assert spec.executable


def test_the_climate_lane_is_shadow_until_an_operator_names_it() -> None:
    """Shadow by construction: absent from the allow-list, the executor never runs it."""
    assert parse_activation({}).is_active(CLIMATE_LANE_ID) is False
    assert parse_activation({ACTIVE_LANES_VARIABLE: ""}).is_active(CLIMATE_LANE_ID) is False


def test_activating_the_climate_lane_uses_the_allow_list() -> None:
    activation = parse_activation({ACTIVE_LANES_VARIABLE: CLIMATE_LANE_ID})

    assert activation.is_active(CLIMATE_LANE_ID) is True


def test_the_command_timeout_is_the_cli_default_budget_plus_a_stated_grace() -> None:
    """A command timeout at or below the inner wall clock SIGKILLs a writer holding a session lock."""
    spec = LANE_SPECS[CLIMATE_LANE_ID]

    assert spec.command_timeout_seconds == int(CLIMATE_DEFAULT_TIME_BUDGET_SECONDS) + COMMAND_CLEANUP_MARGIN_SECONDS
    assert spec.command_timeout_seconds > CLIMATE_DEFAULT_TIME_BUDGET_SECONDS
    assert "--time-budget-seconds" not in (spec.command or ()), (
        "the executor passes no override, so the CLI default is the budget the derivation is against"
    )
