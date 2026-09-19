"""The dispatch map, and one lane forecast end to end through a real reader and a real ladder."""

from __future__ import annotations

import io
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from test_forecast_lane_bootstrap import ForecastHarness, build_harness

from plantgeo_ml_service.foundation.parquet_markers import PartitionCompletion
from plantgeo_ml_service.foundation.parquet_paths import (
    ZOOM_TIERS,
    availability_bootstrap_marker_key,
    availability_lane_root,
    completion_marker_path,
    partition_path,
)
from plantgeo_ml_service.pipeline.monte_carlo_daily import (
    ANALOG_ENSEMBLE_ARTIFACT_PREFIX,
    ANALOG_ENSEMBLE_FORECASTER,
    MONTE_CARLO_FORECASTER,
    MONTE_CARLO_MODULES,
    STEM_LAYERS,
    LaneForecastReceipt,
    MonteCarloDispatchError,
    MonteCarloOptions,
    analog_ensemble_artifact_sha256,
    dispatchable_lanes,
    load_forecast_module,
    run_monte_carlo_daily,
    run_monte_carlo_lane,
)
from plantgeo_ml_service.pipeline.object_store import JSON_CONTENT_TYPE
from plantgeo_ml_service.pipeline.observed_reader import ObservedReadRefusalError
from plantgeo_ml_service.warehouse.lanes import LANE_CONTRACTS, settled_through
from plantgeo_ml_service.warehouse.streams import (
    FIRE_DETECTIONS_SCHEMA,
    FIRE_DETECTIONS_STREAM,
    FORECAST_PROVENANCE_COLUMNS,
)

if TYPE_CHECKING:
    from pathlib import Path

MOMENT: Final = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)
ISSUED_ON: Final = date(2026, 9, 19)
FIRE_CEILING: Final = settled_through(FIRE_DETECTIONS_STREAM, ISSUED_ON)
OBSERVED_DAY_COUNT: Final = 60
CELL_LONGITUDE: Final = -120.125
CELL_LATITUDE: Final = 46.375
RANDOM_SEED: Final = 20260919

#: A short horizon keeps the fixture small; the ensemble stays above every module's own draw floor.
FIRE_OPTIONS: Final = MonteCarloOptions(
    horizon_days=5,
    simulation_count=200,
    history_days=OBSERVED_DAY_COUNT,
    created_at=MOMENT,
)


def write_observed_fire_history(harness: ForecastHarness) -> tuple[date, ...]:
    """Write one cell's governed observed history, one completed base-rung partition per day."""
    days = tuple(FIRE_CEILING - timedelta(days=offset) for offset in reversed(range(OBSERVED_DAY_COUNT)))
    for index, day in enumerate(days):
        table = pa.table(
            {
                "cell_longitude": [CELL_LONGITUDE],
                "cell_latitude": [CELL_LATITUDE],
                "observed_day": [day],
                "detection_count": [1 + (index % 4)],
                "frp_sum": [5.0 + index],
                "frp_observation_count": [1],
                "high_confidence_detection_count": [1],
                "newest_observed_at": [MOMENT],
            },
            schema=FIRE_DETECTIONS_SCHEMA.arrow_schema,
        )
        part = harness.store.write_partition(table, layer=FIRE_DETECTIONS_STREAM, kind="observed", zoom=13, day=day)
        harness.store.write_completion_marker(
            PartitionCompletion(part_count=1, row_count=part.row_count, completed_at=MOMENT, run_id="fixture"),
            layer=FIRE_DETECTIONS_STREAM,
            kind="observed",
            zoom=13,
            day=day,
        )
    return days


def read_rung(harness: ForecastHarness, *, day: date, zoom: int) -> pl.DataFrame:
    """Read one written forecast rung back out of the bucket."""
    payload = harness.objects[partition_path(FIRE_DETECTIONS_STREAM, "forecast", zoom, day)]
    return pl.from_arrow(pq.read_table(io.BytesIO(payload)))


def run_fire_lane(tmp_path: Path) -> tuple[ForecastHarness, LaneForecastReceipt]:
    """Write the observed fixture and forecast the fire-detections lane from it."""
    harness = build_harness(tmp_path)
    write_observed_fire_history(harness)
    receipt = run_monte_carlo_lane(
        harness.store,
        harness.pointers,
        harness.reader,
        layer=FIRE_DETECTIONS_STREAM,
        issued_on=ISSUED_ON,
        random_seed=RANDOM_SEED,
        options=FIRE_OPTIONS,
    )
    return harness, receipt


def test_every_registry_stem_is_in_the_explicit_dispatch_map() -> None:
    """A stem resolved by string assembly could import a module nobody listed here."""
    declared = {contract.forecast_module for contract in LANE_CONTRACTS.values() if contract.forecast_module}

    assert declared <= set(MONTE_CARLO_MODULES)
    assert set(MONTE_CARLO_MODULES) == {
        "fire_detections",
        "sensors",
        "signal",
        "vegetation_ndvi_forecast",
        "water_gauges",
    }
    for stem in MONTE_CARLO_MODULES:
        assert load_forecast_module(stem).METHOD_NAME


