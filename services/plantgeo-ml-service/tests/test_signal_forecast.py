"""The signal lane's 30-day Monte Carlo forecast, split out of agri-data-service's
`tests/parquet/test_signal_serving.py` when the forecasters moved here (track
`plantgeo_ml_service_20260918`, owner decision D2 2026-09-18).

The observed-side halves of that file stayed with the sibling service; this file is its forecast
section verbatim, with imports re-pointed at `plantgeo_ml_service.method.monte_carlo.signal`.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy
import pytest

from plantgeo_ml_service.method.monte_carlo.signal import (
    ERA5_LAND_PUBLICATION_LAG_DAYS,
    METHOD_NAME_ADDITIVE_ANOMALY,
    METHOD_NAME_EMPIRICAL_RESAMPLE,
    NASA_POWER_PUBLICATION_LAG_DAYS,
    PUBLISHED_QUANTILES,
    SURFACE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS,
    InsufficientSignalHistoryError,
    ObservedSignalDay,
    SignalSeriesSpecError,
    SimulationRequest,
    issued_on_for,
    series_spec_for,
    simulate_signal_forecast,
)

# === method/monte_carlo/signal.py: the 30-day forecast ===========================================

_SEASONAL_CYCLE_DAYS = 366


def _observed_temperature_series(  # noqa: PLR0913 - one argument per synthetic-series knob, all keyword-only
    *, start: date, days: int, seed: int, mean: float = 10.0, amplitude: float = 15.0, noise: float = 1.0
) -> tuple[ObservedSignalDay, ...]:
    """A smooth, strongly seasonal synthetic series -- the shape the additive-anomaly path expects."""
    rng = numpy.random.default_rng(seed)
    rows = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        day_of_year = day.timetuple().tm_yday
        seasonal = mean + amplitude * math.sin(2.0 * math.pi * (day_of_year - 80) / _SEASONAL_CYCLE_DAYS)
        value = float(seasonal + rng.normal(0.0, noise))
        rows.append(ObservedSignalDay(observed_day=day, value=value, observation_checksum=f"temp-{offset}"))
    return tuple(rows)


def _observed_humidity_series(*, start: date, days: int, seed: int) -> tuple[ObservedSignalDay, ...]:
    """A series that presses against both the 0 and 100 percent bounds, to prove clipping holds."""
    rng = numpy.random.default_rng(seed)
    rows = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        day_of_year = day.timetuple().tm_yday
        seasonal = 90.0 + 15.0 * math.sin(2.0 * math.pi * day_of_year / _SEASONAL_CYCLE_DAYS)
        value = float(seasonal + rng.normal(0.0, 12.0))
        rows.append(ObservedSignalDay(observed_day=day, value=value, observation_checksum=f"rh-{offset}"))
    return tuple(rows)


def _observed_precipitation_series(*, start: date, days: int, seed: int) -> tuple[ObservedSignalDay, ...]:
    """A zero-inflated, right-skewed synthetic series -- exactly the shape a symmetric bootstrap breaks on."""
    rng = numpy.random.default_rng(seed)
    rows = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        day_of_year = day.timetuple().tm_yday
        wet_probability = 0.3 + 0.2 * math.sin(2.0 * math.pi * day_of_year / _SEASONAL_CYCLE_DAYS)
        is_wet = rng.random() < wet_probability
        value = float(rng.exponential(4.0)) if is_wet else 0.0
        rows.append(ObservedSignalDay(observed_day=day, value=value, observation_checksum=f"precip-{offset}"))
    return tuple(rows)


HISTORY_START = date(2020, 1, 1)
HISTORY_DAYS = 1500
ISSUED_ON = date(2023, 6, 15)
RELATIVE_HUMIDITY_UPPER_BOUND_PERCENT = 100.0


class TestSignalMonteCarloForecast:
    def test_carries_all_six_provenance_columns(self) -> None:
        observations = _observed_temperature_series(start=HISTORY_START, days=HISTORY_DAYS, seed=1)
        request = SimulationRequest(horizon_days=14, simulation_count=300, seed=42)

        run = simulate_signal_forecast(
            spec=series_spec_for("air_temperature_mean"),
            observations=observations,
            issued_on=ISSUED_ON,
            request=request,
            series_key="cell-1:air_temperature_mean",
        )

        assert run.random_seed == request.seed
        assert run.ensemble_size == request.simulation_count
        assert run.horizon_days == request.horizon_days
        assert run.issued_on == ISSUED_ON
        sha256_hex_length = 64
        assert len(run.forecast_run_id) == sha256_hex_length
        assert {row.quantile for row in run.rows} == set(PUBLISHED_QUANTILES)
        assert len(run.rows) == request.horizon_days * len(PUBLISHED_QUANTILES)
        assert run.method_name == METHOD_NAME_ADDITIVE_ANOMALY

    def test_reproducible_given_the_same_seed_and_inputs(self) -> None:
        observations = _observed_temperature_series(start=HISTORY_START, days=HISTORY_DAYS, seed=1)
        request = SimulationRequest(horizon_days=10, simulation_count=200, seed=7)
        kwargs = {
            "spec": series_spec_for("air_temperature_mean"),
            "observations": observations,
            "issued_on": ISSUED_ON,
            "request": request,
            "series_key": "cell-1:air_temperature_mean",
        }

        first = simulate_signal_forecast(**kwargs)  # type: ignore[arg-type]
        second = simulate_signal_forecast(**kwargs)  # type: ignore[arg-type]

        assert first == second

    def test_a_different_seed_moves_the_forecast_run_id_and_the_draws(self) -> None:
        observations = _observed_temperature_series(start=HISTORY_START, days=HISTORY_DAYS, seed=1)
        spec = series_spec_for("air_temperature_mean")

        first = simulate_signal_forecast(
            spec=spec,
            observations=observations,
            issued_on=ISSUED_ON,
            request=SimulationRequest(horizon_days=10, simulation_count=200, seed=1),
            series_key="cell-1:air_temperature_mean",
        )
        second = simulate_signal_forecast(
            spec=spec,
            observations=observations,
            issued_on=ISSUED_ON,
            request=SimulationRequest(horizon_days=10, simulation_count=200, seed=2),
            series_key="cell-1:air_temperature_mean",
        )

        assert first.forecast_run_id != second.forecast_run_id
        assert first.rows != second.rows

    def test_quantiles_are_ordered_low_median_high_on_every_horizon_day(self) -> None:
        observations = _observed_temperature_series(start=HISTORY_START, days=HISTORY_DAYS, seed=3)
        request = SimulationRequest(horizon_days=14, simulation_count=500, seed=11)

        run = simulate_signal_forecast(
            spec=series_spec_for("air_temperature_mean"),
            observations=observations,
            issued_on=ISSUED_ON,
            request=request,
            series_key="cell-1:air_temperature_mean",
        )

        for step in range(1, request.horizon_days + 1):
            by_quantile = {row.quantile: row.value for row in run.rows if row.horizon_step == step}
            assert by_quantile[0.1] <= by_quantile[0.5] <= by_quantile[0.9]

    def test_additive_anomaly_draws_respect_the_series_declared_bounds(self) -> None:
        observations = _observed_humidity_series(start=HISTORY_START, days=HISTORY_DAYS, seed=4)
        spec = series_spec_for("relative_humidity")

        run = simulate_signal_forecast(
            spec=spec,
            observations=observations,
            issued_on=ISSUED_ON,
            request=SimulationRequest(horizon_days=14, simulation_count=500, seed=13),
            series_key="cell-1:relative_humidity",
        )

        assert all(0.0 <= row.value <= RELATIVE_HUMIDITY_UPPER_BOUND_PERCENT for row in run.rows)

    def test_precipitation_never_produces_a_negative_draw(self) -> None:
        """The design problem the layer-lanes contract calls out by name: no symmetric-bootstrap negatives."""
        observations = _observed_precipitation_series(start=HISTORY_START, days=HISTORY_DAYS, seed=5)
        spec = series_spec_for("precipitation")

        run = simulate_signal_forecast(
            spec=spec,
            observations=observations,
            issued_on=ISSUED_ON,
            request=SimulationRequest(horizon_days=30, simulation_count=2000, seed=17),
            series_key="cell-1:precipitation",
        )

        assert run.method_name == METHOD_NAME_EMPIRICAL_RESAMPLE
        assert all(row.value >= 0.0 for row in run.rows)
        # A meaningfully wet synthetic series should still draw SOME positive rainfall -- proving the
        # non-negativity is not merely because every draw collapsed to zero.
        assert any(row.value > 0.0 for row in run.rows)

    def test_a_forecast_never_sees_observations_after_its_own_issued_on(self) -> None:
        """Time-honesty, proven rather than asserted: identical history except for what comes after
        `issued_on` must produce an identical forecast."""
        full_history = _observed_temperature_series(start=HISTORY_START, days=HISTORY_DAYS, seed=6)
        truncated_history = tuple(row for row in full_history if row.observed_day <= ISSUED_ON)
        spec = series_spec_for("air_temperature_mean")
        request = SimulationRequest(horizon_days=10, simulation_count=200, seed=23)

        with_future = simulate_signal_forecast(
            spec=spec, observations=full_history, issued_on=ISSUED_ON, request=request, series_key="cell-1:x"
        )
        without_future = simulate_signal_forecast(
            spec=spec, observations=truncated_history, issued_on=ISSUED_ON, request=request, series_key="cell-1:x"
        )

        assert with_future == without_future

    def test_insufficient_history_is_refused_never_fabricated(self) -> None:
        sparse_history = _observed_temperature_series(start=date(2026, 1, 1), days=10, seed=8)
        spec = series_spec_for("air_temperature_mean")

        with pytest.raises(InsufficientSignalHistoryError):
            simulate_signal_forecast(
                spec=spec,
                observations=sparse_history,
                issued_on=date(2026, 1, 10),
                request=SimulationRequest(horizon_days=5, simulation_count=100, seed=1),
                series_key="cell-1:x",
            )

    def test_issued_on_respects_each_signals_own_producer_lag(self) -> None:
        today = date(2026, 8, 22)

        nasa_issued = issued_on_for(series_spec_for("air_temperature_mean"), today=today)
        era5_issued = issued_on_for(series_spec_for("vapor_pressure_deficit"), today=today)
        radiation_issued = issued_on_for(series_spec_for("surface_shortwave_radiation"), today=today)

        assert nasa_issued == today - timedelta(days=NASA_POWER_PUBLICATION_LAG_DAYS)
        assert era5_issued == today - timedelta(days=ERA5_LAND_PUBLICATION_LAG_DAYS)
        assert radiation_issued == today - timedelta(days=SURFACE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS)
        # The whole point of a per-signal lag: radiation must NOT inherit the blanket NASA constant.
        assert radiation_issued != nasa_issued

    def test_an_undeclared_signal_name_is_refused_by_name(self) -> None:
        with pytest.raises(SignalSeriesSpecError, match="not_a_real_signal"):
            series_spec_for("not_a_real_signal")
