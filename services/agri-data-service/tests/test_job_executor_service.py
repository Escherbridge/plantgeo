"""Current lane catalogue, activation, and cadence behavior for the unified executor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agri_data_service.execution.job_executor_service import (
    ACTIVE_LANES_VARIABLE,
    LANE_SPECS,
    ActivationConfig,
    ExecutorConfigurationError,
    executor_inventory,
    next_scheduled_bucket,
    parse_activation,
    scheduled_bucket,
)


EXPECTED_SCHEDULES = {
    "fire-detections-direct-forward": "15 * * * *",
    "water-gauges-direct-forward": "15 * * * *",
    "mtbs-forward": "55 7 * * 2",
    "climate-nasa-power-direct-forward": "40 * * * *",
    "soil-era5-land-direct-forward": "50 * * * *",
    "vegetation-sentinel2-ndvi-direct-forward": "5 * * * *",
    "weather-observations-direct-forward": "30 * * * *",
    "drought-direct-forward": "45 * * * *",
    "fire-perimeters-direct-forward": "10 * * * *",
    "sensors-direct-forward": "20 * * * *",
    "watersheds-direct-forward": "0 3 * * *",
    "evacuation-zones-direct-forward": "35 * * * *",
    "burn-severity-direct-forward": "55 8 * * *",
}


def test_catalogue_contains_only_current_source_direct_lanes() -> None:
    assert set(LANE_SPECS) == set(EXPECTED_SCHEDULES)
    assert {lane_id: spec.schedule for lane_id, spec in LANE_SPECS.items()} == EXPECTED_SCHEDULES
    assert all(spec.executable for spec in LANE_SPECS.values())
    assert not any(lane_id.startswith(("postgres-", "parquet-", "jobs-")) for lane_id in LANE_SPECS)


def test_empty_activation_keeps_every_lane_in_shadow() -> None:
    activation = parse_activation({})
    assert activation == ActivationConfig(frozenset())
    assert not any(activation.is_active(lane_id) for lane_id in LANE_SPECS)


def test_active_lane_allow_list_activates_selected_lanes() -> None:
    selected = {"fire-detections-direct-forward", "water-gauges-direct-forward"}
    activation = parse_activation({ACTIVE_LANES_VARIABLE: ",".join(sorted(selected))})
    assert activation.active_lanes == selected


def test_unknown_lane_is_rejected() -> None:
    with pytest.raises(ExecutorConfigurationError, match="unknown active lane"):
        parse_activation({ACTIVE_LANES_VARIABLE: "retired-lane"})


def test_inventory_exposes_only_the_allow_list_and_current_lanes() -> None:
    inventory = executor_inventory(parse_activation({}))
    assert inventory["activation_variables"] == [ACTIVE_LANES_VARIABLE]
    rows = inventory["lanes"]
    assert isinstance(rows, list)
    assert {row["lane_id"] for row in rows} == set(EXPECTED_SCHEDULES)


def test_scheduled_bucket_uses_the_declared_phase_offset() -> None:
    spec = LANE_SPECS["weather-observations-direct-forward"]
    assert scheduled_bucket(spec, datetime(2026, 9, 12, 12, 44, tzinfo=UTC)) == datetime(
        2026, 9, 12, 12, 30, tzinfo=UTC
    )


def test_scheduled_bucket_requires_an_aware_clock() -> None:
    with pytest.raises(ExecutorConfigurationError, match="timezone"):
        scheduled_bucket(LANE_SPECS["sensors-direct-forward"], datetime(2026, 9, 12, 12, 0))


def test_next_bucket_coalesces_incremental_downtime_to_current_bucket() -> None:
    spec = LANE_SPECS["fire-detections-direct-forward"]
    now = datetime(2026, 9, 12, 12, 20, tzinfo=UTC)
    previous = now - timedelta(days=2)
    assert next_scheduled_bucket(spec, now, previous) == datetime(2026, 9, 12, 12, 15, tzinfo=UTC)


def test_definitions_preserve_current_commands_and_schedule_metadata() -> None:
    for lane_id, expected_schedule in EXPECTED_SCHEDULES.items():
        spec = LANE_SPECS[lane_id]
        definition = spec.definition_spec()
        assert definition.schedule == expected_schedule
        assert definition.parameters["lane_id"] == lane_id
