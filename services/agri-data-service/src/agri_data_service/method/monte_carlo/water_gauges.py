"""Deterministic log-space, mean-reverting AR(1) anomaly-bootstrap for USGS discharge, per gauge.

Layer L1 (method/monte_carlo): Pure domain computation, no I/O, no SQLAlchemy, no httpx.
See `docs/lanes/water-gauges.md` §3 (history depth) and §7 (why this method, not a seasonal one)
and `conductor/code_styleguides/layer-lanes.md` §3 (the provenance columns every row here carries).
Shaped after `method/monte_carlo/vegetation_ndvi_forecast.py`, the only prior Monte Carlo in this
codebase, with two deliberate departures explained inline: no seasonal climatology (§7 -- the dense
record is measured at ~3 months, not enough to resolve a day-of-year cycle), and a log-space
transform instead of a raw additive anomaly (discharge is non-negative and heavy-tailed; NDVI is
bounded on both sides and roughly symmetric).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Final

import numpy
from numpy.random import PCG64

if TYPE_CHECKING:
    from numpy.typing import NDArray

METHOD_NAME: Final = "water_gauges_lognormal_ar1_bootstrap_v1"
PURPOSE_FORWARD_SIMULATION: Final = "forward_simulation"
PURPOSE_HOLDOUT_EVALUATION: Final = "holdout_evaluation"
GAP_POLICY: Final = "strict"

# docs/lanes/water-gauges.md section 3: the declared 2022-08-05 code floor is borrowed from the
# vegetation lane, not measured against this source, and the best evidence in THIS repo for the
# DENSE, continuous record is ~2026-05-24 onward -- roughly three months as of that (stale, by the
# doc's own admission) snapshot. This constant is deliberately far below the wrong four-year
# assumption it replaces, and below even the ~3-month measured depth, as a conservative placeholder
# pending the fresh gap-clustering re-measurement docs/lanes/water-gauges.md §3 calls for.
MIN_TRAINING_DAYS: Final = 60

# A river's day-to-day autocorrelation decays over days, not the ~30-day window NDVI's seasonal
# cycle needs: baseflow recession and storm response both play out within a week. A gap wider than
# this is "too stale to inform persistence" rather than stretched across as if it were continuous.
MAX_AUTOCORRELATION_GAP_DAYS: Final = 7
MIN_AUTOCORRELATION_PAIRS: Final = 8
# Below this the empirical anomaly pool cannot support a stable flood-tail estimate: the same
# handful of draws would repeat as the most extreme historical anomaly on nearly every simulation.
MIN_INNOVATION_SAMPLES: Final = 20
MAX_DAILY_PERSISTENCE: Final = 0.98

LOW_QUANTILE: Final = 0.1
MEDIAN_QUANTILE: Final = 0.5
HIGH_QUANTILE: Final = 0.9
# The flood tail is the shape this lane's brief calls out explicitly (docs/lanes/water-gauges.md
# §7, "flood peaks are the tail that matters"): a symmetric p10/p50/p90 band understates it, so a
# dedicated far-tail quantile is published rather than only p90.
FLOOD_TAIL_QUANTILE: Final = 0.99
QUANTILE_LEVELS: Final[tuple[float, ...]] = (LOW_QUANTILE, MEDIAN_QUANTILE, HIGH_QUANTILE, FLOOD_TAIL_QUANTILE)
# Publishing p99 from fewer than this many draws means the 99th percentile is set by only one or
# two of the most extreme draws in the whole ensemble -- indistinguishable from noise in exactly
# the tail this method exists to inform. Below this floor the flood-tail quantile is dropped
# instead, per layer-lanes.md section 3: "if the ensemble is too small... publish fewer."
MIN_ENSEMBLE_FOR_FLOOD_TAIL: Final = 200

FLOAT_FINGERPRINT_FORMAT: Final = ".17g"
MIN_HORIZON_DAYS: Final = 1
MAX_HORIZON_DAYS: Final = 30


class InsufficientWaterGaugesHistoryError(ValueError):
    """Raised when a gauge's own governed history cannot support this method without fabrication.

    An explicit, typed refusal rather than an empty result: docs/lanes/water-gauges.md section 7
    calls for a method that degrades gracefully with limited history, which means refusing loudly
    below its floor -- naming exactly which precondition failed -- never silently returning
    something that could be mistaken for "the gauge forecasts to nothing."
    """

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ObservedReading:
    """One (site, instant) discharge reading at or before some cutoff -- the method's raw input unit.

    Field-for-field the written `kind=observed` grain (`warehouse/schemas/water_gauges.py`),
    including its silent-gauge convention: `flow_cfs=None` is a real tick that reported nothing,
    never fabricated as zero and never dropped from the input.
    """

    observed_at: datetime
    observed_day: date
    flow_cfs: float | None
    observation_checksum: str


@dataclass(frozen=True, slots=True)
class DailyDischarge:
    """One gauge's own daily discharge level: the mean of one day's non-null readings."""

    day: date
    mean_flow_cfs: float
    reading_count: int


@dataclass(frozen=True, slots=True)
class SiteDischargeHistory:
    """A gauge's own history reduced to a daily series, a persistence estimate, and an anomaly pool."""

    site_number: str
    cutoff_day: date
    history_start_day: date
    governed_reading_count: int
    daily_observation_count: int
    baseline_log_level: float
    anomaly_values: tuple[float, ...]
    latest_observed_day: date
    latest_observed_log_level: float
    anchor_day: date
    anchor_anomaly: float
    lag_one_autocorrelation: float
    autocorrelation_pair_count: int
    mean_observation_gap_days: float
    daily_persistence: float


