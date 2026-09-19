"""Database-free candidate cores for the seasonal forecast ladder.

Every candidate here is a pure function of a daily series and an origin date: it never opens a
session, never reads a clock, and never sees a value dated on or after the origin it is asked to
forecast from. The caller slices the history; the candidate cannot widen it.

Assumptions and limits are stated per candidate below and in the track's pre-registration
(`conductor/tracks/seasonal_forecast_feedback_20260726/preregistration-2026-08-14.md`, section 4).
A bootstrap band is an empirical path quantile, never a calibrated confidence interval.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final, Protocol

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from numpy.typing import NDArray

DEFAULT_SEED: Final = 20260814
BOOTSTRAP_PATH_COUNT: Final = 2000
SEASONAL_WINDOW_DAYS: Final = 15
MIN_CLIMATOLOGY_SAMPLES: Final = 5
SEASONAL_NAIVE_LAG_DAYS: Final = 365
SEASONAL_NAIVE_SEARCH_DAYS: Final = 3
RIDGE_LAG_DAYS: Final[tuple[int, ...]] = (1, 2, 3, 5, 7, 14, 21, 28)
RIDGE_ROLLING_WINDOWS: Final[tuple[int, ...]] = (7, 14, 30)
RIDGE_PENALTY_GRID: Final[tuple[float, ...]] = (0.01, 0.1, 1.0, 10.0, 100.0)
RIDGE_INNER_VALIDATION_FRACTION: Final = 0.2
MINIMUM_LINEAR_FIT_POINTS: Final = 2
MINIMUM_BOOTSTRAP_INCREMENTS: Final = 2
LOW_QUANTILE: Final = 10.0
HIGH_QUANTILE: Final = 90.0


class SeasonalCandidateError(ValueError):
    """A candidate cannot be fitted or applied on the history it was given."""


@dataclass(frozen=True)
class DailySeries:
    """A dense daily grid; `values[i]` is the value at `start_date + i days`, NaN where absent."""

    series_key: str
    unit: str
    start_date: date
    values: NDArray[np.float64]

    @property
    def end_date(self) -> date:
        """The last calendar day the grid covers, whether or not it holds a value."""
        return self.start_date + timedelta(days=int(self.values.size) - 1)

    def index_of(self, day: date) -> int:
        """Grid index of ``day``; may be out of range and the caller must check."""
        return (day - self.start_date).days

    def value_at(self, day: date) -> float:
        """Value at ``day``, or NaN when the day sits outside the grid or carries no observation."""
        index = self.index_of(day)
        if index < 0 or index >= self.values.size:
            return float("nan")
        return float(self.values[index])

    def before(self, cutoff: date) -> DailySeries:
        """The sub-series holding only days strictly before ``cutoff``."""
        stop = max(0, min(int(self.values.size), self.index_of(cutoff)))
        return DailySeries(
            series_key=self.series_key,
            unit=self.unit,
            start_date=self.start_date,
            values=self.values[:stop].copy(),
        )

    def observed_day_count(self) -> int:
        """How many grid days carry a real value."""
        return int(np.count_nonzero(~np.isnan(self.values)))

    def last_observed(self) -> tuple[date, float]:
        """The most recent observed (day, value); raises when the series holds nothing."""
        observed = np.flatnonzero(~np.isnan(self.values))
        if observed.size == 0:
            raise SeasonalCandidateError(f"{self.series_key} holds no observation")
        index = int(observed[-1])
        return self.start_date + timedelta(days=index), float(self.values[index])


def build_daily_series(
    series_key: str,
    unit: str,
    observations: Mapping[date, float],
) -> DailySeries:
    """Lay a date-keyed mapping onto a dense daily grid, NaN where a day is absent."""
    if not observations:
        raise SeasonalCandidateError(f"{series_key} has no observation to lay on a grid")
    start = min(observations)
    end = max(observations)
    values = np.full((end - start).days + 1, np.nan, dtype=np.float64)
    for day, value in observations.items():
        values[(day - start).days] = value
    return DailySeries(series_key=series_key, unit=unit, start_date=start, values=values)


@dataclass(frozen=True)
class Prediction:
    """One candidate's forecast for horizon steps 1..N at a single origin."""

    median: NDArray[np.float64]
    native_low: NDArray[np.float64] | None
    native_high: NDArray[np.float64] | None

    @property
    def horizon_steps(self) -> int:
        """How many daily steps this prediction covers."""
        return int(self.median.size)


