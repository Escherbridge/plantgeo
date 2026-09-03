"""The eight climate registrations: their nature, their floors, their lags and their refusing adapter."""

from __future__ import annotations

import re
from datetime import date

import pytest

from agri_data_service.execution.job_executor_service import (
    ACTIVE_LANES_VARIABLE,
    COMMAND_CLEANUP_MARGIN_SECONDS,
    LANE_SPECS,
    ExecutorConfigurationError,
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
CLIMATE_STREAM_COUNT = 8
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


def test_the_executor_carries_one_shadow_climate_lane_with_no_legacy_owner() -> None:
    """Nothing ever produced a forward climate day, so there is no cutover to acknowledge."""
    spec = LANE_SPECS[CLIMATE_LANE_ID]

    assert spec.command == EXPECTED_COMMAND
    assert spec.legacy_owners == ()
    assert spec.required_handoff_acknowledgements == ()
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


def test_activating_the_climate_lane_needs_no_handoff_acknowledgement() -> None:
    """With no legacy owner there is nothing to disable first, and the gate must say so cleanly."""
    activation = parse_activation({ACTIVE_LANES_VARIABLE: CLIMATE_LANE_ID})

    assert activation.is_active(CLIMATE_LANE_ID) is True
    assert activation.handoff_acknowledgements == {}


def test_each_climate_stream_also_gets_its_generic_parquet_spec_and_it_stays_shadow() -> None:
    """The generic lane spec exists so the census sees the stream; its adapter still refuses."""
    for product in CLIMATE_FIELD_PRODUCTS:
        spec = LANE_SPECS[f"parquet-{product.stream}"]
        assert spec.publication_lag_days == product.publication_lag_days, product.stream
        assert spec.writer_ceiling is None, product.stream
        assert parse_activation({}).is_active(spec.lane_id) is False, product.stream


def test_the_direct_lane_and_its_eight_generic_specs_declare_each_other_as_conflicts() -> None:
    """Two owners of one calendar, one of which can only ever fail; the gate must refuse the pairing."""
    direct = LANE_SPECS[CLIMATE_LANE_ID]
    generic_ids = tuple(f"parquet-{product.stream}" for product in CLIMATE_FIELD_PRODUCTS)

    assert set(direct.conflicts_with) == set(generic_ids)
    for lane_id in generic_ids:
        assert LANE_SPECS[lane_id].conflicts_with == (CLIMATE_LANE_ID,), lane_id


@pytest.mark.parametrize("first", [True, False])
def test_activating_a_generic_climate_lane_beside_the_direct_writer_is_refused(first: bool) -> None:
    """Declared on both sides, so the refusal does not depend on which lane an operator names first."""
    generic = f"parquet-{CLIMATE_FIELD_PRODUCTS[0].stream}"
    pair = (CLIMATE_LANE_ID, generic) if first else (generic, CLIMATE_LANE_ID)

    with pytest.raises(ExecutorConfigurationError, match="conflicts with active lane"):
        parse_activation({ACTIVE_LANES_VARIABLE: ",".join(pair)})


def test_the_command_timeout_is_the_cli_default_budget_plus_a_stated_grace() -> None:
    """A command timeout at or below the inner wall clock SIGKILLs a writer holding a session lock."""
    spec = LANE_SPECS[CLIMATE_LANE_ID]

    assert spec.command_timeout_seconds == int(CLIMATE_DEFAULT_TIME_BUDGET_SECONDS) + COMMAND_CLEANUP_MARGIN_SECONDS
    assert spec.command_timeout_seconds > CLIMATE_DEFAULT_TIME_BUDGET_SECONDS
    assert "--time-budget-seconds" not in (spec.command or ()), (
        "the executor passes no override, so the CLI default is the budget the derivation is against"
    )


def test_no_climate_lane_joins_the_ingest_cron_atomic_cutover_group() -> None:
    """`plantgeo-ingest-cron` never produced a climate day, so it cannot be their legacy owner.

    Naming it would make the real ingest cutover drag along eight lanes whose registered adapter
    refuses by design, which is an invented dependency standing in for a handoff that never existed.
    """
    for product in CLIMATE_FIELD_PRODUCTS:
        assert LANE_SPECS[f"parquet-{product.stream}"].legacy_owners == (), product.stream
        assert LANE_SPECS[f"parquet-{product.stream}"].required_handoff_acknowledgements == (), product.stream