@dataclass(frozen=True, slots=True)
class ForecastRequest:
    """Bounded, reproducible simulation parameters, and the identifiers stamped on every output row."""

    forecast_run_id: str
    random_seed: int
    ensemble_size: int
    horizon_days: int

    def __post_init__(self) -> None:
        if not (MIN_HORIZON_DAYS <= self.horizon_days <= MAX_HORIZON_DAYS):
            raise ValueError(f"horizon_days must be between {MIN_HORIZON_DAYS} and {MAX_HORIZON_DAYS}")
        if self.ensemble_size < 1:
            raise ValueError("ensemble_size must be positive")
        if not self.forecast_run_id.strip():
            raise ValueError("forecast_run_id must be non-blank")


@dataclass(frozen=True, slots=True)
class HorizonQuantileRow:
    """One (site, horizon-day, quantile) forecast row -- every layer-lanes.md section 3 provenance
    column present, none borrowed from an observed row's own lineage.
    """

    site_number: str
    horizon_step: int
    valid_day: date
    quantile: float
    flow_cfs: float
    forecast_run_id: str
    random_seed: int
    ensemble_size: int
    horizon_days: int
    issued_on: date


def log_level(flow_cfs: float) -> float:
    """Return the log-space level of a non-negative discharge reading; `log1p(0.0) == 0.0` exactly."""
    return math.log1p(flow_cfs)


def discharge_from_log_level(value: float) -> float:
    """Invert `log_level`, clamped at zero -- the one line that makes a negative discharge impossible.

    `expm1` alone maps every real number to `(-1, +inf)`, so a sufficiently negative simulated
    log-level could still invert to a small negative number; this hard floor is kept explicit
    rather than trusted as a mathematical guarantee.
    """
    return max(0.0, math.expm1(value))


def eligible_history(readings: tuple[ObservedReading, ...], cutoff_day: date) -> tuple[ObservedReading, ...]:
    """Return the leakage-free history: readings at or before the cutoff day, in true-instant order."""
    kept = [row for row in readings if row.observed_day <= cutoff_day]
    kept.sort(key=lambda row: (row.observed_day, row.observed_at))
    return tuple(kept)