class FittedCandidate(Protocol):
    """A candidate whose parameters are frozen; `predict` may read only days before `origin`."""

    def predict(self, history: DailySeries, origin: date, horizon_steps: int) -> Prediction:
        """Forecast target days ``origin + 1 .. origin + horizon_steps``."""


class SeasonalCandidate(Protocol):
    """A candidate family: fit on a bounded history, then predict from a later origin."""

    @property
    def name(self) -> str:
        """The immutable candidate identity written into every result row."""

    @property
    def has_native_interval(self) -> bool:
        """True when the candidate produces its own band and needs no residual calibration."""

    def fit(self, history: DailySeries) -> FittedCandidate:
        """Fit on ``history``, which the caller has already sliced to the permitted window."""


def _target_days(origin: date, horizon_steps: int) -> tuple[date, ...]:
    return tuple(origin + timedelta(days=step) for step in range(1, horizon_steps + 1))


@dataclass(frozen=True)
class _PersistenceFit:
    def predict(self, history: DailySeries, origin: date, horizon_steps: int) -> Prediction:
        _, value = history.before(origin).last_observed()
        return Prediction(median=np.full(horizon_steps, value, dtype=np.float64), native_low=None, native_high=None)


class PersistenceCandidate:
    """Carry the last observed value forward; the skill denominator for every other candidate."""

    name: Final = "persistence_v1"
    has_native_interval: Final = False

    def fit(self, history: DailySeries) -> FittedCandidate:  # noqa: ARG002 - stateless by construction
        """Persistence has no parameters; the fitted object exists so the protocol is uniform."""
        return _PersistenceFit()


@dataclass(frozen=True)
class _SeasonalNaiveFit:
    def predict(self, history: DailySeries, origin: date, horizon_steps: int) -> Prediction:
        available = history.before(origin)
        _, fallback = available.last_observed()
        medians = [_seasonal_naive_value(available, target, fallback) for target in _target_days(origin, horizon_steps)]
        return Prediction(median=np.asarray(medians, dtype=np.float64), native_low=None, native_high=None)


def _seasonal_naive_value(available: DailySeries, target: date, fallback: float) -> float:
    anchor = target - timedelta(days=SEASONAL_NAIVE_LAG_DAYS)
    for offset in range(SEASONAL_NAIVE_SEARCH_DAYS + 1):
        for signed in (offset, -offset) if offset else (0,):
            value = available.value_at(anchor + timedelta(days=signed))
            if not math.isnan(value):
                return value
    return fallback


class SeasonalNaiveCandidate:
    """Repeat the value one year before the target day, searching +/-3 days for the nearest match."""

    name: Final = "seasonal_naive_v1"
    has_native_interval: Final = False

    def fit(self, history: DailySeries) -> FittedCandidate:  # noqa: ARG002 - lookup rule, no parameters
        """Seasonal-naive is a lookup rule; nothing is estimated from the fit window."""
        return _SeasonalNaiveFit()


@dataclass(frozen=True)
class _ClimatologyFit:
    day_of_year_mean: NDArray[np.float64]
    overall_mean: float

    def predict(self, history: DailySeries, origin: date, horizon_steps: int) -> Prediction:  # noqa: ARG002
        medians: list[float] = []
        for target in _target_days(origin, horizon_steps):
            level = float(self.day_of_year_mean[_day_of_year_index(target)])
            medians.append(self.overall_mean if math.isnan(level) else level)
        return Prediction(median=np.asarray(medians, dtype=np.float64), native_low=None, native_high=None)


