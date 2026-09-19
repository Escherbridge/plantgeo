"""Forecast-row provenance for the vegetation NDVI seasonal-anomaly forecaster.

Split out of agri-data-service's `tests/parquet/test_vegetation_serving.py` when the forecasters
moved here (track `plantgeo_ml_service_20260918`, owner decision D2 2026-09-18). The serving-read
and source-reconciliation sections stayed with the sibling; this file is the provenance section
verbatim, with imports re-pointed at `plantgeo_ml_service.method.monte_carlo`.

`AUGUST_FIRST` and `EXPECTED_QUANTILE_ROWS_PER_STEP` are duplicated from the sibling's fixture
block rather than imported: the two services deploy independently and never import each other.
"""

from __future__ import annotations

from datetime import date

import pytest

from plantgeo_ml_service.method.monte_carlo.vegetation_ndvi_forecast import (
    DAYS_PER_SEASONAL_CYCLE,
    HIGH_QUANTILE,
    LOW_QUANTILE,
    MEDIAN_QUANTILE,
    HorizonQuantiles,
    ProvenancedForecastRow,
    SeasonalHistory,
    SimulationRequest,
    provenanced_forecast_rows,
)

AUGUST_FIRST = date(2026, 8, 1)
EXPECTED_QUANTILE_ROWS_PER_STEP = 3


# --- method/monte_carlo/vegetation_ndvi_forecast.py: forecast-row provenance ---------------------


def _sample_history(cutoff_day: date) -> SeasonalHistory:
    return SeasonalHistory(
        cutoff_day=cutoff_day,
        history_start_day=date(2022, 8, 5),
        governed_day_count=40,
        training_day_count=36,
        climatology_by_day_of_year=tuple(0.5 for _ in range(DAYS_PER_SEASONAL_CYCLE)),
        climatology_sample_counts=tuple(4 for _ in range(DAYS_PER_SEASONAL_CYCLE)),
        anomaly_values=(0.01, -0.02, 0.03),
        anomaly_days_of_year=(200, 201, 202),
        latest_observed_day=cutoff_day,
        latest_observed_value=0.55,
        anchor_day=cutoff_day,
        anchor_anomaly=0.02,
        lag_one_autocorrelation=0.4,
        autocorrelation_pair_count=10,
        mean_observation_gap_days=6.0,
        daily_persistence=0.9,
    )


def test_provenanced_forecast_rows_carries_every_contract_column() -> None:
    history = _sample_history(AUGUST_FIRST)
    quantiles = (
        HorizonQuantiles(
            horizon_step=1,
            valid_day=date(2026, 8, 2),
            low_value=0.4,
            median_value=0.5,
            high_value=0.6,
            innovation_pool_size=5,
        ),
        HorizonQuantiles(
            horizon_step=2,
            valid_day=date(2026, 8, 3),
            low_value=0.41,
            median_value=0.51,
            high_value=0.61,
            innovation_pool_size=5,
        ),
    )
    request = SimulationRequest(horizon_days=2, simulation_count=1000, seed=42)
    forecast_run_id = "a" * 64

    rows = provenanced_forecast_rows(
        history=history, quantiles=quantiles, request=request, forecast_run_id=forecast_run_id
    )

    assert len(rows) == len(quantiles) * EXPECTED_QUANTILE_ROWS_PER_STEP
    assert all(isinstance(row, ProvenancedForecastRow) for row in rows)
    for row in rows:
        assert row.forecast_run_id == forecast_run_id
        assert row.random_seed == request.seed
        assert row.ensemble_size == request.simulation_count
        assert row.issued_on == history.cutoff_day
        assert row.quantile in (LOW_QUANTILE, MEDIAN_QUANTILE, HIGH_QUANTILE)

    step_one = {row.quantile: row.metric_value for row in rows if row.horizon_days == 1}
    assert step_one == {LOW_QUANTILE: 0.4, MEDIAN_QUANTILE: 0.5, HIGH_QUANTILE: 0.6}


def test_provenanced_forecast_rows_rejects_a_blank_run_id() -> None:
    history = _sample_history(AUGUST_FIRST)
    request = SimulationRequest(horizon_days=1, simulation_count=1000, seed=1)

    with pytest.raises(ValueError, match="non-blank"):
        provenanced_forecast_rows(history=history, quantiles=(), request=request, forecast_run_id="   ")