def build_daily_series(readings: tuple[ObservedReading, ...], cutoff_day: date) -> tuple[DailyDischarge, ...]:
    """Collapse eligible sub-daily readings to one mean-discharge value per day.

    This method forecasts at DAILY grain (see module docstring and `planes/water_gauges.py`): a
    30-day-ahead ensemble cannot honestly predict which INSTANT a future NWIS poll will land on,
    only the day's level, so same-day ticks are averaged here, inside this method's own input
    reduction -- the written observed schema's true sub-daily grain is untouched by this collapse.

    A day where every reading's `flow_cfs` is null contributes NOTHING to the series, rather than a
    fabricated zero: that would misrepresent a silent gauge tick as a measured drought reading. Such
    a day is simply absent from the returned series, exactly like a day the gauge never reported at
    all.
    """
    kept = eligible_history(readings, cutoff_day)
    by_day: dict[date, list[float]] = {}
    for row in kept:
        if row.flow_cfs is None:
            continue
        if row.flow_cfs < 0:
            raise InsufficientWaterGaugesHistoryError(
                "negative_discharge_reading",
                f"{row.observed_day.isoformat()} carries a negative flow_cfs ({row.flow_cfs}); this "
                "method's log-space transform is undefined below zero, and genuine reverse flow at "
                "some gauges (docs/lanes/water-gauges.md section 5, down to -172,000 cfs) needs a "
                "dedicated method rather than a silent clamp that would misstate its magnitude",
            )
        by_day.setdefault(row.observed_day, []).append(row.flow_cfs)
    days = sorted(by_day)
    return tuple(
        DailyDischarge(day=day, mean_flow_cfs=sum(by_day[day]) / len(by_day[day]), reading_count=len(by_day[day]))
        for day in days
    )


def _estimate_daily_persistence(
    days: tuple[date, ...],
    anomalies: NDArray[numpy.float64],
) -> tuple[float, float, int, float]:
    leading: list[float] = []
    trailing: list[float] = []
    gaps: list[int] = []
    for index in range(1, len(days)):
        gap_days = (days[index] - days[index - 1]).days
        if 1 <= gap_days <= MAX_AUTOCORRELATION_GAP_DAYS:
            leading.append(float(anomalies[index - 1]))
            trailing.append(float(anomalies[index]))
            gaps.append(gap_days)
    pair_count = len(gaps)
    if pair_count < MIN_AUTOCORRELATION_PAIRS:
        raise InsufficientWaterGaugesHistoryError(
            "autocorrelation_pairs_below_minimum",
            f"{pair_count} usable day-to-day pairs (gap <= {MAX_AUTOCORRELATION_GAP_DAYS}d) below "
            f"the required {MIN_AUTOCORRELATION_PAIRS}",
        )
    leading_array = numpy.asarray(leading, dtype=numpy.float64)
    trailing_array = numpy.asarray(trailing, dtype=numpy.float64)
    if float(leading_array.std()) == 0.0 or float(trailing_array.std()) == 0.0:
        raise InsufficientWaterGaugesHistoryError(
            "anomaly_pool_has_no_spread",
            "this gauge's own anomaly pool is degenerate (constant discharge), so no honest band can be simulated",
        )
    correlation = float(numpy.corrcoef(leading_array, trailing_array)[0, 1])
    if not math.isfinite(correlation):
        raise InsufficientWaterGaugesHistoryError(
            "autocorrelation_not_finite", "this gauge's own day-to-day autocorrelation is not finite"
        )
    mean_gap_days = float(numpy.asarray(gaps, dtype=numpy.float64).mean())
    clamped = min(max(correlation, 0.0), MAX_DAILY_PERSISTENCE)
    return clamped ** (1.0 / mean_gap_days), correlation, pair_count, mean_gap_days


