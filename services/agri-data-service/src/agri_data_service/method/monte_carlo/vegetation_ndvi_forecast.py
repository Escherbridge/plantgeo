"""Deterministic seasonal-anomaly Monte Carlo for sparse daily NDVI cell series.

Layer L1 (method/monte_carlo): Pure domain computation, no I/O, no SQLAlchemy.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final

import numpy
from numpy.random import PCG64

if TYPE_CHECKING:
    from numpy.typing import NDArray

METHOD_NAME: Final = "ndvi_seasonal_anomaly_bootstrap_v1"
PURPOSE_FORWARD_SIMULATION: Final = "forward_simulation"
PURPOSE_HOLDOUT_EVALUATION: Final = "holdout_evaluation"
GAP_POLICY: Final = "strict"
DAYS_PER_SEASONAL_CYCLE: Final = 366
SEASONAL_WINDOW_DAYS: Final = 15
MIN_CLIMATOLOGY_SAMPLES: Final = 4
MIN_INNOVATION_SAMPLES: Final = 4
MIN_AUTOCORRELATION_PAIRS: Final = 8
MAX_AUTOCORRELATION_GAP_DAYS: Final = 30
MIN_TRAINING_DAYS: Final = 24
MAX_DAILY_PERSISTENCE: Final = 0.98
NDVI_LOWER_BOUND: Final = -1.0
NDVI_UPPER_BOUND: Final = 1.0
LOW_QUANTILE: Final = 0.1
MEDIAN_QUANTILE: Final = 0.5
HIGH_QUANTILE: Final = 0.9
FLOAT_FINGERPRINT_FORMAT: Final = ".17g"


class InsufficientNdviHistoryError(ValueError):
    """Raised when a cell's own governed history cannot support the method without fabrication."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ObservedDay:
    """One publisher-named day of governed NDVI for a single grid cell."""

    observed_day: date
    metric_value: float
    observation_checksum: str


@dataclass(frozen=True, slots=True)
class SeasonalHistory:
    """A cell's own history reduced to a seasonal level, an anomaly pool and a persistence anchor."""

    cutoff_day: date
    history_start_day: date
    governed_day_count: int
    training_day_count: int
    climatology_by_day_of_year: tuple[float, ...]
    climatology_sample_counts: tuple[int, ...]
    anomaly_values: tuple[float, ...]
    anomaly_days_of_year: tuple[int, ...]
    latest_observed_day: date
    latest_observed_value: float
    anchor_day: date
    anchor_anomaly: float
    lag_one_autocorrelation: float
    autocorrelation_pair_count: int
    mean_observation_gap_days: float
    daily_persistence: float


@dataclass(frozen=True, slots=True)
class HorizonQuantiles:
    """Marginal simulated distribution for one horizon step."""

    horizon_step: int
    valid_day: date
    low_value: float
    median_value: float
    high_value: float
    innovation_pool_size: int


@dataclass(frozen=True, slots=True)
class SimulationRequest:
    """Bounded, reproducible simulation parameters."""

    horizon_days: int
    simulation_count: int
    seed: int


def day_of_year_index(value: date) -> int:
    """Return the 1-based day of year used as the seasonal phase coordinate."""
    return value.timetuple().tm_yday


def circular_day_distance(first_day_of_year: int, second_day_of_year: int) -> int:
    """Return the wrap-around distance in days between two days of year."""
    direct = abs(first_day_of_year - second_day_of_year)
    return min(direct, DAYS_PER_SEASONAL_CYCLE - direct)


def _seasonal_window_mask(days_of_year: NDArray[numpy.int64], target_day_of_year: int) -> NDArray[numpy.bool_]:
    direct = numpy.abs(days_of_year - target_day_of_year)
    circular = numpy.minimum(direct, DAYS_PER_SEASONAL_CYCLE - direct)
    return numpy.asarray(circular <= SEASONAL_WINDOW_DAYS, dtype=numpy.bool_)


def _build_climatology(
    days_of_year: NDArray[numpy.int64],
    values: NDArray[numpy.float64],
) -> tuple[NDArray[numpy.float64], NDArray[numpy.int64]]:
    climatology = numpy.full(DAYS_PER_SEASONAL_CYCLE, numpy.nan, dtype=numpy.float64)
    sample_counts = numpy.zeros(DAYS_PER_SEASONAL_CYCLE, dtype=numpy.int64)
    for target_day_of_year in range(1, DAYS_PER_SEASONAL_CYCLE + 1):
        window = _seasonal_window_mask(days_of_year, target_day_of_year)
        sample_count = int(window.sum())
        sample_counts[target_day_of_year - 1] = sample_count
        if sample_count >= MIN_CLIMATOLOGY_SAMPLES:
            climatology[target_day_of_year - 1] = float(values[window].mean())
    return climatology, sample_counts