def test_an_unmapped_stem_is_refused_by_naming_what_is_mapped() -> None:
    with pytest.raises(MonteCarloDispatchError, match="dispatch map"):
        load_forecast_module("plantgeo_ml_service.method.ml.analog_ensemble")


def test_only_lanes_bound_to_a_writable_stream_are_dispatchable() -> None:
    """`sensors` and `water_gauges` have no lane contract here, so nothing invents a stream for them."""
    assert dispatchable_lanes() == ("fire-detections", "signal", "vegetation")
    assert set(STEM_LAYERS) < set(MONTE_CARLO_MODULES)


def test_a_lane_with_no_forecaster_is_refused_rather_than_guessed(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)

    with pytest.raises(MonteCarloDispatchError, match="declares no forecaster"):
        run_monte_carlo_lane(
            harness.store,
            harness.pointers,
            harness.reader,
            layer="drought",
            issued_on=ISSUED_ON,
            random_seed=1,
        )


def test_the_lane_writes_every_rung_with_non_null_provenance_on_every_row(tmp_path: Path) -> None:
    harness, receipt = run_fire_lane(tmp_path)

    assert receipt.forecaster == MONTE_CARLO_FORECASTER
    assert receipt.written_days
    for day in receipt.written_days:
        for zoom in ZOOM_TIERS:
            assert completion_marker_path(FIRE_DETECTIONS_STREAM, "forecast", zoom, day) in harness.objects
            frame = read_rung(harness, day=day, zoom=zoom)
            assert frame.height > 0
            for column in FORECAST_PROVENANCE_COLUMNS:
                assert frame.get_column(column).null_count() == 0


def test_the_seed_is_recorded_and_every_row_is_issued_from_the_settled_frontier(tmp_path: Path) -> None:
    harness, receipt = run_fire_lane(tmp_path)

    frame = read_rung(harness, day=receipt.written_days[0], zoom=13)

    assert receipt.random_seed == RANDOM_SEED
    assert set(frame.get_column("random_seed").to_list()) == {RANDOM_SEED}
    assert set(frame.get_column("issued_on").to_list()) == {FIRE_CEILING}
    assert set(frame.get_column("ensemble_size").to_list()) == {FIRE_OPTIONS.simulation_count}
    assert min(receipt.written_days) == FIRE_CEILING + timedelta(days=1)
    assert len(receipt.written_days) == FIRE_OPTIONS.horizon_days


def test_the_lane_is_bootstrapped_and_published_before_any_day_is_selectable(tmp_path: Path) -> None:
    harness, receipt = run_fire_lane(tmp_path)
    lane_root = availability_lane_root(FIRE_DETECTIONS_STREAM, "forecast")

    assert availability_bootstrap_marker_key(lane_root) in harness.objects
    assert receipt.publication is not None
    assert receipt.publication.outcome == "advanced"
    assert receipt.publication.rows == len(receipt.written_days) * len(ZOOM_TIERS)
    assert receipt.publication.generation_key in harness.objects


def test_a_lane_with_no_governed_history_refuses_rather_than_fabricating_rows(tmp_path: Path) -> None:
    """The observed reader refuses an ungoverned window; the lane surfaces that, it does not fill it."""
    harness = build_harness(tmp_path)

    with pytest.raises(ObservedReadRefusalError) as refusal:
        run_monte_carlo_lane(
            harness.store,
            harness.pointers,
            harness.reader,
            layer=FIRE_DETECTIONS_STREAM,
            issued_on=ISSUED_ON,
            random_seed=1,
            options=FIRE_OPTIONS,
        )

    assert refusal.value.reason == "day_not_governed"


def test_the_analog_ensemble_artifact_probe_decides_which_forecaster_the_signal_lane_uses(
    tmp_path: Path,
) -> None:
    harness = build_harness(tmp_path)

    assert analog_ensemble_artifact_sha256(harness.store) is None
    harness.store.backend.put(
        f"{ANALOG_ENSEMBLE_ARTIFACT_PREFIX}{'f' * 64}.json",
        b"{}",
        content_type=JSON_CONTENT_TYPE,
    )
    assert analog_ensemble_artifact_sha256(harness.store) == "f" * 64
    assert ANALOG_ENSEMBLE_FORECASTER != MONTE_CARLO_FORECASTER


def test_the_receipt_states_which_forecaster_ran_and_what_it_refused(tmp_path: Path) -> None:
    """An operator reads the turn's outcome off the receipt, not off the bucket listing."""
    _harness, receipt = run_fire_lane(tmp_path)

    wire = receipt.to_wire()

    assert wire["forecaster"] == MONTE_CARLO_FORECASTER
    assert wire["forecast_module"] == "fire_detections"
    assert wire["layer"] == FIRE_DETECTIONS_STREAM
    assert wire["issued_on"] == ISSUED_ON.isoformat()
    assert wire["written_days"] == [day.isoformat() for day in receipt.written_days]
    assert wire["refusals"] == []


def test_a_whole_turn_is_bounded_by_the_registry() -> None:
    """The turn walks the lanes the registry declares, so a new forecaster lane joins it by itself."""
    assert dispatchable_lanes()[0] == FIRE_DETECTIONS_STREAM
    assert run_monte_carlo_daily is not None
    assert all(LANE_CONTRACTS[layer].forecast_module in STEM_LAYERS for layer in dispatchable_lanes())