def build_site_history(
    site_number: str,
    readings: tuple[ObservedReading, ...],
    cutoff_day: date,
) -> SiteDischargeHistory:
    """Reduce one gauge's own governed history at or before the cutoff day to the method's state.

    No day-of-year climatology: unlike vegetation's seasonal-anomaly bootstrap, this lane's dense
    record is not yet deep enough to resolve a seasonal cycle (docs/lanes/water-gauges.md section
    7), so the baseline this gauge reverts to is its own flat, all-time mean log-level over the
    training window rather than a day-of-year mean.
    """
    if not site_number.strip():
        raise ValueError("site_number must be non-blank")
    daily_series = build_daily_series(readings, cutoff_day)
    if len(daily_series) < MIN_TRAINING_DAYS:
        raise InsufficientWaterGaugesHistoryError(
            "training_days_below_minimum",
            f"{len(daily_series)} distinct daily readings for {site_number} at or before "
            f"{cutoff_day.isoformat()}, below the required {MIN_TRAINING_DAYS}",
        )
    days = tuple(row.day for row in daily_series)
    if len(set(days)) != len(days):
        raise InsufficientWaterGaugesHistoryError(
            "duplicate_observed_day", f"{site_number}'s daily series carries more than one row for one day"
        )

    log_levels = numpy.asarray([log_level(row.mean_flow_cfs) for row in daily_series], dtype=numpy.float64)
    baseline = float(log_levels.mean())
    anomalies = log_levels - baseline

    daily_persistence, correlation, pair_count, mean_gap_days = _estimate_daily_persistence(days, anomalies)

    if anomalies.size < MIN_INNOVATION_SAMPLES:
        raise InsufficientWaterGaugesHistoryError(
            "innovation_pool_below_minimum",
            f"{anomalies.size} daily anomalies for {site_number}, below the required {MIN_INNOVATION_SAMPLES}",
        )

    return SiteDischargeHistory(
        site_number=site_number,
        cutoff_day=cutoff_day,
        history_start_day=days[0],
        governed_reading_count=len(eligible_history(readings, cutoff_day)),
        daily_observation_count=len(daily_series),
        baseline_log_level=baseline,
        anomaly_values=tuple(float(value) for value in anomalies),
        latest_observed_day=days[-1],
        latest_observed_log_level=float(log_levels[-1]),
        anchor_day=days[-1],
        anchor_anomaly=float(anomalies[-1]),
        lag_one_autocorrelation=correlation,
        autocorrelation_pair_count=pair_count,
        mean_observation_gap_days=mean_gap_days,
        daily_persistence=daily_persistence,
    )


def history_checksum(site_number: str, readings: tuple[ObservedReading, ...], cutoff_day: date) -> str:
    """Return a sha256 digest over the ordered governed history one simulation consumed."""
    parts = [
        "|".join(
            (
                row.observed_day.isoformat(),
                row.observed_at.isoformat(),
                "null" if row.flow_cfs is None else format(row.flow_cfs, FLOAT_FINGERPRINT_FORMAT),
                row.observation_checksum,
            )
        )
        for row in eligible_history(readings, cutoff_day)
    ]
    canonical = "|".join(("water_gauges_history_v1", site_number, *parts))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _quantile_levels_for(ensemble_size: int) -> tuple[float, ...]:
    """Drop the flood-tail quantile when the ensemble is too small to support it honestly."""
    if ensemble_size < MIN_ENSEMBLE_FOR_FLOOD_TAIL:
        return QUANTILE_LEVELS[:-1]
    return QUANTILE_LEVELS


def canonical_parameter_text(
    *,
    site_number: str,
    history_checksum_value: str,
    history: SiteDischargeHistory,
    request: ForecastRequest,
) -> str:
    """Return the pinned canonical parameter text whose digest reproduces one simulation exactly."""
    published_quantiles = "_".join(f"p{round(level * 100)}" for level in _quantile_levels_for(request.ensemble_size))
    fields = (
        METHOD_NAME,
        site_number,
        history_checksum_value,
        history.cutoff_day.isoformat(),
        history.history_start_day.isoformat(),
        str(request.horizon_days),
        str(request.ensemble_size),
        str(request.random_seed),
        request.forecast_run_id,
        GAP_POLICY,
        str(MIN_TRAINING_DAYS),
        str(MIN_AUTOCORRELATION_PAIRS),
        str(MAX_AUTOCORRELATION_GAP_DAYS),
        str(MIN_INNOVATION_SAMPLES),
        str(history.daily_observation_count),
        format(history.daily_persistence, FLOAT_FINGERPRINT_FORMAT),
        format(history.baseline_log_level, FLOAT_FINGERPRINT_FORMAT),
        format(history.anchor_anomaly, FLOAT_FINGERPRINT_FORMAT),
        history.anchor_day.isoformat(),
        str(history.governed_reading_count),
        "pcg64_random_raw",
        published_quantiles,
        "log1p_expm1_nonnegative_clamp",
    )
    return "|".join(fields)