def _day_of_year_index(day: date) -> int:
    """Day-of-year on a fixed 366-slot ring; 29 February shares slot 59 with 1 March in common years."""
    return min(day.timetuple().tm_yday, 366) - 1


class SeasonalClimatologyCandidate:
    """Circular day-of-year mean over a +/-15-day window, refusing a slot with too few samples."""

    name: Final = "seasonal_climatology_v1"
    has_native_interval: Final = False

    def fit(self, history: DailySeries) -> FittedCandidate:
        """Estimate one level per day-of-year slot from the permitted window only."""
        observed = np.flatnonzero(~np.isnan(history.values))
        if observed.size == 0:
            raise SeasonalCandidateError(f"{history.series_key} holds no observation to build a climatology")
        slot_totals = np.zeros(366, dtype=np.float64)
        slot_counts = np.zeros(366, dtype=np.int64)
        for index in observed:
            day = history.start_date + timedelta(days=int(index))
            slot = _day_of_year_index(day)
            value = float(history.values[index])
            for offset in range(-SEASONAL_WINDOW_DAYS, SEASONAL_WINDOW_DAYS + 1):
                slot_totals[(slot + offset) % 366] += value
                slot_counts[(slot + offset) % 366] += 1
        means = np.full(366, np.nan, dtype=np.float64)
        supported = slot_counts >= MIN_CLIMATOLOGY_SAMPLES
        means[supported] = slot_totals[supported] / slot_counts[supported]
        return _ClimatologyFit(
            day_of_year_mean=means,
            overall_mean=float(np.nanmean(history.values)),
        )


@dataclass(frozen=True)
class _ElapsedTimeLinearFit:
    intercept: float
    slope_per_second: float
    epoch: date

    def predict(self, history: DailySeries, origin: date, horizon_steps: int) -> Prediction:  # noqa: ARG002
        elapsed = np.asarray(
            [float((target - self.epoch).days * 86400) for target in _target_days(origin, horizon_steps)],
            dtype=np.float64,
        )
        return Prediction(
            median=self.intercept + self.slope_per_second * elapsed,
            native_low=None,
            native_high=None,
        )


class ElapsedTimeLinearCandidate:
    """The warehouse's `sql_linear` method reimplemented: least squares of value on elapsed UTC time.

    This is deliberately a trend-only regression, exactly as the shipped SQL functions describe it.
    It is not a ridge on seasonal features wearing the `sql_linear` name.
    """

    name: Final = "sql_linear_elapsed_time_v1"
    has_native_interval: Final = False

    def fit(self, history: DailySeries) -> FittedCandidate:
        """Estimate intercept and slope on elapsed seconds since the first observed day."""
        observed = np.flatnonzero(~np.isnan(history.values))
        if observed.size < MINIMUM_LINEAR_FIT_POINTS:
            raise SeasonalCandidateError(f"{history.series_key} needs two observations for a linear fit")
        epoch = history.start_date
        elapsed = observed.astype(np.float64) * 86400.0
        values = history.values[observed]
        design = np.column_stack((np.ones_like(elapsed), elapsed))
        solution, _, _, _ = np.linalg.lstsq(design, values, rcond=None)
        return _ElapsedTimeLinearFit(
            intercept=float(solution[0]),
            slope_per_second=float(solution[1]),
            epoch=epoch,
        )


def _deterministic_generator(seed: int, series_key: str, origin: date) -> np.random.Generator:
    """Seed a generator from a SHA-256 of the run identity so a rerun reproduces the same draws."""
    digest = hashlib.sha256(f"{seed}|{series_key}|{origin.isoformat()}".encode()).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "big"))


