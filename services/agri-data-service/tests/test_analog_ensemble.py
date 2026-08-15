"""Database-free proofs for the Analog Ensemble (AnEn) pure k-NN forecaster arithmetic."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from agri_data_service.method.ml.analog_ensemble import (
    METHOD_NAME,
    AnEnHyperparameters,
    find_analogs,
    generate_anen_forecast,
)

HISTORY_DAY_COUNT = 60
FIRST_DAY = date(2025, 1, 1)
QUERY_DAY_OUTSIDE_HISTORY = date(2025, 3, 15)
NEAREST_INDEX = 55
SINGLE_ANALOG_HORIZON = 3


def _history() -> tuple[np.ndarray, tuple[date, ...]]:
    """A 60-day, single-feature history whose feature value equals the day's index."""
    dates = tuple(FIRST_DAY + timedelta(days=offset) for offset in range(HISTORY_DAY_COUNT))
    matrix = np.arange(HISTORY_DAY_COUNT, dtype=float).reshape(-1, 1)
    return matrix, dates


def _target_series(dates: tuple[date, ...]) -> dict[date, float]:
    """One target value per history day, ten times its index -- easy to hand-verify."""
    return {day: float(index) * 10.0 for index, day in enumerate(dates)}


def test_find_analogs_raises_on_an_empty_history_matrix() -> None:
    with pytest.raises(ValueError, match="empty history matrix"):
        find_analogs(np.array([1.0]), np.empty((0, 1)), (), date(2025, 1, 1), horizon_days=0, k=1)


def test_find_analogs_refuses_a_negative_horizon() -> None:
    matrix, dates = _history()

    with pytest.raises(ValueError, match="horizon_days must not be negative"):
        find_analogs(
            query_vector=np.array([30.0]),
            history_matrix=matrix,
            history_dates=dates,
            query_date=dates[30],
            horizon_days=-1,
            k=1,
        )


def test_find_analogs_excludes_the_full_temporal_window_around_the_query_date() -> None:
    matrix, dates = _history()
    query_date = dates[30]
    exclusion_days = 5

    _distances, selected = find_analogs(
        query_vector=np.array([30.0]),
        history_matrix=matrix,
        history_dates=dates,
        query_date=query_date,
        horizon_days=0,
        k=1,
        exclusion_days=exclusion_days,
    )

    assert len(selected) == 1
    assert abs((selected[0] - query_date).days) > exclusion_days


def test_find_analogs_raises_when_every_date_falls_inside_the_exclusion_window() -> None:
    matrix, dates = _history()
    short_matrix = matrix[:5]
    short_dates = dates[:5]

    with pytest.raises(ValueError, match="temporal exclusion"):
        find_analogs(
            query_vector=np.array([2.0]),
            history_matrix=short_matrix,
            history_dates=short_dates,
            query_date=short_dates[2],
            horizon_days=0,
            k=1,
            exclusion_days=30,
        )


def test_find_analogs_refuses_a_future_analog_regardless_of_the_symmetric_exclusion_window() -> None:
    """The symmetric `exclusion_days` window alone would admit a distant future analog (here,
    query_date + 100 days) whose successor path has not happened yet -- proving the METHOD layer
    itself refuses it, not just the caller in `execution/analog_ensemble_model.py`.

    `exclusion_days=0` means the symmetric window excludes only the query day itself, so a
    pre-fix `find_analogs` (no `horizon_days` parameter) would happily select this future analog:
    it is at distance zero from the query vector, the closest possible match, and would always
    win a k=1 search. `horizon_days=10` must refuse it regardless.
    """
    matrix, dates = _history()
    query_date = dates[30]
    future_date = query_date + timedelta(days=100)
    # A feature vector identical to the query -- the objectively nearest possible match by
    # Euclidean distance, so if the guard failed open this row would always be selected.
    leaking_history = np.vstack([matrix, np.array([[30.0]])])
    leaking_dates = (*dates, future_date)

    _distances, selected = find_analogs(
        query_vector=np.array([30.0]),
        history_matrix=leaking_history,
        history_dates=leaking_dates,
        query_date=query_date,
        horizon_days=10,
        k=1,
        exclusion_days=0,
    )

    assert future_date not in selected
    assert selected[0] < query_date - timedelta(days=10)


