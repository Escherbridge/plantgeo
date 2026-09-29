"""Current lane catalogue, activation, and cadence behavior for the unified executor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agri_data_service.execution.job_executor_service import (
    ACTIVE_LANES_VARIABLE,
    LANE_SPECS,
    STOPPED_LANES_VARIABLE,
    ActivationConfig,
    ExecutorConfigurationError,
    executor_inventory,
    next_scheduled_bucket,
    parse_activation,
    scheduled_bucket,
)
from agri_data_service.execution.lane_catalogue import GAP_FILL_SUFFIX
from agri_data_service.foundation.lane_config import load_lane_configs
from agri_data_service.foundation.region import load_region
from tests.lane_config.builders import REAL_LANES_DIRECTORY

EXPECTED_SCHEDULES = {
    "fire-detections-direct-forward": "15 * * * *",
    "water-gauges-direct-forward": "15 * * * *",
    "mtbs-forward": "55 7 * * 2",
    "climate-nasa-power-direct-forward": "40 * * * *",
    "soil-era5-land-direct-forward": "50 */6 * * *",
    "vegetation-sentinel2-ndvi-direct-forward": "5 * * * *",
    "vegetation-ndvi-governed-plane-promotion": "25 * * * *",
    "weather-observations-direct-forward": "30 * * * *",
    "drought-direct-forward": "45 * * * *",
    "fire-perimeters-direct-forward": "10 * * * *",
    "sensors-direct-forward": "20 * * * *",
    "watersheds-direct-forward": "0 3 * * *",
    "evacuation-zones-direct-forward": "35 * * * *",
    "burn-severity-direct-forward": "55 8 * * *",
    "land-context-blm-forward": "0 9 * * *",
    "land-context-blm-reconcile": "0 11 * * *",
    "land-context-blm-backfill": "0 13 * * *",
    "crop-cover-usda-maintain": "0 10 * * *",
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


def test_unknown_lane_is_quarantined() -> None:
    """Rewritten for quarantine (spec Sec 4.9.3, plan 0W.5): an unknown allow-list id no longer exits
    the process -- it is quarantined, `active_lanes` never includes it, and every other listed lane
    still activates normally."""
    activation = parse_activation({ACTIVE_LANES_VARIABLE: "retired-lane,fire-detections-direct-forward"})
    assert activation.quarantined == frozenset({"retired-lane"})
    assert activation.active_lanes == frozenset({"fire-detections-direct-forward"})
    assert activation.is_active("fire-detections-direct-forward")
    assert not activation.is_active("retired-lane")


def _config_definition_ids() -> set[str]:
    """Every definition the real `lanes/` tree dispatches: each config lane, and its gap-fill when it declares one.

    Derived from the TOMLs, never pinned, so a disabled lane TOML (spec §4.4) needs no edit here.
    """
    lanes = load_lane_configs(REAL_LANES_DIRECTORY, load_region()).lanes.values()
    config = [lane for lane in lanes if lane.executor == "config"]
    gap_fills = {f"{lane.id}{GAP_FILL_SUFFIX}" for lane in config if lane.schedule.gap_fill_cron is not None}
    return {lane.id for lane in config} | gap_fills


def test_inventory_exposes_only_the_allow_list_the_kill_switch_and_current_lanes() -> None:
    inventory = executor_inventory(parse_activation({}))
    assert inventory["activation_variables"] == [ACTIVE_LANES_VARIABLE, STOPPED_LANES_VARIABLE]
    rows = inventory["lanes"]
    assert isinstance(rows, list)
    assert {row["lane_id"] for row in rows} == set(EXPECTED_SCHEDULES) | _config_definition_ids()


def test_the_soil_lane_opens_four_six_hourly_buckets_a_day_at_its_phase() -> None:
    """O6/FR-21: legacy soil runs at 00:50, 06:50, 12:50 and 18:50 UTC, on top of G0's per-run cap."""
    spec = LANE_SPECS["soil-era5-land-direct-forward"]
    day = datetime(2026, 9, 28, tzinfo=UTC)
    buckets = sorted({scheduled_bucket(spec, day + timedelta(minutes=minute)) for minute in range(0, 24 * 60, 10)})
    assert [bucket.strftime("%H:%M") for bucket in buckets] == ["18:50", "00:50", "06:50", "12:50", "18:50"]
    assert buckets[0].date() == (day - timedelta(days=1)).date(), "before 00:50 the lane is still in yesterday's"


def test_scheduled_bucket_uses_the_declared_phase_offset() -> None:
    spec = LANE_SPECS["weather-observations-direct-forward"]
    assert scheduled_bucket(spec, datetime(2026, 9, 12, 12, 44, tzinfo=UTC)) == datetime(
        2026, 9, 12, 12, 30, tzinfo=UTC
    )


def test_scheduled_bucket_requires_an_aware_clock() -> None:
    with pytest.raises(ExecutorConfigurationError, match="timezone"):
        scheduled_bucket(LANE_SPECS["sensors-direct-forward"], datetime.fromisoformat("2026-09-12T12:00:00"))


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