@dataclass(frozen=True)
class _DailyIncrementBootstrapFit:
    increments: NDArray[np.float64]
    seed: int
    path_count: int
    lower_bound: float | None
    upper_bound: float | None

    def predict(self, history: DailySeries, origin: date, horizon_steps: int) -> Prediction:
        available = history.before(origin)
        _, anchor = available.last_observed()
        generator = _deterministic_generator(self.seed, history.series_key, origin)
        draws = generator.integers(0, self.increments.size, size=(self.path_count, horizon_steps))
        paths = anchor + np.cumsum(self.increments[draws], axis=1)
        if self.lower_bound is not None or self.upper_bound is not None:
            paths = np.clip(paths, self.lower_bound, self.upper_bound)
        quantiles = np.percentile(paths, (LOW_QUANTILE, 50.0, HIGH_QUANTILE), axis=0, method="linear")
        return Prediction(
            median=np.asarray(quantiles[1], dtype=np.float64),
            native_low=np.asarray(quantiles[0], dtype=np.float64),
            native_high=np.asarray(quantiles[2], dtype=np.float64),
        )


class DailyIncrementBootstrapCandidate:
    """The warehouse's `daily_increment_bootstrap_v1` reimplemented database-free.

    It resamples consecutive-calendar-day first differences and accumulates them into paths, exactly
    as `agri.forecast_daily_bootstrap` describes. The index stream is derived from a SHA-256 of the
    run identity, so a rerun is reproducible; it is **not** a byte-identical replay of the SQL
    function's own index stream, and no result here should be reported as one. v1 assumes
    exchangeable daily increments and models neither seasonality nor autocorrelation.
    """

    name: Final = "daily_increment_bootstrap_v1"
    has_native_interval: Final = True

    def __init__(
        self,
        *,
        seed: int = DEFAULT_SEED,
        path_count: int = BOOTSTRAP_PATH_COUNT,
        lower_bound: float | None = None,
        upper_bound: float | None = None,
    ) -> None:
        self._seed = seed
        self._path_count = path_count
        self._lower_bound = lower_bound
        self._upper_bound = upper_bound

    def fit(self, history: DailySeries) -> FittedCandidate:
        """Collect the consecutive-day increments the permitted window contains."""
        values = history.values
        increments = values[1:] - values[:-1]
        usable = increments[~np.isnan(increments)]
        if usable.size < MINIMUM_BOOTSTRAP_INCREMENTS:
            raise SeasonalCandidateError(
                f"{history.series_key} has {usable.size} consecutive-day increment(s); the bootstrap needs two"
            )
        return _DailyIncrementBootstrapFit(
            increments=usable.astype(np.float64),
            seed=self._seed,
            path_count=self._path_count,
            lower_bound=self._lower_bound,
            upper_bound=self._upper_bound,
        )


def ridge_feature_names() -> tuple[str, ...]:
    """The pinned, ordered feature vector of the regularized lag/seasonal candidate."""
    names = [f"lag_{lag}" for lag in RIDGE_LAG_DAYS]
    names.extend(f"rolling_mean_{window}" for window in RIDGE_ROLLING_WINDOWS)
    names.extend(("sin_annual", "cos_annual", "sin_semiannual", "cos_semiannual"))
    return tuple(names)


def _ridge_features(history: DailySeries, origin: date, target: date) -> NDArray[np.float64] | None:
    """Feature row for one (origin, target) pair, or None when any input is unavailable."""
    row: list[float] = []
    for lag in RIDGE_LAG_DAYS:
        value = history.value_at(origin - timedelta(days=lag))
        if math.isnan(value):
            return None
        row.append(value)
    for window in RIDGE_ROLLING_WINDOWS:
        stop = history.index_of(origin)
        start = stop - window
        if start < 0 or stop > history.values.size:
            return None
        segment = history.values[start:stop]
        if segment.size != window or bool(np.isnan(segment).any()):
            return None
        row.append(float(segment.mean()))
    angle = 2.0 * math.pi * (_day_of_year_index(target) + 1) / 366.0
    row.extend((math.sin(angle), math.cos(angle), math.sin(2.0 * angle), math.cos(2.0 * angle)))
    return np.asarray(row, dtype=np.float64)


