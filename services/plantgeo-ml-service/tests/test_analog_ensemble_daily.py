"""The Analog Ensemble lane end to end: provenance on every row, every rung, one pointer advance."""

from __future__ import annotations

import io
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final

import polars as pl
import pyarrow.parquet as pq
import pytest
from test_covariate_vectors import CEILING, ISSUED_ON, MOMENT, PINNED_SIGNALS, observed_frame, observed_row
from test_forecast_lane_bootstrap import ForecastHarness, build_harness

from plantgeo_ml_service.foundation.parquet_paths import (
    ZOOM_TIERS,
    availability_bootstrap_marker_key,
    availability_lane_root,
    availability_pointer_path,
    completion_marker_path,
    partition_path,
)
from plantgeo_ml_service.method.ml.analog_ensemble import AnEnHyperparameters
from plantgeo_ml_service.pipeline.analog_ensemble_daily import (
    ANALOG_ENSEMBLE_HORIZON_DAYS,
    PUBLISHED_QUANTILES,
    AnalogEnsembleOptions,
    AnalogEnsembleRunReceipt,
    forecast_run_identity,
    write_analog_ensemble_run,
)
from plantgeo_ml_service.pipeline.covariate_vectors import (
    CovariateVectorError,
    build_covariate_matrices,
    covariate_window_bounds,
)
from plantgeo_ml_service.warehouse.lanes import settled_through
from plantgeo_ml_service.warehouse.streams import FORECAST_PROVENANCE_COLUMNS, SIGNAL_STREAM

if TYPE_CHECKING:
    from pathlib import Path

    from plantgeo_ml_service.pipeline.covariate_vectors import CovariateMatrix

RANDOM_SEED: Final = 20260919
HISTORY_DAYS: Final = 40
SHA256_HEX_LENGTH: Final = 64

#: A short horizon and a one-day exclusion keep the analog pool large enough for a 40-day fixture,
#: so every assertion below is about the lane rather than about the search having enough neighbours.
TEST_HYPERPARAMETERS: Final = AnEnHyperparameters(k_neighbors=5, temporal_exclusion_days=1, horizon_days=3)

TEST_OPTIONS: Final = AnalogEnsembleOptions(
    signal_order=PINNED_SIGNALS,
    hyperparameters=TEST_HYPERPARAMETERS,
    artifact_sha256="e" * 64,
    created_at=MOMENT,
)


def synthetic_matrices(*, day_count: int = HISTORY_DAYS) -> tuple[CovariateMatrix, ...]:
    """Build one cell's covariate matrix from `day_count` consecutive complete days."""
    days = [CEILING - timedelta(days=offset) for offset in reversed(range(day_count))]
    rows = [
        observed_row(day=day, signal_name="air_temperature_mean", value=10.0 + (index % 7))
        for index, day in enumerate(days)
    ] + [
        observed_row(day=day, signal_name="relative_humidity", value=40.0 + (index % 5) * 2.0)
        for index, day in enumerate(days)
    ]
    return build_covariate_matrices(observed_frame(rows), issued_on=ISSUED_ON, signal_order=PINNED_SIGNALS)


def run(tmp_path: Path) -> tuple[ForecastHarness, AnalogEnsembleRunReceipt]:
    """Run one issue day against a fresh harness and return the harness and its receipt."""
    harness = build_harness(tmp_path)
    receipt = write_analog_ensemble_run(
        harness.store,
        harness.pointers,
        synthetic_matrices(),
        issued_on=ISSUED_ON,
        random_seed=RANDOM_SEED,
        options=TEST_OPTIONS,
    )
    return harness, receipt


def read_rung(harness: ForecastHarness, *, day: date, zoom: int) -> pl.DataFrame:
    """Read one written rung back out of the bucket, as a reader would."""
    payload = harness.objects[partition_path(SIGNAL_STREAM, "forecast", zoom, day)]
    return pl.from_arrow(pq.read_table(io.BytesIO(payload)))


def test_the_run_writes_every_rung_of_the_ladder_for_every_valid_day(tmp_path: Path) -> None:
    harness, receipt = run(tmp_path)

    assert receipt.written_days
    for day in receipt.written_days:
        for zoom in ZOOM_TIERS:
            assert partition_path(SIGNAL_STREAM, "forecast", zoom, day) in harness.objects
            assert completion_marker_path(SIGNAL_STREAM, "forecast", zoom, day) in harness.objects


def test_every_provenance_column_is_non_null_on_every_row_of_every_rung(tmp_path: Path) -> None:
    """A column that is unconditionally null is a placeholder, not provenance."""
    harness, receipt = run(tmp_path)

    for day in receipt.written_days:
        for zoom in ZOOM_TIERS:
            frame = read_rung(harness, day=day, zoom=zoom)
            assert frame.height > 0
            for column in FORECAST_PROVENANCE_COLUMNS:
                assert frame.get_column(column).null_count() == 0


def test_the_seed_and_the_run_identity_are_recorded_on_every_row(tmp_path: Path) -> None:
    harness, receipt = run(tmp_path)
    expected_run_id = forecast_run_identity(
        artifact_sha256="e" * 64,
        issued_on=ISSUED_ON,
        random_seed=RANDOM_SEED,
    )

    frame = read_rung(harness, day=receipt.written_days[0], zoom=13)

    assert receipt.forecast_run_id == expected_run_id
    assert set(frame.get_column("random_seed").to_list()) == {RANDOM_SEED}
    assert set(frame.get_column("forecast_run_id").to_list()) == {expected_run_id}
    assert set(frame.get_column("quantile").to_list()) == set(PUBLISHED_QUANTILES)


