"""The eight soil registrations: their nature, their floors, their lag and their refusing adapter."""

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
from agri_data_service.parquet_ops.snapshot_products import PRODUCT_BY_LAYER
from agri_data_service.pipeline.direct.soil.products import (
    ERA5_LAND_ARCHIVE_PUBLICATION_LAG_DAYS,
    ERA5_LAND_SNAPSHOT_LAST_DAY,
    SOIL_DEFAULT_TIME_BUDGET_SECONDS,
    SOIL_DIRECT_WRITER_START_DAY,
    SOIL_FIELD_PRODUCTS,
)
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY, LaneRegistryError
from agri_data_service.warehouse.parquet.schema import get_stream_schema

SOIL_LANE_ID = "soil-era5-land-direct-forward"
CLIMATE_LANE_ID = "climate-nasa-power-direct-forward"
SOIL_STREAM_COUNT = 8
EXPECTED_COMMAND = ("python", "-m", "agri_data_service.pipeline.direct.soil")
EXPECTED_SCHEDULE = "50 * * * *"
EXPECTED_PHASE_OFFSET_SECONDS = 3000
EXPECTED_WRITER_FLOOR = "2026-08-03"
EXPECTED_CADENCE_DAYS = 1
#: The five soil products the snapshot manifest actually holds. The three `soil-field-moisture-*`
#: streams are dedicated slider prefixes rather than snapshot products (`parquet_ops/coverage.py`
#: DEDICATED_SLIDER_PRODUCT_LAYERS), so they have no descriptor to carry a forward edge.
SNAPSHOT_ROOTED_SOIL_LAYERS = (
    "soil-field-vpd",
    "soil-temperature-0-to-7cm",
    "soil-temperature-7-to-28cm",
    "soil-temperature-28-to-100cm",
    "soil-temperature-100-to-255cm",
)


def test_every_soil_product_is_a_registered_lane_with_a_time_axis() -> None:
    """A stream with no registration is one nothing schedules and nothing ever reports on."""
    assert len(SOIL_FIELD_PRODUCTS) == SOIL_STREAM_COUNT

    for product in SOIL_FIELD_PRODUCTS:
        registration = LANE_REGISTRY[product.stream]
        assert registration.nature == "daily_series", product.stream
        assert registration.history_floor == SOIL_DIRECT_WRITER_START_DAY, product.stream
        assert registration.publication_lag_days == ERA5_LAND_ARCHIVE_PUBLICATION_LAG_DAYS, product.stream
        assert registration.cadence_days == EXPECTED_CADENCE_DAYS, product.stream
        assert registration.watermark is None, product.stream
        assert registration.forecast_module is None, product.stream
        assert registration.forecastable is False, product.stream
        assert registration.writer_ceiling is None, product.stream


def test_all_eight_streams_share_one_floor_and_one_clock() -> None:
    """One upstream on one release schedule: unlike POWER's two clocks, a turn never straddles two edges."""
    assert {product.snapshot_last_day for product in SOIL_FIELD_PRODUCTS} == {ERA5_LAND_SNAPSHOT_LAST_DAY}
    assert {product.publication_lag_days for product in SOIL_FIELD_PRODUCTS} == {ERA5_LAND_ARCHIVE_PUBLICATION_LAG_DAYS}
    assert date(2026, 8, 3) == SOIL_DIRECT_WRITER_START_DAY


def test_every_soil_floor_cites_the_snapshot_and_the_measured_redistributor_lag() -> None:
    """An uncited floor is a guess that reads as a measurement; the citation is the guard."""
    for product in SOIL_FIELD_PRODUCTS:
        basis = LANE_REGISTRY[product.stream].floor_basis
        assert "daily_series" in basis, product.stream
        assert "SOURCE-DIRECT" in basis, product.stream
        assert ERA5_LAND_SNAPSHOT_LAST_DAY.isoformat() in basis, product.stream
        assert "coverage_census.py" in basis, product.stream
        assert "REDISTRIBUTOR" in basis, product.stream


@pytest.mark.asyncio
async def test_the_registered_adapter_refuses_and_names_the_soil_writer_not_the_climate_one() -> None:
    """Two direct writers now; an operator sent to the wrong module gets a run that explains nothing."""
    registration = LANE_REGISTRY["soil-field-vpd"]

    with pytest.raises(LaneRegistryError, match=re.escape("pipeline.direct.soil")):
        await registration.adapter(None, None, day=date(2026, 8, 20), run_id="generic")


@pytest.mark.asyncio
async def test_the_climate_registration_still_names_the_climate_writer() -> None:
    """The refusal factory must not have collapsed both writers onto one message."""
    registration = LANE_REGISTRY["soil-wetness-surface"]

    with pytest.raises(LaneRegistryError, match=re.escape("pipeline.direct.climate")):
        await registration.adapter(None, None, day=date(2026, 8, 20), run_id="generic")


def test_every_soil_slug_resolves_to_the_schema_the_writer_will_conform_to() -> None:
    """A slug the writer cannot resolve a schema for would fail on its first real write, not here."""
    for product in SOIL_FIELD_PRODUCTS:
        schema = get_stream_schema(product.stream)
        assert schema.name == product.stream
        assert schema is product.stream_schema


def test_the_five_snapshot_rooted_soil_products_route_forward_days_through_the_lane() -> None:
    """Without the edge these products go on reporting 2026-08-02 while the bucket grows past it."""
    for layer in SNAPSHOT_ROOTED_SOIL_LAYERS:
        assert PRODUCT_BY_LAYER[layer].forward_first_day == SOIL_DIRECT_WRITER_START_DAY, layer