def _estimate_daily_persistence(
    observed_days: tuple[date, ...],
    anomalies: NDArray[numpy.float64],
) -> tuple[float, float, int, float]:
    leading: list[float] = []
    trailing: list[float] = []
    gaps: list[int] = []
    for index in range(1, len(observed_days)):
        gap_days = (observed_days[index] - observed_days[index - 1]).days
        if 1 <= gap_days <= MAX_AUTOCORRELATION_GAP_DAYS:
            leading.append(float(anomalies[index - 1]))
            trailing.append(float(anomalies[index]))
            gaps.append(gap_days)
    pair_count = len(gaps)
    if pair_count < MIN_AUTOCORRELATION_PAIRS:
        raise InsufficientNdviHistoryError(
            "autocorrelation_pairs_below_minimum",
            f"{pair_count} usable anomaly pairs below the required {MIN_AUTOCORRELATION_PAIRS}",
        )
    leading_array = numpy.asarray(leading, dtype=numpy.float64)
    trailing_array = numpy.asarray(trailing, dtype=numpy.float64)
    if float(leading_array.std()) == 0.0 or float(trailing_array.std()) == 0.0:
        raise InsufficientNdviHistoryError(
            "anomaly_pool_has_no_spread",
            "the cell's own anomaly pool is degenerate, so no honest band can be simulated",
        )
    correlation = float(numpy.corrcoef(leading_array, trailing_array)[0, 1])
    if not math.isfinite(correlation):
        raise InsufficientNdviHistoryError(
            "anomaly_autocorrelation_not_finite",
            "the cell's own anomaly autocorrelation is not finite",
        )
    mean_gap_days = float(numpy.asarray(gaps, dtype=numpy.float64).mean())
    clamped = min(max(correlation, 0.0), MAX_DAILY_PERSISTENCE)
    return clamped ** (1.0 / mean_gap_days), correlation, pair_count, mean_gap_days


def eligible_history(observations: tuple[ObservedDay, ...], cutoff_day: date) -> tuple[ObservedDay, ...]:
    """Return the leakage-free history: publisher-named days at or before the cutoff, in day order."""
    kept = [row for row in observations if row.observed_day <= cutoff_day]
    kept.sort(key=lambda row: row.observed_day)
    observed_days = [row.observed_day for row in kept]
    if len(set(observed_days)) != len(observed_days):
        raise InsufficientNdviHistoryError(
            "duplicate_observed_day",
            "the governed history carries more than one row for a publisher-named day",
        )
    return tuple(kept)


def build_seasonal_history(observations: tuple[ObservedDay, ...], cutoff_day: date) -> SeasonalHistory:
    """Reduce a cell's own governed history at or before the cutoff day to the method's state."""
    kept = eligible_history(observations, cutoff_day)
    if len(kept) < MIN_TRAINING_DAYS:
        raise InsufficientNdviHistoryError(
            "training_days_below_minimum",
            f"{len(kept)} observed days at or before {cutoff_day.isoformat()} below the required {MIN_TRAINING_DAYS}",
        )
    observed_days = tuple(row.observed_day for row in kept)
    values = numpy.asarray([row.metric_value for row in kept], dtype=numpy.float64)
    days_of_year = numpy.asarray([day_of_year_index(day) for day in observed_days], dtype=numpy.int64)
    climatology, sample_counts = _build_climatology(days_of_year, values)
    supported = ~numpy.isnan(climatology[days_of_year - 1])
    if int(supported.sum()) < MIN_TRAINING_DAYS:
        raise InsufficientNdviHistoryError(
            "seasonally_referenced_days_below_minimum",
            f"{int(supported.sum())} of {len(kept)} observed days have a supported seasonal window, "
            f"below the required {MIN_TRAINING_DAYS}",
        )
    supported_days = tuple(day for day, keep in zip(observed_days, supported.tolist(), strict=True) if keep)
    supported_values = values[supported]
    supported_days_of_year = days_of_year[supported]
    anomalies = supported_values - climatology[supported_days_of_year - 1]
    daily_persistence, correlation, pair_count, mean_gap_days = _estimate_daily_persistence(supported_days, anomalies)
    return SeasonalHistory(
        cutoff_day=cutoff_day,
        history_start_day=observed_days[0],
        governed_day_count=len(kept),
        training_day_count=len(supported_days),
        climatology_by_day_of_year=tuple(float(value) for value in climatology),
        climatology_sample_counts=tuple(int(count) for count in sample_counts),
        anomaly_values=tuple(float(value) for value in anomalies),
        anomaly_days_of_year=tuple(int(value) for value in supported_days_of_year),
        latest_observed_day=observed_days[-1],
        latest_observed_value=float(values[-1]),
        anchor_day=supported_days[-1],
        anchor_anomaly=float(anomalies[-1]),
        lag_one_autocorrelation=correlation,
        autocorrelation_pair_count=pair_count,
        mean_observation_gap_days=mean_gap_days,
        daily_persistence=daily_persistence,
    )


