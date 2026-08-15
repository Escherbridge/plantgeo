"""Rolling-origin evaluation harness: origins, folds, calibration, metrics and block bootstrap.

Database-free. The harness owns the one rule the candidates cannot enforce for themselves -- a
candidate is never handed a history that reaches its own origin -- and it raises rather than
warning when that rule is broken.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final, Literal

import numpy as np

from agri_data_service.method.ml.seasonal_candidates import (
    DailySeries,
    Prediction,
    SeasonalCandidateError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from numpy.typing import NDArray

    from agri_data_service.method.ml.seasonal_candidates import SeasonalCandidate

FoldKind = Literal["development", "final_holdout"]

MINIMUM_HISTORY_DAYS: Final = 730
HORIZON_STEPS: Final = 30
ORIGIN_STRIDE_DAYS: Final = 30
CALIBRATION_SEGMENT_DAYS: Final = 365
CALIBRATION_PSEUDO_ORIGIN_STRIDE_DAYS: Final = 7
NOMINAL_INTERVAL_COVERAGE: Final = 0.80
BOOTSTRAP_DRAW_COUNT: Final = 2000
BOOTSTRAP_SEED: Final = 20260814
MAPE_MINIMUM_ABSOLUTE_ACTUAL: Final = 1.0
MINIMUM_DEVELOPMENT_ORIGINS: Final = 8
MINIMUM_FINAL_HOLDOUT_ORIGINS: Final = 6
MINIMUM_TARGET_DAY_COVERAGE: Final = 0.95
MINIMUM_BOOTSTRAP_BLOCKS: Final = 2

_SEASON_BY_MONTH: Final[Mapping[int, str]] = {
    12: "DJF",
    1: "DJF",
    2: "DJF",
    3: "MAM",
    4: "MAM",
    5: "MAM",
    6: "JJA",
    7: "JJA",
    8: "JJA",
    9: "SON",
    10: "SON",
    11: "SON",
}


class SeasonalLeakageError(AssertionError):
    """A candidate was about to be handed a value dated on or after its own origin."""


@dataclass(frozen=True)
class OriginPlan:
    """One simulated origin and the target days it is scored on."""

    origin: date
    fold_kind: FoldKind
    target_days: tuple[date, ...]


@dataclass(frozen=True)
class RollingOriginPlan:
    """The pre-registered origin schedule for one series."""

    history_start: date
    last_observed: date
    minimum_history_days: int
    horizon_steps: int
    stride_days: int
    holdout_boundary: date
    origins: tuple[OriginPlan, ...]

    def by_fold(self, fold_kind: FoldKind) -> tuple[OriginPlan, ...]:
        """The origins belonging to one fold."""
        return tuple(plan for plan in self.origins if plan.fold_kind == fold_kind)


def build_rolling_origin_plan(  # noqa: PLR0913 - one keyword per pre-registered schedule knob
    *,
    history_start: date,
    last_observed: date,
    holdout_boundary: date,
    minimum_history_days: int = MINIMUM_HISTORY_DAYS,
    horizon_steps: int = HORIZON_STEPS,
    stride_days: int = ORIGIN_STRIDE_DAYS,
) -> RollingOriginPlan:
    """Build the expanding-window origin schedule; consecutive target windows never overlap."""
    if stride_days < horizon_steps:
        raise ValueError("stride must be at least the horizon so target windows do not overlap")
    first_origin = history_start + timedelta(days=minimum_history_days)
    origins: list[OriginPlan] = []
    origin = first_origin
    while origin + timedelta(days=horizon_steps) <= last_observed:
        fold: FoldKind = "final_holdout" if origin >= holdout_boundary else "development"
        origins.append(
            OriginPlan(
                origin=origin,
                fold_kind=fold,
                target_days=tuple(origin + timedelta(days=step) for step in range(1, horizon_steps + 1)),
            )
        )
        origin = origin + timedelta(days=stride_days)
    return RollingOriginPlan(
        history_start=history_start,
        last_observed=last_observed,
        minimum_history_days=minimum_history_days,
        horizon_steps=horizon_steps,
        stride_days=stride_days,
        holdout_boundary=holdout_boundary,
        origins=tuple(origins),
    )


@dataclass(frozen=True)
class ScoredOrigin:
    """One candidate's scored forecast at one origin."""

    series_key: str
    candidate_name: str
    origin: date
    fold_kind: FoldKind
    target_days: tuple[date, ...]
    horizon_steps: tuple[int, ...]
    median: tuple[float, ...]
    low: tuple[float, ...]
    high: tuple[float, ...]
    actual: tuple[float, ...]

    @property
    def scored_day_count(self) -> int:
        """How many target days carried an actual and were scored."""
        return len(self.actual)