@dataclass(frozen=True)
class _RidgeHorizonModel:
    feature_mean: NDArray[np.float64]
    feature_scale: NDArray[np.float64]
    weights: NDArray[np.float64]
    intercept: float

    def predict_row(self, row: NDArray[np.float64]) -> float:
        """Apply the standardization and weights fitted inside the training fold."""
        standardized = (row - self.feature_mean) / self.feature_scale
        return float(self.intercept + standardized @ self.weights)


@dataclass(frozen=True)
class _RidgeFit:
    models: tuple[_RidgeHorizonModel | None, ...]
    penalty: float
    fallback: float

    def predict(self, history: DailySeries, origin: date, horizon_steps: int) -> Prediction:
        available = history.before(origin)
        medians: list[float] = []
        for step, target in enumerate(_target_days(origin, horizon_steps), start=1):
            model = self.models[step - 1] if step - 1 < len(self.models) else None
            row = _ridge_features(available, origin, target)
            medians.append(self.fallback if model is None or row is None else model.predict_row(row))
        return Prediction(median=np.asarray(medians, dtype=np.float64), native_low=None, native_high=None)


def _solve_ridge(features: NDArray[np.float64], targets: NDArray[np.float64], penalty: float) -> _RidgeHorizonModel:
    mean = features.mean(axis=0)
    scale = features.std(axis=0)
    scale[scale == 0.0] = 1.0
    standardized = (features - mean) / scale
    target_mean = float(targets.mean())
    centered = targets - target_mean
    gram = standardized.T @ standardized + penalty * np.eye(standardized.shape[1])
    weights = np.linalg.solve(gram, standardized.T @ centered)
    return _RidgeHorizonModel(feature_mean=mean, feature_scale=scale, weights=weights, intercept=target_mean)


def _day_of_year_ring(start_date: date, length: int) -> NDArray[np.float64]:
    """Day-of-year slot (0-365) for each grid index, precomputed once per training-row build."""
    return np.asarray(
        [float(_day_of_year_index(start_date + timedelta(days=index))) for index in range(length)],
        dtype=np.float64,
    )


def _rolling_means(values: NDArray[np.float64], window: int) -> NDArray[np.float64]:
    """`out[i]` is the mean of `values[i - window : i]`, NaN where the window is short or has a gap."""
    out = np.full(values.size, np.nan, dtype=np.float64)
    if values.size < window:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(values, window)
    out[window:] = windows[:-1].mean(axis=1)
    return out


def _ridge_training_rows(
    history: DailySeries,
    horizon_step: int,
    minimum_feature_days: int,
) -> tuple[NDArray[np.float64], NDArray[np.float64], tuple[date, ...]]:
    """Vectorized design matrix for one horizon: one row per usable pseudo-origin inside the window."""
    values = history.values
    size = int(values.size)
    first_index = max(minimum_feature_days, *RIDGE_LAG_DAYS)
    last_index = size - horizon_step
    if last_index <= first_index:
        return np.zeros((0, len(ridge_feature_names()))), np.zeros(0), ()
    indices = np.arange(first_index, last_index)
    columns: list[NDArray[np.float64]] = [values[indices - lag] for lag in RIDGE_LAG_DAYS]
    columns.extend(_rolling_means(values, window)[indices] for window in RIDGE_ROLLING_WINDOWS)
    ring = _day_of_year_ring(history.start_date, size + horizon_step)
    angle = 2.0 * np.pi * (ring[indices + horizon_step] + 1.0) / 366.0
    columns.extend((np.sin(angle), np.cos(angle), np.sin(2.0 * angle), np.cos(2.0 * angle)))
    features = np.column_stack(columns)
    targets = values[indices + horizon_step]
    usable = ~np.isnan(features).any(axis=1) & ~np.isnan(targets)
    if not bool(usable.any()):
        return np.zeros((0, len(ridge_feature_names()))), np.zeros(0), ()
    kept = indices[usable]
    return (
        features[usable],
        targets[usable],
        tuple(history.start_date + timedelta(days=int(index)) for index in kept),
    )