def climatology_baseline(history: SeasonalHistory, valid_day: date) -> float:
    """Return the seasonal-naive baseline: the cell's own day-of-year climatology, NaN where unsupported."""
    return history.climatology_by_day_of_year[day_of_year_index(valid_day) - 1]


def persistence_baseline(history: SeasonalHistory) -> float:
    """Return the trivial carry-forward baseline: the latest observed value at or before the cutoff."""
    return history.latest_observed_value


def history_checksum(observations: tuple[ObservedDay, ...], cutoff_day: date) -> str:
    """Return a sha256 digest over the ordered governed history that one simulation consumed."""
    parts = [
        "|".join(
            (
                row.observed_day.isoformat(),
                format(row.metric_value, FLOAT_FINGERPRINT_FORMAT),
                row.observation_checksum,
            )
        )
        for row in eligible_history(observations, cutoff_day)
    ]
    canonical = "|".join(("ndvi_seasonal_anomaly_history_v1", *parts))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_parameter_text(  # noqa: PLR0913
    *,
    series_key: str,
    release_set_id: str,
    input_release_checksum: str,
    contract_checksum: str,
    governed_history_checksum: str,
    as_of_text: str,
    history: SeasonalHistory,
    request: SimulationRequest,
) -> str:
    """Return the pinned canonical parameter text whose digest reproduces one simulation exactly."""
    fields = (
        METHOD_NAME,
        series_key,
        release_set_id,
        input_release_checksum,
        contract_checksum,
        governed_history_checksum,
        as_of_text,
        history.cutoff_day.isoformat(),
        history.history_start_day.isoformat(),
        str(request.horizon_days),
        str(request.simulation_count),
        str(request.seed),
        GAP_POLICY,
        format(NDVI_LOWER_BOUND, FLOAT_FINGERPRINT_FORMAT),
        format(NDVI_UPPER_BOUND, FLOAT_FINGERPRINT_FORMAT),
        str(SEASONAL_WINDOW_DAYS),
        str(MIN_CLIMATOLOGY_SAMPLES),
        str(MIN_INNOVATION_SAMPLES),
        str(MIN_AUTOCORRELATION_PAIRS),
        str(MAX_AUTOCORRELATION_GAP_DAYS),
        str(history.training_day_count),
        format(history.daily_persistence, FLOAT_FINGERPRINT_FORMAT),
        format(history.anchor_anomaly, FLOAT_FINGERPRINT_FORMAT),
        history.anchor_day.isoformat(),
        str(history.governed_day_count),
        "pcg64_random_raw",
        "p10_p50_p90",
    )
    return "|".join(fields)


def parameter_checksum(canonical_text: str) -> str:
    """Return the sha256 hex digest that binds one simulation's parameters and its random stream."""
    return hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()


def _innovation_pools(
    history: SeasonalHistory,
    valid_days: tuple[date, ...],
) -> tuple[NDArray[numpy.float64], NDArray[numpy.int64]]:
    anomaly_days_of_year = numpy.asarray(history.anomaly_days_of_year, dtype=numpy.int64)
    anomaly_values = numpy.asarray(history.anomaly_values, dtype=numpy.float64)
    pools: list[NDArray[numpy.float64]] = []
    for valid_day in valid_days:
        pool = anomaly_values[_seasonal_window_mask(anomaly_days_of_year, day_of_year_index(valid_day))]
        if pool.size < MIN_INNOVATION_SAMPLES:
            raise InsufficientNdviHistoryError(
                "innovation_pool_below_minimum",
                f"{pool.size} seasonal anomalies for {valid_day.isoformat()} "
                f"below the required {MIN_INNOVATION_SAMPLES}",
            )
        pools.append(pool)
    pool_sizes = numpy.asarray([pool.size for pool in pools], dtype=numpy.int64)
    padded = numpy.zeros((len(pools), int(pool_sizes.max())), dtype=numpy.float64)
    for index, pool in enumerate(pools):
        padded[index, : pool.size] = pool
    return padded, pool_sizes