@dataclass(frozen=True)
class CandidateRun:
    """Everything one candidate produced over one series' whole origin schedule."""

    series_key: str
    candidate_name: str
    scored_origins: tuple[ScoredOrigin, ...]
    leakage_checked_calls: int
    skipped_origins: tuple[tuple[date, str], ...]

    def by_fold(self, fold_kind: FoldKind) -> tuple[ScoredOrigin, ...]:
        """The scored origins belonging to one fold."""
        return tuple(scored for scored in self.scored_origins if scored.fold_kind == fold_kind)


def _require_history_before(history: DailySeries, origin: date, series_key: str) -> None:
    """Refuse to hand a candidate anything dated on or after the origin it forecasts from."""
    if history.values.size and history.end_date >= origin:
        raise SeasonalLeakageError(
            f"{series_key}: history reaches {history.end_date.isoformat()} at origin {origin.isoformat()}"
        )


def _calibration_bands(
    series: DailySeries,
    candidate: SeasonalCandidate,
    origin: date,
    horizon_steps: int,
    leakage_counter: list[int],
) -> tuple[NDArray[np.float64], NDArray[np.float64]] | None:
    """Empirical p10/p90 residual offsets per horizon, from a held-out segment inside the fit window."""
    calibration_cutoff = origin - timedelta(days=CALIBRATION_SEGMENT_DAYS)
    fit_window = series.before(calibration_cutoff)
    _require_history_before(fit_window, calibration_cutoff, series.series_key)
    leakage_counter[0] += 1
    if fit_window.observed_day_count() < horizon_steps + 2:
        return None
    try:
        fitted = candidate.fit(fit_window)
    except SeasonalCandidateError:
        return None
    residuals: list[NDArray[np.float64]] = []
    pseudo_origin = calibration_cutoff
    while pseudo_origin + timedelta(days=horizon_steps) < origin:
        available = series.before(pseudo_origin)
        _require_history_before(available, pseudo_origin, series.series_key)
        leakage_counter[0] += 1
        try:
            prediction = fitted.predict(available, pseudo_origin, horizon_steps)
        except SeasonalCandidateError:
            pseudo_origin += timedelta(days=CALIBRATION_PSEUDO_ORIGIN_STRIDE_DAYS)
            continue
        actuals = np.asarray(
            [series.value_at(pseudo_origin + timedelta(days=step)) for step in range(1, horizon_steps + 1)],
            dtype=np.float64,
        )
        residuals.append(actuals - prediction.median)
        pseudo_origin += timedelta(days=CALIBRATION_PSEUDO_ORIGIN_STRIDE_DAYS)
    if not residuals:
        return None
    stacked = np.vstack(residuals)
    with np.errstate(invalid="ignore"):
        low = np.nanpercentile(stacked, 10.0, axis=0, method="linear")
        high = np.nanpercentile(stacked, 90.0, axis=0, method="linear")
    if bool(np.isnan(low).any()) or bool(np.isnan(high).any()):
        return None
    return np.asarray(low, dtype=np.float64), np.asarray(high, dtype=np.float64)


def _banded(prediction: Prediction, bands: tuple[NDArray[np.float64], NDArray[np.float64]] | None) -> Prediction:
    if prediction.native_low is not None and prediction.native_high is not None:
        return prediction
    if bands is None:
        return Prediction(median=prediction.median, native_low=None, native_high=None)
    low_offset, high_offset = bands
    return Prediction(
        median=prediction.median,
        native_low=prediction.median + low_offset,
        native_high=prediction.median + high_offset,
    )