class RegularizedLagSeasonalRidgeCandidate:
    """Direct per-horizon ridge on standardized lag, rolling-mean and harmonic seasonal features.

    Every choice that could leak -- lag construction, standardization, the harmonic definition and
    the penalty -- is made from the fit window alone. The penalty is selected on an inner temporal
    split of that window, never on the scored days.
    """

    name: Final = "regularized_lag_seasonal_ridge_v1"
    has_native_interval: Final = False

    def __init__(self, *, horizon_steps: int, minimum_feature_history_days: int = max(RIDGE_ROLLING_WINDOWS)) -> None:
        self._horizon_steps = horizon_steps
        self._minimum_feature_history_days = minimum_feature_history_days

    def fit(self, history: DailySeries) -> FittedCandidate:
        """Select one penalty on an inner temporal split, then refit every horizon model on the fold."""
        penalty = self._select_penalty(history)
        models: list[_RidgeHorizonModel | None] = []
        for step in range(1, self._horizon_steps + 1):
            features, targets, _ = _ridge_training_rows(history, step, self._minimum_feature_history_days)
            models.append(None if features.shape[0] <= features.shape[1] else _solve_ridge(features, targets, penalty))
        observed = history.values[~np.isnan(history.values)]
        if observed.size == 0:
            raise SeasonalCandidateError(f"{history.series_key} holds no observation to fit a ridge")
        return _RidgeFit(models=tuple(models), penalty=penalty, fallback=float(observed[-1]))

    def _select_penalty(self, history: DailySeries) -> float:
        errors: dict[float, list[float]] = {penalty: [] for penalty in RIDGE_PENALTY_GRID}
        for step in range(1, self._horizon_steps + 1):
            features, targets, pseudo_origins = _ridge_training_rows(history, step, self._minimum_feature_history_days)
            if features.shape[0] < 4 * features.shape[1]:
                continue
            split = int(len(pseudo_origins) * (1.0 - RIDGE_INNER_VALIDATION_FRACTION))
            if split <= features.shape[1] or split >= features.shape[0]:
                continue
            inner_features, inner_targets = features[:split], targets[:split]
            validation_features, validation_targets = features[split:], targets[split:]
            for penalty in RIDGE_PENALTY_GRID:
                model = _solve_ridge(inner_features, inner_targets, penalty)
                standardized = (validation_features - model.feature_mean) / model.feature_scale
                predictions = model.intercept + standardized @ model.weights
                errors[penalty].append(float(np.abs(predictions - validation_targets).mean()))
        scored = {penalty: float(np.mean(values)) for penalty, values in errors.items() if values}
        if not scored:
            return 1.0
        return min(sorted(scored), key=lambda penalty: scored[penalty])


def default_candidates(*, horizon_steps: int, seed: int = DEFAULT_SEED) -> tuple[SeasonalCandidate, ...]:
    """The pre-registered ladder, in the order the results table reports it."""
    return (
        PersistenceCandidate(),
        SeasonalNaiveCandidate(),
        SeasonalClimatologyCandidate(),
        ElapsedTimeLinearCandidate(),
        DailyIncrementBootstrapCandidate(seed=seed),
        RegularizedLagSeasonalRidgeCandidate(horizon_steps=horizon_steps),
    )


def candidate_names(candidates: Sequence[SeasonalCandidate]) -> tuple[str, ...]:
    """The immutable identities of a candidate ladder."""
    return tuple(candidate.name for candidate in candidates)