def test_generate_anen_forecast_is_deterministic_and_matches_the_single_nearest_analog() -> None:
    matrix, dates = _history()
    targets = _target_series(dates)
    hyperparams = AnEnHyperparameters(k_neighbors=1, temporal_exclusion_days=5, horizon_days=SINGLE_ANALOG_HORIZON)

    first = generate_anen_forecast(
        query_date=QUERY_DAY_OUTSIDE_HISTORY,
        query_vector=np.array([float(NEAREST_INDEX)]),
        history_matrix=matrix,
        history_dates=dates,
        target_series=targets,
        hyperparams=hyperparams,
    )
    second = generate_anen_forecast(
        query_date=QUERY_DAY_OUTSIDE_HISTORY,
        query_vector=np.array([float(NEAREST_INDEX)]),
        history_matrix=matrix,
        history_dates=dates,
        target_series=targets,
        hyperparams=hyperparams,
    )

    assert first == second, "the pure method carries no randomness and must reproduce exactly"
    assert first.method_name == METHOD_NAME
    assert len(first.steps) == SINGLE_ANALOG_HORIZON
    for step in first.steps:
        successor_index = NEAREST_INDEX + step.horizon_step
        expected_value = float(successor_index) * 10.0
        assert step.analog_count == 1
        assert step.low_value == step.median_value == step.high_value == pytest.approx(expected_value)
        assert step.mean_bias_correction == 0.0


def test_generate_anen_forecast_applies_the_analog_space_bias_correction() -> None:
    matrix, dates = _history()
    targets = _target_series(dates)
    analog_date = dates[NEAREST_INDEX]
    bias = 5.0
    hyperparams = AnEnHyperparameters(k_neighbors=1, temporal_exclusion_days=5, horizon_days=1)

    result = generate_anen_forecast(
        query_date=QUERY_DAY_OUTSIDE_HISTORY,
        query_vector=np.array([float(NEAREST_INDEX)]),
        history_matrix=matrix,
        history_dates=dates,
        target_series=targets,
        recorded_residuals={analog_date: bias},
        hyperparams=hyperparams,
    )

    step = result.steps[0]
    successor_value = float(NEAREST_INDEX + 1) * 10.0
    assert step.mean_bias_correction == bias
    assert step.median_value == pytest.approx(successor_value - bias)


def test_generate_anen_forecast_falls_back_to_the_query_days_own_value_with_no_successor() -> None:
    matrix, dates = _history()
    fallback_value = 42.0
    hyperparams = AnEnHyperparameters(k_neighbors=1, temporal_exclusion_days=5, horizon_days=1)

    result = generate_anen_forecast(
        query_date=QUERY_DAY_OUTSIDE_HISTORY,
        query_vector=np.array([float(NEAREST_INDEX)]),
        history_matrix=matrix,
        history_dates=dates,
        target_series={QUERY_DAY_OUTSIDE_HISTORY: fallback_value},
        hyperparams=hyperparams,
    )

    step = result.steps[0]
    assert step.analog_count == 0
    assert step.low_value == step.median_value == step.high_value == fallback_value
    assert step.mean_bias_correction == 0.0


def test_generate_anen_forecast_computes_empirical_quantiles_over_several_analogs() -> None:
    matrix, dates = _history()
    targets = _target_series(dates)
    hyperparams = AnEnHyperparameters(k_neighbors=5, temporal_exclusion_days=5, horizon_days=1)

    result = generate_anen_forecast(
        query_date=QUERY_DAY_OUTSIDE_HISTORY,
        query_vector=np.array([float(NEAREST_INDEX)]),
        history_matrix=matrix,
        history_dates=dates,
        target_series=targets,
        hyperparams=hyperparams,
    )

    step = result.steps[0]
    assert step.analog_count == hyperparams.k_neighbors
    assert step.low_value <= step.median_value <= step.high_value


def test_hyperparameters_checksum_is_stable_and_sensitive_to_every_field() -> None:
    baseline = AnEnHyperparameters(k_neighbors=10, temporal_exclusion_days=14, horizon_days=7)
    same = AnEnHyperparameters(k_neighbors=10, temporal_exclusion_days=14, horizon_days=7)
    different_k = AnEnHyperparameters(k_neighbors=11, temporal_exclusion_days=14, horizon_days=7)

    assert baseline.checksum == same.checksum
    assert baseline.checksum != different_k.checksum