def run_candidate(
    series: DailySeries,
    candidate: SeasonalCandidate,
    plan: RollingOriginPlan,
) -> CandidateRun:
    """Score one candidate over one series' whole origin schedule, fitting inside every fold."""
    leakage_counter = [0]
    scored: list[ScoredOrigin] = []
    skipped: list[tuple[date, str]] = []
    for origin_plan in plan.origins:
        origin = origin_plan.origin
        training = series.before(origin)
        _require_history_before(training, origin, series.series_key)
        leakage_counter[0] += 1
        if training.observed_day_count() < plan.minimum_history_days:
            skipped.append((origin, "insufficient_training_history"))
            continue
        try:
            fitted = candidate.fit(training)
            prediction = fitted.predict(training, origin, plan.horizon_steps)
        except SeasonalCandidateError as error:
            skipped.append((origin, f"candidate_refused:{error}"))
            continue
        bands = (
            None
            if candidate.has_native_interval
            else _calibration_bands(series, candidate, origin, plan.horizon_steps, leakage_counter)
        )
        banded = _banded(prediction, bands)
        steps: list[int] = []
        days: list[date] = []
        medians: list[float] = []
        lows: list[float] = []
        highs: list[float] = []
        actuals: list[float] = []
        for index, target in enumerate(origin_plan.target_days):
            actual = series.value_at(target)
            if math.isnan(actual):
                continue
            median = float(banded.median[index])
            if math.isnan(median):
                continue
            steps.append(index + 1)
            days.append(target)
            medians.append(median)
            lows.append(float(banded.native_low[index]) if banded.native_low is not None else math.nan)
            highs.append(float(banded.native_high[index]) if banded.native_high is not None else math.nan)
            actuals.append(actual)
        if not actuals:
            skipped.append((origin, "no_actual_in_target_window"))
            continue
        scored.append(
            ScoredOrigin(
                series_key=series.series_key,
                candidate_name=candidate.name,
                origin=origin,
                fold_kind=origin_plan.fold_kind,
                target_days=tuple(days),
                horizon_steps=tuple(steps),
                median=tuple(medians),
                low=tuple(lows),
                high=tuple(highs),
                actual=tuple(actuals),
            )
        )
    return CandidateRun(
        series_key=series.series_key,
        candidate_name=candidate.name,
        scored_origins=tuple(scored),
        leakage_checked_calls=leakage_counter[0],
        skipped_origins=tuple(skipped),
    )


@dataclass(frozen=True)
class MetricSummary:
    """Pooled metrics over a set of scored origins."""

    origin_count: int
    scored_day_count: int
    mae: float
    rmse: float
    bias: float
    mape: float | None
    mape_reason: str | None
    interval_coverage: float | None

    @property
    def is_empty(self) -> bool:
        """True when nothing was scored, so every metric is undefined."""
        return self.scored_day_count == 0


def season_of(day: date) -> str:
    """Meteorological season label of a target day."""
    return _SEASON_BY_MONTH[day.month]