def parameter_checksum(canonical_text: str) -> str:
    """Return the sha256 hex digest that binds one simulation's parameters and its random stream."""
    return hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()


def simulate_horizon_quantiles(
    *,
    history: SiteDischargeHistory,
    request: ForecastRequest,
    checksum: str,
) -> tuple[HorizonQuantileRow, ...]:
    """Simulate marginal per-horizon-day discharge quantiles from this gauge's own anomaly pool.

    Log-space, mean-reverting AR(1): discharge is modelled as
    `max(0, expm1(baseline + decayed_anomaly + scaled_bootstrap_innovation))`, never a raw additive
    anomaly on cfs directly. Exponentiating a symmetric log-space draw is what produces the
    right-skewed, heavy-tailed shape real discharge has -- a moderate log-anomaly already maps to a
    large cfs value, which is exactly the flood-tail behaviour a linear model would understate -- and
    it makes a negative discharge structurally near-impossible before `discharge_from_log_level`'s
    explicit floor even has to do any work. Quantiles are read off the simulated ensemble itself via
    `numpy.percentile`, never fitted to a parametric distribution after the fact, per
    layer-lanes.md section 3.
    """
    valid_days = tuple(history.cutoff_day + timedelta(days=step) for step in range(1, request.horizon_days + 1))
    anomaly_pool = numpy.asarray(history.anomaly_values, dtype=numpy.float64)
    anchor_gap_days = (history.cutoff_day - history.anchor_day).days
    horizon_offsets = numpy.arange(1, request.horizon_days + 1, dtype=numpy.float64)
    decay = numpy.power(history.daily_persistence, anchor_gap_days + horizon_offsets)
    innovation_scale = numpy.sqrt(numpy.maximum(1.0 - numpy.square(decay), 0.0))

    raw_draws = PCG64(int(checksum, 16)).random_raw(request.ensemble_size * request.horizon_days)
    draw_matrix = numpy.asarray(raw_draws, dtype=numpy.uint64).reshape(request.ensemble_size, request.horizon_days)
    pool_indices = numpy.mod(draw_matrix, numpy.uint64(anomaly_pool.size)).astype(numpy.intp)
    innovations = anomaly_pool[pool_indices]

    log_paths = history.baseline_log_level + decay * history.anchor_anomaly + innovation_scale * innovations
    discharge_paths = numpy.maximum(0.0, numpy.expm1(log_paths))

    levels = _quantile_levels_for(request.ensemble_size)
    quantile_values = numpy.percentile(discharge_paths, [level * 100.0 for level in levels], axis=0, method="linear")

    rows: list[HorizonQuantileRow] = []
    for step in range(request.horizon_days):
        for level_index, level in enumerate(levels):
            rows.append(
                HorizonQuantileRow(
                    site_number=history.site_number,
                    horizon_step=step + 1,
                    valid_day=valid_days[step],
                    quantile=level,
                    flow_cfs=float(quantile_values[level_index, step]),
                    forecast_run_id=request.forecast_run_id,
                    random_seed=request.random_seed,
                    ensemble_size=request.ensemble_size,
                    horizon_days=request.horizon_days,
                    issued_on=history.cutoff_day,
                )
            )
    return tuple(rows)


def simulate_water_gauges_forecast(
    *,
    site_number: str,
    readings: tuple[ObservedReading, ...],
    cutoff_day: date,
    request: ForecastRequest,
) -> tuple[HorizonQuantileRow, ...]:
    """Build one gauge's history, bind it to a checksum, and simulate its 30-day forecast ensemble.

    The single entry point a future exporter calls. Every intermediate step above stays public for
    a caller -- or a time-honest holdout evaluation (docs/lanes/water-gauges.md section 7) -- that
    needs the history or the checksum on its own. Raises `InsufficientWaterGaugesHistoryError`
    rather than returning an empty tuple when this gauge's history cannot support the method.
    """
    history = build_site_history(site_number, readings, cutoff_day)
    checksum = parameter_checksum(
        canonical_parameter_text(
            site_number=site_number,
            history_checksum_value=history_checksum(site_number, readings, cutoff_day),
            history=history,
            request=request,
        )
    )
    return simulate_horizon_quantiles(history=history, request=request, checksum=checksum)