def test_the_three_moisture_streams_are_lanes_rather_than_snapshot_products() -> None:
    """They are already served through the ordinary lane path, so there is no descriptor to change."""
    for product in SOIL_FIELD_PRODUCTS:
        if product.product_id == "moisture":
            assert product.stream not in PRODUCT_BY_LAYER, product.stream


def test_the_executor_carries_one_shadow_soil_lane_with_no_legacy_owner() -> None:
    """Nothing ever produced a forward ERA5-Land day, so there is no cutover to acknowledge."""
    spec = LANE_SPECS[SOIL_LANE_ID]

    assert spec.command == EXPECTED_COMMAND
    assert spec.legacy_owners == ()
    assert spec.required_handoff_acknowledgements == ()
    assert spec.schedule == EXPECTED_SCHEDULE
    assert spec.phase_offset_seconds == EXPECTED_PHASE_OFFSET_SECONDS
    assert spec.phase_offset_seconds != LANE_SPECS[CLIMATE_LANE_ID].phase_offset_seconds
    assert spec.migration_disposition == "source-specific"
    assert spec.publication_lag_days == ERA5_LAND_ARCHIVE_PUBLICATION_LAG_DAYS
    assert spec.writer_floor == EXPECTED_WRITER_FLOOR
    assert spec.executable


def test_the_soil_lane_is_shadow_until_an_operator_names_it() -> None:
    """Shadow by construction: absent from the allow-list, the executor never runs it."""
    assert parse_activation({}).is_active(SOIL_LANE_ID) is False
    assert parse_activation({ACTIVE_LANES_VARIABLE: ""}).is_active(SOIL_LANE_ID) is False


def test_activating_the_soil_lane_needs_no_handoff_acknowledgement() -> None:
    """With no legacy owner there is nothing to disable first, and the gate must say so cleanly."""
    activation = parse_activation({ACTIVE_LANES_VARIABLE: SOIL_LANE_ID})

    assert activation.is_active(SOIL_LANE_ID) is True
    assert activation.handoff_acknowledgements == {}


def test_each_soil_stream_also_gets_its_generic_parquet_spec_and_it_stays_shadow() -> None:
    """The generic lane spec exists so the census sees the stream; its adapter still refuses."""
    for product in SOIL_FIELD_PRODUCTS:
        spec = LANE_SPECS[f"parquet-{product.stream}"]
        assert spec.publication_lag_days == product.publication_lag_days, product.stream
        assert spec.writer_ceiling is None, product.stream
        assert spec.legacy_owners == (), product.stream
        assert spec.required_handoff_acknowledgements == (), product.stream
        assert parse_activation({}).is_active(spec.lane_id) is False, product.stream


def test_the_soil_lane_and_its_eight_generic_specs_declare_each_other_as_conflicts() -> None:
    """Two owners of one calendar, one of which can only ever fail; the gate must refuse the pairing."""
    direct = LANE_SPECS[SOIL_LANE_ID]
    generic_ids = tuple(f"parquet-{product.stream}" for product in SOIL_FIELD_PRODUCTS)

    assert set(direct.conflicts_with) == set(generic_ids)
    for lane_id in generic_ids:
        assert LANE_SPECS[lane_id].conflicts_with == (SOIL_LANE_ID,), lane_id


def test_no_soil_generic_spec_conflicts_with_the_climate_writer() -> None:
    """The conflict is per WRITER: naming the climate lane here would refuse a pairing that is fine."""
    for product in SOIL_FIELD_PRODUCTS:
        assert CLIMATE_LANE_ID not in LANE_SPECS[f"parquet-{product.stream}"].conflicts_with, product.stream
    assert set(LANE_SPECS[CLIMATE_LANE_ID].conflicts_with).isdisjoint(LANE_SPECS[SOIL_LANE_ID].conflicts_with)


def test_the_two_direct_writers_may_activate_together() -> None:
    """They share no lane and no calendar, so nothing may stop an operator running both."""
    activation = parse_activation({ACTIVE_LANES_VARIABLE: f"{SOIL_LANE_ID},{CLIMATE_LANE_ID}"})

    assert activation.active_lanes == frozenset({SOIL_LANE_ID, CLIMATE_LANE_ID})


@pytest.mark.parametrize("first", [True, False])
def test_activating_a_generic_soil_lane_beside_the_direct_writer_is_refused(first: bool) -> None:
    """Declared on both sides, so the refusal does not depend on which lane an operator names first."""
    generic = f"parquet-{SOIL_FIELD_PRODUCTS[0].stream}"
    pair = (SOIL_LANE_ID, generic) if first else (generic, SOIL_LANE_ID)

    with pytest.raises(ExecutorConfigurationError, match="conflicts with active lane"):
        parse_activation({ACTIVE_LANES_VARIABLE: ",".join(pair)})


def test_the_command_timeout_is_the_cli_default_budget_plus_a_stated_grace() -> None:
    """A command timeout at or below the inner wall clock SIGKILLs a writer holding a session lock."""
    spec = LANE_SPECS[SOIL_LANE_ID]

    assert spec.command_timeout_seconds == int(SOIL_DEFAULT_TIME_BUDGET_SECONDS) + COMMAND_CLEANUP_MARGIN_SECONDS
    assert spec.command_timeout_seconds > SOIL_DEFAULT_TIME_BUDGET_SECONDS
    assert "--time-budget-seconds" not in (spec.command or ()), (
        "the executor passes no override, so the CLI default is the budget the derivation is against"
    )