def test_no_written_row_is_issued_from_a_day_past_the_settled_frontier(tmp_path: Path) -> None:
    harness, receipt = run(tmp_path)
    ceiling = settled_through(SIGNAL_STREAM, ISSUED_ON)

    for day in receipt.written_days:
        frame = read_rung(harness, day=day, zoom=13)
        assert max(frame.get_column("issued_on").to_list()) <= ceiling
        assert day > ceiling  # a forecast day is strictly beyond the frontier it was issued from


def test_the_cell_identity_is_propagated_verbatim_at_the_base_rung(tmp_path: Path) -> None:
    harness, receipt = run(tmp_path)

    frame = read_rung(harness, day=receipt.written_days[0], zoom=13)

    assert frame.get_column("cell_id").null_count() == 0
    assert set(frame.get_column("cell_id").to_list()) == {"cell-a"}
    assert set(frame.get_column("cell_longitude").to_list()) == {-120.25}


def test_identical_inputs_write_byte_identical_partitions(tmp_path: Path) -> None:
    """NFR 1: same artifact, same inputs, same seed, same bytes -- or the receipts prove nothing."""
    first_harness, first_receipt = run(tmp_path / "first")
    second_harness, second_receipt = run(tmp_path / "second")

    assert first_receipt.forecast_run_id == second_receipt.forecast_run_id
    assert first_harness.objects == second_harness.objects


def test_the_lane_is_bootstrapped_and_its_pointer_is_advanced_and_readable_back(tmp_path: Path) -> None:
    harness, receipt = run(tmp_path)
    lane_root = availability_lane_root(SIGNAL_STREAM, "forecast")

    assert availability_bootstrap_marker_key(lane_root) in harness.objects
    assert receipt.publication is not None
    assert receipt.publication.outcome == "advanced"
    pointer_key = harness.store.absolute_key(availability_pointer_path(SIGNAL_STREAM, "forecast"))
    stored = harness.pointers.read_pointer(pointer_key)
    assert stored is not None
    assert receipt.publication.generation_key in stored.payload.decode("utf-8")
    assert receipt.publication.generation_key in harness.objects


def test_the_generation_indexes_every_rung_of_every_published_day(tmp_path: Path) -> None:
    _harness, receipt = run(tmp_path)

    published = receipt.publication
    assert published.rows == len(receipt.written_days) * len(ZOOM_TIERS)
    assert published.earliest_terminal_day == min(receipt.written_days)
    assert published.latest_terminal_day == max(receipt.written_days)


def test_insufficient_history_refuses_with_a_reason_and_publishes_governed_absences(tmp_path: Path) -> None:
    """A forecaster that cannot honestly forecast produces a refusal receipt, never a fabricated row.

    M2: it also publishes, because "refused" and "never ran" must not be the same silence in the
    index. Every day of the horizon is indexed as a governed absence and NO partition is written.
    """
    harness = build_harness(tmp_path)

    receipt = write_analog_ensemble_run(
        harness.store,
        harness.pointers,
        synthetic_matrices(day_count=TEST_HYPERPARAMETERS.horizon_days),
        issued_on=ISSUED_ON,
        random_seed=RANDOM_SEED,
        options=TEST_OPTIONS,
    )

    assert receipt.written_days == ()
    assert receipt.row_count == 0
    assert {refusal.reason for refusal in receipt.refusals} == {"insufficient_history"}
    assert not any(key.endswith(".parquet") for key in harness.objects if "/availability/" not in key)

    assert receipt.publication is not None
    assert receipt.publication.outcome == "advanced"
    assert len(receipt.absent_days) == TEST_HYPERPARAMETERS.horizon_days
    assert receipt.absent_days[0] == settled_through(SIGNAL_STREAM, ISSUED_ON) + timedelta(days=1)


def test_a_cell_with_no_rows_for_a_pinned_signal_is_refused_not_invented(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)
    matrices = synthetic_matrices()

    receipt = write_analog_ensemble_run(
        harness.store,
        harness.pointers,
        matrices,
        issued_on=ISSUED_ON,
        random_seed=RANDOM_SEED,
        options=AnalogEnsembleOptions(
            signal_order=(*PINNED_SIGNALS, "wind_speed"),
            hyperparameters=TEST_HYPERPARAMETERS,
            artifact_sha256="e" * 64,
            created_at=MOMENT,
        ),
    )

    assert "series_absent" in {refusal.reason for refusal in receipt.refusals}
    assert receipt.row_count > 0


def test_the_run_identity_changes_with_the_seed() -> None:
    first = forecast_run_identity(artifact_sha256="e" * 64, issued_on=ISSUED_ON, random_seed=1)
    second = forecast_run_identity(artifact_sha256="e" * 64, issued_on=ISSUED_ON, random_seed=2)

    assert first != second
    assert len(first) == len(second) == SHA256_HEX_LENGTH


def test_the_shipped_defaults_are_the_horizon_and_quantiles_the_spec_names() -> None:
    assert PUBLISHED_QUANTILES == (0.1, 0.5, 0.9)
    assert AnalogEnsembleOptions().resolved_hyperparameters().horizon_days == ANALOG_ENSEMBLE_HORIZON_DAYS
    with pytest.raises(CovariateVectorError, match="at least"):
        covariate_window_bounds(issued_on=ISSUED_ON, history_days=AnalogEnsembleOptions(history_days=1).history_days)
