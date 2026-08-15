"""Unit tests for the database-free seasonal candidate cores."""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pytest

from agri_data_service.method.ml.seasonal_candidates import (
    DailyIncrementBootstrapCandidate,
    ElapsedTimeLinearCandidate,
    PersistenceCandidate,
    RegularizedLagSeasonalRidgeCandidate,
    SeasonalCandidateError,
    SeasonalClimatologyCandidate,
    SeasonalNaiveCandidate,
    build_daily_series,
    default_candidates,
    ridge_feature_names,
)

START = date(2022, 1, 1)


def _seasonal_series(days: int = 1200, *, key: str = "series", amplitude: float = 10.0) -> object:
    observations = {
        START + timedelta(days=index): 15.0 + amplitude * math.sin(2 * math.pi * index / 365.25)
        for index in range(days)
    }
    return build_daily_series(key, "C", observations)


def test_build_daily_series_places_gaps_as_nan() -> None:
    series = build_daily_series("gappy", "C", {START: 1.0, START + timedelta(days=3): 4.0})
    assert series.values.size == 4  # noqa: PLR2004
    assert math.isnan(series.value_at(START + timedelta(days=1)))
    assert series.observed_day_count() == 2  # noqa: PLR2004


def test_before_excludes_the_cutoff_day_itself() -> None:
    series = _seasonal_series(100)
    cutoff = START + timedelta(days=50)
    sliced = series.before(cutoff)
    assert sliced.end_date < cutoff
    assert math.isnan(sliced.value_at(cutoff))


def test_persistence_repeats_the_last_observed_value() -> None:
    series = _seasonal_series(400)
    origin = START + timedelta(days=300)
    fitted = PersistenceCandidate().fit(series.before(origin))
    prediction = fitted.predict(series.before(origin), origin, 5)
    expected = series.value_at(origin - timedelta(days=1))
    assert prediction.median.tolist() == [expected] * 5


def test_seasonal_naive_reads_the_value_one_year_before_the_target() -> None:
    series = _seasonal_series(900)
    origin = START + timedelta(days=800)
    fitted = SeasonalNaiveCandidate().fit(series.before(origin))
    prediction = fitted.predict(series.before(origin), origin, 3)
    for step in range(1, 4):
        anchor = origin + timedelta(days=step) - timedelta(days=365)
        assert prediction.median[step - 1] == pytest.approx(series.value_at(anchor))


def test_climatology_refuses_an_unsupported_day_of_year_slot_and_falls_back() -> None:
    sparse = build_daily_series("sparse", "C", {START + timedelta(days=index * 40): float(index) for index in range(4)})
    fitted = SeasonalClimatologyCandidate().fit(sparse)
    origin = START + timedelta(days=200)
    prediction = fitted.predict(sparse, origin, 3)
    assert not bool(np.isnan(prediction.median).any())


def test_climatology_recovers_a_clean_seasonal_level() -> None:
    series = _seasonal_series(1200)
    origin = START + timedelta(days=1100)
    fitted = SeasonalClimatologyCandidate().fit(series.before(origin))
    prediction = fitted.predict(series.before(origin), origin, 10)
    actuals = [series.value_at(origin + timedelta(days=step)) for step in range(1, 11)]
    assert float(np.abs(prediction.median - np.asarray(actuals)).mean()) < 1.0


def test_elapsed_time_linear_recovers_a_pure_trend() -> None:
    observations = {START + timedelta(days=index): 3.0 + 0.5 * index for index in range(200)}
    series = build_daily_series("trend", "C", observations)
    origin = START + timedelta(days=200)
    fitted = ElapsedTimeLinearCandidate().fit(series)
    prediction = fitted.predict(series, origin, 3)
    assert prediction.median[0] == pytest.approx(3.0 + 0.5 * 201, rel=1e-6)


def test_elapsed_time_linear_refuses_a_single_observation() -> None:
    series = build_daily_series("one", "C", {START: 1.0})
    with pytest.raises(SeasonalCandidateError, match="two observations"):
        ElapsedTimeLinearCandidate().fit(series)