@dataclass(frozen=True, slots=True)
class ProvenancedForecastRow:
    """One quantile of one simulated horizon step, carrying every column layer-lanes.md section 3 requires.

    `forecast_run_id` is the run's `parameter_checksum` (see `canonical_parameter_text`) -- which is
    also the literal integer `simulate_horizon_quantiles` seeds `PCG64` with (`int(checksum, 16)`),
    so recording it alone reproduces this row's exact draws without replaying the ~25-field
    canonicalisation. `random_seed` is the separate, caller-declared `SimulationRequest.seed`: kept
    for audit because it is a distinct, human-meaningful value, but it is only one of the fields
    folded into `forecast_run_id` and does not alone reproduce a run.
    """

    forecast_run_id: str
    random_seed: int
    ensemble_size: int
    horizon_days: int
    issued_on: date
    quantile: float
    valid_day: date
    metric_value: float
    innovation_pool_size: int


def provenanced_forecast_rows(
    *,
    history: SeasonalHistory,
    quantiles: tuple[HorizonQuantiles, ...],
    request: SimulationRequest,
    forecast_run_id: str,
) -> tuple[ProvenancedForecastRow, ...]:
    """Reshape wide per-horizon p10/p50/p90 quantiles into one contract-provenanced row per quantile.

    `forecast_run_id` must be the `parameter_checksum` that actually produced `quantiles` -- passing
    a different value would let a row's recorded provenance disagree with the draws it reports.
    Never let a forecast row inherit an observed row's provenance (layer-lanes.md section 3): every
    field here comes from this run's own `history`/`request`, never from an `ObservedDay`. Quantiles
    come from the ensemble (`simulate_horizon_quantiles`'s own `numpy.percentile` over simulated
    paths), never from a distribution fitted after the fact.
    """
    if not forecast_run_id.strip():
        raise ValueError("forecast_run_id must be non-blank; it is this run's sole reproducibility key")
    return tuple(
        ProvenancedForecastRow(
            forecast_run_id=forecast_run_id,
            random_seed=request.seed,
            ensemble_size=request.simulation_count,
            horizon_days=step.horizon_step,
            issued_on=history.cutoff_day,
            quantile=quantile_level,
            valid_day=step.valid_day,
            metric_value=quantile_value,
            innovation_pool_size=step.innovation_pool_size,
        )
        for step in quantiles
        for quantile_level, quantile_value in (
            (LOW_QUANTILE, step.low_value),
            (MEDIAN_QUANTILE, step.median_value),
            (HIGH_QUANTILE, step.high_value),
        )
    )


def simulate_horizon_quantiles(
    *,
    history: SeasonalHistory,
    request: SimulationRequest,
    checksum: str,
) -> tuple[HorizonQuantiles, ...]:
    """Simulate marginal per-horizon quantiles from the cell's own seasonally matched anomaly pool."""
    if request.horizon_days < 1:
        raise ValueError("horizon days must be positive")
    if request.simulation_count < 1:
        raise ValueError("simulation count must be positive")
    valid_days = tuple(history.cutoff_day + timedelta(days=step) for step in range(1, request.horizon_days + 1))
    seasonal_levels = numpy.asarray([climatology_baseline(history, day) for day in valid_days], dtype=numpy.float64)
    if bool(numpy.isnan(seasonal_levels).any()):
        raise InsufficientNdviHistoryError(
            "climatology_window_unsupported",
            "at least one horizon day has fewer seasonal neighbours than the climatology minimum",
        )
    padded_pools, pool_sizes = _innovation_pools(history, valid_days)
    anchor_gap_days = (history.cutoff_day - history.anchor_day).days
    horizon_offsets = numpy.arange(1, request.horizon_days + 1, dtype=numpy.float64)
    decay = numpy.power(history.daily_persistence, anchor_gap_days + horizon_offsets)
    innovation_scale = numpy.sqrt(numpy.maximum(1.0 - numpy.square(decay), 0.0))
    raw_draws = PCG64(int(checksum, 16)).random_raw(request.simulation_count * request.horizon_days)
    draw_matrix = numpy.asarray(raw_draws, dtype=numpy.uint64).reshape(request.simulation_count, request.horizon_days)
    pool_indices = numpy.mod(draw_matrix, pool_sizes.astype(numpy.uint64)).astype(numpy.intp)
    innovations = numpy.take_along_axis(padded_pools, pool_indices.T, axis=1).T
    paths = seasonal_levels + decay * history.anchor_anomaly + innovation_scale * innovations
    bounded = numpy.clip(paths, NDVI_LOWER_BOUND, NDVI_UPPER_BOUND)
    low, median, high = numpy.percentile(
        bounded,
        [LOW_QUANTILE * 100.0, MEDIAN_QUANTILE * 100.0, HIGH_QUANTILE * 100.0],
        axis=0,
        method="linear",
    )
    return tuple(
        HorizonQuantiles(
            horizon_step=step + 1,
            valid_day=valid_days[step],
            low_value=float(low[step]),
            median_value=float(median[step]),
            high_value=float(high[step]),
            innovation_pool_size=int(pool_sizes[step]),
        )
        for step in range(request.horizon_days)
    )