def _flatten(
    scored: Sequence[ScoredOrigin],
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    medians: list[float] = []
    lows: list[float] = []
    highs: list[float] = []
    actuals: list[float] = []
    for origin in scored:
        medians.extend(origin.median)
        lows.extend(origin.low)
        highs.extend(origin.high)
        actuals.extend(origin.actual)
    return (
        np.asarray(medians, dtype=np.float64),
        np.asarray(lows, dtype=np.float64),
        np.asarray(highs, dtype=np.float64),
        np.asarray(actuals, dtype=np.float64),
    )


def summarize(scored: Sequence[ScoredOrigin]) -> MetricSummary:
    """Pool MAE, RMSE, bias, MAPE and interval coverage over a set of scored origins."""
    medians, lows, highs, actuals = _flatten(scored)
    if actuals.size == 0:
        return MetricSummary(
            origin_count=len(scored),
            scored_day_count=0,
            mae=math.nan,
            rmse=math.nan,
            bias=math.nan,
            mape=None,
            mape_reason="no_scored_day",
            interval_coverage=None,
        )
    errors = medians - actuals
    mape: float | None
    mape_reason: str | None
    if bool((np.abs(actuals) < MAPE_MINIMUM_ABSOLUTE_ACTUAL).any()):
        mape, mape_reason = None, "mape_undefined_near_zero"
    else:
        mape, mape_reason = float(np.abs(errors / actuals).mean() * 100.0), None
    coverage: float | None
    if bool(np.isnan(lows).any()) or bool(np.isnan(highs).any()):
        coverage = None
    else:
        coverage = float(np.mean((actuals >= lows) & (actuals <= highs)))
    return MetricSummary(
        origin_count=len(scored),
        scored_day_count=int(actuals.size),
        mae=float(np.abs(errors).mean()),
        rmse=float(np.sqrt(np.mean(errors**2))),
        bias=float(errors.mean()),
        mape=mape,
        mape_reason=mape_reason,
        interval_coverage=coverage,
    )


def skill_versus_persistence(candidate: MetricSummary, persistence: MetricSummary) -> float | None:
    """1 - MAE_candidate / MAE_persistence at identical origins; None when the baseline is degenerate."""
    if candidate.is_empty or persistence.is_empty or persistence.mae <= 0.0:
        return None
    return 1.0 - candidate.mae / persistence.mae


def _baseline_index(persistence: Sequence[ScoredOrigin]) -> dict[tuple[str, date], ScoredOrigin]:
    """Index the baseline on (series, origin).

    Keying on the origin date alone silently collapses every series that shares an origin, which is
    exactly the defect this helper exists to prevent: a pooled run over eight series would then score
    every candidate against whichever series happened to be indexed last.
    """
    return {(origin.series_key, origin.origin): origin for origin in persistence}


def pass_fraction(candidate: Sequence[ScoredOrigin], persistence: Sequence[ScoredOrigin]) -> float | None:
    """Fraction of origins whose candidate MAE is at or below the persistence MAE."""
    baseline = _baseline_index(persistence)
    wins = 0
    compared = 0
    for scored in candidate:
        other = baseline.get((scored.series_key, scored.origin))
        if other is None:
            continue
        candidate_mae = float(np.abs(np.asarray(scored.median) - np.asarray(scored.actual)).mean())
        baseline_mae = float(np.abs(np.asarray(other.median) - np.asarray(other.actual)).mean())
        compared += 1
        if candidate_mae <= baseline_mae:
            wins += 1
    return None if compared == 0 else wins / compared


@dataclass(frozen=True)
class BootstrapInterval:
    """A percentile interval from a block bootstrap over whole origins."""

    point: float
    lower: float
    upper: float
    draw_count: int
    block_count: int


def bootstrap_skill_interval(
    candidate: Sequence[ScoredOrigin],
    persistence: Sequence[ScoredOrigin],
    *,
    draw_count: int = BOOTSTRAP_DRAW_COUNT,
    seed: int = BOOTSTRAP_SEED,
) -> BootstrapInterval | None:
    """Resample whole origins with replacement; a drawn origin keeps all its horizon steps together.

    The **origin date** is the block, and every series scored at that origin is drawn with it,
    because target days inside one origin are autocorrelated and four cells 45-95 km apart are
    spatially dependent. The effective sample size is the origin count, never the scored-day count.
    """
    baseline = _baseline_index(persistence)
    grouped: dict[date, list[tuple[float, int, float, int]]] = {}
    for scored in candidate:
        other = baseline.get((scored.series_key, scored.origin))
        if other is None:
            continue
        candidate_errors = np.abs(np.asarray(scored.median) - np.asarray(scored.actual))
        baseline_errors = np.abs(np.asarray(other.median) - np.asarray(other.actual))
        grouped.setdefault(scored.origin, []).append(
            (
                float(candidate_errors.sum()),
                int(candidate_errors.size),
                float(baseline_errors.sum()),
                int(baseline_errors.size),
            )
        )
    if len(grouped) < MINIMUM_BOOTSTRAP_BLOCKS:
        return None
    ordered = [grouped[origin] for origin in sorted(grouped)]
    candidate_totals = np.asarray([sum(part[0] for part in block) for block in ordered], dtype=np.float64)
    candidate_counts = np.asarray([sum(part[1] for part in block) for block in ordered], dtype=np.float64)
    baseline_totals = np.asarray([sum(part[2] for part in block) for block in ordered], dtype=np.float64)
    baseline_counts = np.asarray([sum(part[3] for part in block) for block in ordered], dtype=np.float64)
    point_skill = 1.0 - (candidate_totals.sum() / candidate_counts.sum()) / (
        baseline_totals.sum() / baseline_counts.sum()
    )
    generator = np.random.default_rng(seed)
    draws = generator.integers(0, len(ordered), size=(draw_count, len(ordered)))
    drawn_candidate = candidate_totals[draws].sum(axis=1) / candidate_counts[draws].sum(axis=1)
    drawn_baseline = baseline_totals[draws].sum(axis=1) / baseline_counts[draws].sum(axis=1)
    skills = 1.0 - drawn_candidate / drawn_baseline
    return BootstrapInterval(
        point=float(point_skill),
        lower=float(np.percentile(skills, 2.5)),
        upper=float(np.percentile(skills, 97.5)),
        draw_count=draw_count,
        block_count=len(ordered),
    )


def slice_by_season(scored: Sequence[ScoredOrigin]) -> dict[str, tuple[ScoredOrigin, ...]]:
    """Split scored origins into per-season sub-origins by the season of each target day."""
    buckets: dict[str, list[ScoredOrigin]] = {}
    for origin in scored:
        grouped: dict[str, list[int]] = {}
        for index, day in enumerate(origin.target_days):
            grouped.setdefault(season_of(day), []).append(index)
        for season, indices in grouped.items():
            buckets.setdefault(season, []).append(
                ScoredOrigin(
                    series_key=origin.series_key,
                    candidate_name=origin.candidate_name,
                    origin=origin.origin,
                    fold_kind=origin.fold_kind,
                    target_days=tuple(origin.target_days[index] for index in indices),
                    horizon_steps=tuple(origin.horizon_steps[index] for index in indices),
                    median=tuple(origin.median[index] for index in indices),
                    low=tuple(origin.low[index] for index in indices),
                    high=tuple(origin.high[index] for index in indices),
                    actual=tuple(origin.actual[index] for index in indices),
                )
            )
    return {season: tuple(values) for season, values in sorted(buckets.items())}


@dataclass(frozen=True)
class AbstentionCheck:
    """The pre-registered abstention thresholds, evaluated against what was actually scored."""

    development_origin_count: int
    final_holdout_origin_count: int
    target_day_coverage: float
    reasons: tuple[str, ...]

    @property
    def must_abstain(self) -> bool:
        """True when at least one pre-registered abstention threshold fired."""
        return bool(self.reasons)


def evaluate_abstention(run: CandidateRun, plan: RollingOriginPlan) -> AbstentionCheck:
    """Apply the pre-registered thresholds: origin counts per fold and target-day coverage."""
    development = len(run.by_fold("development"))
    holdout = len(run.by_fold("final_holdout"))
    scheduled = sum(len(origin.target_days) for origin in plan.origins)
    scored = sum(origin.scored_day_count for origin in run.scored_origins)
    coverage = scored / scheduled if scheduled else 0.0
    reasons: list[str] = []
    if development < MINIMUM_DEVELOPMENT_ORIGINS:
        reasons.append(f"development_origins={development}<{MINIMUM_DEVELOPMENT_ORIGINS}")
    if holdout < MINIMUM_FINAL_HOLDOUT_ORIGINS:
        reasons.append(f"final_holdout_origins={holdout}<{MINIMUM_FINAL_HOLDOUT_ORIGINS}")
    if coverage < MINIMUM_TARGET_DAY_COVERAGE:
        reasons.append(f"target_day_coverage={coverage:.4f}<{MINIMUM_TARGET_DAY_COVERAGE}")
    return AbstentionCheck(
        development_origin_count=development,
        final_holdout_origin_count=holdout,
        target_day_coverage=coverage,
        reasons=tuple(reasons),
    )