def test_the_bootstrap_refuses_a_series_with_too_few_consecutive_day_increments() -> None:
    series = build_daily_series(
        "sparse", "C", {START: 1.0, START + timedelta(days=10): 2.0, START + timedelta(days=20): 3.0}
    )
    with pytest.raises(SeasonalCandidateError, match="consecutive-day increment"):
        DailyIncrementBootstrapCandidate().fit(series)


def test_the_bootstrap_is_deterministic_for_one_seed_and_origin() -> None:
    series = _seasonal_series(500)
    origin = START + timedelta(days=400)
    candidate = DailyIncrementBootstrapCandidate(seed=42, path_count=200)
    first = candidate.fit(series.before(origin)).predict(series.before(origin), origin, 5)
    second = candidate.fit(series.before(origin)).predict(series.before(origin), origin, 5)
    assert first.median.tolist() == second.median.tolist()
    assert first.native_low is not None
    assert first.native_high is not None
    assert all(low <= high for low, high in zip(first.native_low, first.native_high, strict=True))


def test_the_bootstrap_changes_with_the_seed() -> None:
    series = _seasonal_series(500)
    origin = START + timedelta(days=400)
    first = DailyIncrementBootstrapCandidate(seed=1, path_count=200).fit(series.before(origin))
    second = DailyIncrementBootstrapCandidate(seed=2, path_count=200).fit(series.before(origin))
    left = first.predict(series.before(origin), origin, 5).median.tolist()
    right = second.predict(series.before(origin), origin, 5).median.tolist()
    assert left != right


def test_ridge_feature_vector_is_pinned_and_ordered() -> None:
    names = ridge_feature_names()
    assert names[:3] == ("lag_1", "lag_2", "lag_3")
    assert names[-4:] == ("sin_annual", "cos_annual", "sin_semiannual", "cos_semiannual")
    assert len(names) == 15  # noqa: PLR2004


def test_ridge_beats_persistence_on_a_clean_seasonal_signal() -> None:
    series = _seasonal_series(1200)
    origin = START + timedelta(days=1100)
    training = series.before(origin)
    ridge = RegularizedLagSeasonalRidgeCandidate(horizon_steps=30).fit(training)
    persistence = PersistenceCandidate().fit(training)
    actuals = np.asarray([series.value_at(origin + timedelta(days=step)) for step in range(1, 31)])
    ridge_error = float(np.abs(ridge.predict(training, origin, 30).median - actuals).mean())
    persistence_error = float(np.abs(persistence.predict(training, origin, 30).median - actuals).mean())
    assert ridge_error < persistence_error


def test_ridge_predictions_do_not_change_when_post_origin_values_are_corrupted() -> None:
    """The candidate is handed a sliced history; corrupting the future must not move its forecast."""
    series = _seasonal_series(1200)
    origin = START + timedelta(days=1000)
    training = series.before(origin)
    clean = RegularizedLagSeasonalRidgeCandidate(horizon_steps=10).fit(training)
    corrupted_values = series.values.copy()
    corrupted_values[series.index_of(origin) :] = 1_000_000.0
    corrupted = build_daily_series(
        series.series_key,
        series.unit,
        {
            series.start_date + timedelta(days=index): float(corrupted_values[index])
            for index in range(corrupted_values.size)
        },
    ).before(origin)
    dirty = RegularizedLagSeasonalRidgeCandidate(horizon_steps=10).fit(corrupted)
    assert clean.predict(training, origin, 10).median.tolist() == dirty.predict(corrupted, origin, 10).median.tolist()


def test_the_default_ladder_carries_every_pre_registered_family() -> None:
    names = [candidate.name for candidate in default_candidates(horizon_steps=30)]
    assert names == [
        "persistence_v1",
        "seasonal_naive_v1",
        "seasonal_climatology_v1",
        "sql_linear_elapsed_time_v1",
        "daily_increment_bootstrap_v1",
        "regularized_lag_seasonal_ridge_v1",
    ]
    assert [candidate.has_native_interval for candidate in default_candidates(horizon_steps=30)] == [
        False,
        False,
        False,
        False,
        True,
        False,
    ]
