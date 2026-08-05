"""Contract tests for the seasonal-anomaly NDVI Monte Carlo method."""

from __future__ import annotations

import hashlib
import math
from datetime import date, timedelta
from typing import TYPE_CHECKING

import pytest

from agri_data_service.execution.vegetation_ndvi_forecast import (
    MIN_TRAINING_DAYS,
    NDVI_LOWER_BOUND,
    NDVI_UPPER_BOUND,
    InsufficientNdviHistoryError,
    ObservedDay,
    SimulationRequest,
    build_seasonal_history,
    canonical_parameter_text,
    circular_day_distance,
    climatology_baseline,
    day_of_year_index,
    eligible_history,
    history_checksum,
    parameter_checksum,
    persistence_baseline,
    simulate_horizon_quantiles,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_HISTORY_START = date(2022, 8, 5)
_CUTOFF_DAY = date(2026, 8, 5)
_CADENCE_DAYS = 6
_HALF_YEAR_DAY_OF_YEAR = 184
_HALF_YEAR_DISTANCE = 183
_TEN_DAY_DISTANCE = 10
_MIN_POOL_SIZE = 4
_PERSISTENT_FLOOR = 0.9
_WHITE_CEILING = 0.5
_BAND_RATIO_FLOOR = 0.5
_BAND_RATIO_CEILING = 2.0


def _seasonal_value(day: date, offset: float) -> float:
    phase = 2.0 * math.pi * (day_of_year_index(day) - 100) / 365.25
    return 0.45 + 0.25 * math.sin(phase) + offset


def _persistent_anomaly(index: int) -> float:
    return 0.05 * math.sin(index * 0.12) + 0.008 * math.sin(index * 2.3)


def _white_anomaly(index: int) -> float:
    return 0.05 * math.sin(index * 1.97) + 0.008 * math.cos(index * 0.7)


def _synthetic_observations(
    *,
    start_day: date = _HISTORY_START,
    end_day: date = _CUTOFF_DAY,
    cadence_days: int = _CADENCE_DAYS,
    anomaly: Callable[[int], float] = _persistent_anomaly,
) -> tuple[ObservedDay, ...]:
    rows: list[ObservedDay] = []
    day = start_day
    index = 0
    while day <= end_day:
        wobble = anomaly(index)
        rows.append(
            ObservedDay(
                observed_day=day,
                metric_value=_seasonal_value(day, wobble),
                observation_checksum=hashlib.sha256(day.isoformat().encode("utf-8")).hexdigest(),
            )
        )
        day += timedelta(days=cadence_days)
        index += 1
    return tuple(rows)


def _request() -> SimulationRequest:
    return SimulationRequest(horizon_days=30, simulation_count=500, seed=7)


def _checksum(observations: tuple[ObservedDay, ...], cutoff_day: date) -> str:
    history = build_seasonal_history(observations, cutoff_day)
    return parameter_checksum(
        canonical_parameter_text(
            series_key="ndvi-daily:test-cell",
            release_set_id="00000000-0000-0000-0000-000000000001",
            input_release_checksum="a" * 64,
            contract_checksum="b" * 64,
            governed_history_checksum=history_checksum(observations, cutoff_day),
            as_of_text="2026-08-05T00:00:00+00:00",
            history=history,
            request=_request(),
        )
    )


def test_circular_day_distance_wraps_across_the_year_boundary() -> None:
    assert circular_day_distance(1, 366) == 1
    assert circular_day_distance(10, 20) == _TEN_DAY_DISTANCE
    assert circular_day_distance(1, _HALF_YEAR_DAY_OF_YEAR) == _HALF_YEAR_DISTANCE


def test_eligible_history_drops_every_day_after_the_cutoff() -> None:
    observations = _synthetic_observations()
    kept = eligible_history(observations, date(2024, 1, 1))
    assert kept
    assert max(row.observed_day for row in kept) <= date(2024, 1, 1)
    assert len(kept) < len(observations)


def test_eligible_history_refuses_duplicate_publisher_days() -> None:
    duplicated = (
        ObservedDay(observed_day=date(2025, 5, 1), metric_value=0.5, observation_checksum="c" * 64),
        ObservedDay(observed_day=date(2025, 5, 1), metric_value=0.6, observation_checksum="d" * 64),
    )
    with pytest.raises(InsufficientNdviHistoryError, match="duplicate_observed_day"):
        eligible_history(duplicated, date(2025, 6, 1))


def test_history_checksum_ignores_observations_after_the_cutoff() -> None:
    observations = _synthetic_observations()
    truncated = tuple(row for row in observations if row.observed_day <= date(2025, 1, 1))
    assert history_checksum(observations, date(2025, 1, 1)) == history_checksum(truncated, date(2025, 1, 1))


def test_simulation_is_leakage_free_at_the_cutoff() -> None:
    observations = _synthetic_observations()
    truncated = tuple(row for row in observations if row.observed_day <= date(2025, 6, 1))
    full_history = build_seasonal_history(observations, date(2025, 6, 1))
    truncated_history = build_seasonal_history(truncated, date(2025, 6, 1))
    assert full_history == truncated_history
    checksum = _checksum(observations, date(2025, 6, 1))
    from_full = simulate_horizon_quantiles(history=full_history, request=_request(), checksum=checksum)
    from_truncated = simulate_horizon_quantiles(history=truncated_history, request=_request(), checksum=checksum)
    assert from_full == from_truncated


def test_simulation_is_deterministic_for_identical_parameters() -> None:
    observations = _synthetic_observations()
    history = build_seasonal_history(observations, _CUTOFF_DAY)
    checksum = _checksum(observations, _CUTOFF_DAY)
    first = simulate_horizon_quantiles(history=history, request=_request(), checksum=checksum)
    second = simulate_horizon_quantiles(history=history, request=_request(), checksum=checksum)
    assert first == second


def test_simulation_changes_when_the_seed_changes() -> None:
    observations = _synthetic_observations()
    history = build_seasonal_history(observations, _CUTOFF_DAY)
    base = simulate_horizon_quantiles(
        history=history,
        request=_request(),
        checksum=_checksum(observations, _CUTOFF_DAY),
    )
    reseeded_request = SimulationRequest(horizon_days=30, simulation_count=500, seed=8)
    reseeded_checksum = parameter_checksum(
        canonical_parameter_text(
            series_key="ndvi-daily:test-cell",
            release_set_id="00000000-0000-0000-0000-000000000001",
            input_release_checksum="a" * 64,
            contract_checksum="b" * 64,
            governed_history_checksum=history_checksum(observations, _CUTOFF_DAY),
            as_of_text="2026-08-05T00:00:00+00:00",
            history=history,
            request=reseeded_request,
        )
    )
    reseeded = simulate_horizon_quantiles(history=history, request=reseeded_request, checksum=reseeded_checksum)
    assert [row.median_value for row in base] != [row.median_value for row in reseeded]


def test_quantiles_are_ordered_bounded_and_never_degenerate() -> None:
    observations = _synthetic_observations()
    history = build_seasonal_history(observations, _CUTOFF_DAY)
    quantiles = simulate_horizon_quantiles(
        history=history,
        request=_request(),
        checksum=_checksum(observations, _CUTOFF_DAY),
    )
    assert len(quantiles) == _request().horizon_days
    for step, row in enumerate(quantiles, start=1):
        assert row.horizon_step == step
        assert row.valid_day == _CUTOFF_DAY + timedelta(days=step)
        assert row.low_value <= row.median_value <= row.high_value
        assert row.low_value >= NDVI_LOWER_BOUND
        assert row.high_value <= NDVI_UPPER_BOUND
        assert row.high_value > row.low_value
        assert row.innovation_pool_size >= _MIN_POOL_SIZE


def test_band_widens_with_horizon_when_the_cell_s_anomalies_persist() -> None:
    observations = _synthetic_observations(anomaly=_persistent_anomaly)
    history = build_seasonal_history(observations, _CUTOFF_DAY)
    assert history.daily_persistence > _PERSISTENT_FLOOR
    quantiles = simulate_horizon_quantiles(
        history=history,
        request=_request(),
        checksum=_checksum(observations, _CUTOFF_DAY),
    )
    near = quantiles[0].high_value - quantiles[0].low_value
    far = quantiles[-1].high_value - quantiles[-1].low_value
    assert far > near


def test_band_is_climatological_from_the_first_step_when_anomalies_are_white() -> None:
    observations = _synthetic_observations(anomaly=_white_anomaly)
    history = build_seasonal_history(observations, _CUTOFF_DAY)
    assert history.daily_persistence < _WHITE_CEILING
    quantiles = simulate_horizon_quantiles(
        history=history,
        request=_request(),
        checksum=_checksum(observations, _CUTOFF_DAY),
    )
    near = quantiles[0].high_value - quantiles[0].low_value
    far = quantiles[-1].high_value - quantiles[-1].low_value
    assert near > 0.0
    assert _BAND_RATIO_FLOOR < far / near < _BAND_RATIO_CEILING


def test_short_history_is_refused_rather_than_extrapolated() -> None:
    sparse = _synthetic_observations(start_day=date(2026, 6, 1), end_day=_CUTOFF_DAY, cadence_days=6)
    assert len(sparse) < MIN_TRAINING_DAYS
    with pytest.raises(InsufficientNdviHistoryError, match="training_days_below_minimum"):
        build_seasonal_history(sparse, _CUTOFF_DAY)


def test_single_season_history_is_refused_for_unsupported_seasonal_windows() -> None:
    single_season = _synthetic_observations(start_day=date(2026, 1, 5), end_day=date(2026, 6, 30), cadence_days=3)
    assert len(single_season) >= MIN_TRAINING_DAYS
    with pytest.raises(InsufficientNdviHistoryError, match="climatology_window_unsupported"):
        simulate_horizon_quantiles(
            history=build_seasonal_history(single_season, date(2026, 6, 30)),
            request=_request(),
            checksum="f" * 64,
        )


def test_baselines_are_the_trivial_predictors_they_claim_to_be() -> None:
    observations = _synthetic_observations()
    history = build_seasonal_history(observations, _CUTOFF_DAY)
    assert persistence_baseline(history) == observations[-1].metric_value
    seasonal = climatology_baseline(history, _CUTOFF_DAY + timedelta(days=10))
    assert math.isfinite(seasonal)
    assert NDVI_LOWER_BOUND <= seasonal <= NDVI_UPPER_BOUND
