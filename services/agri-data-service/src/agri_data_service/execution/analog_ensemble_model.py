"""Evaluation-only rolling-origin Analog Ensemble (k-NN) forecaster over the 0016 covariate layer.

Mirrors `covariate_wind_model.py`'s shape: a database-free-at-the-core pure method
(`agri_data_service.method.ml.analog_ensemble`) driven by the SAME governed reader functions
that module already established (`load_covariate_matrix`, `load_target_series`,
`load_baseline_evaluation`, `persistence_anchor`, `evaluate`) -- imported and reused rather than
duplicated. Receipt persistence lives in `analog_ensemble_persist.py`.

**Time-honesty beyond the covariate layer's own gate.** The published AnEn method (Delle Monache
et al. 2013) searches a frozen historical archive where a "future" analog relative to the query
date is not leakage, because the whole archive already existed when the query was posed. This
module instead treats each rolling origin as an operational forecast issue time, so an eligible
analog's ENTIRE successor path (`analog_date + 1 .. analog_date + horizon_days`) must lie before
the origin -- not merely the analog date itself. `eligible_analog_pool` enforces
`analog_date < origin_date - horizon_days` before a pool is even assembled, and
`method/ml/analog_ensemble.find_analogs` enforces the SAME boundary structurally against whatever
history it is handed (it takes `horizon_days` and refuses any candidate `>= query_date -
horizon_days`, on top of its own `temporal_exclusion_days` window, which stays the published
method's archive-case tie-avoidance window rather than the leakage guard itself). The two guards
overlap by design: this module's pre-filter is what makes a run's eligible pool visible and
accounted for in `analog_pool_size`, and the method-layer guard is what keeps the primitive itself
from failing open for a caller that skips the pre-filter. See "Known gaps" at the bottom for what
this still does not prove.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Final

import numpy as np

from agri_data_service.execution.covariate_wind_model import (
    SCHEMA_VERSION,
    TARGET_SIGNAL_NAME,
    CovariateMatrix,
    FeatureCoverage,
    ForecastEvaluation,
    OriginNotEvaluableError,
    SkippedOrigin,
    evaluate,
    load_baseline_evaluation,
    load_covariate_matrix,
    load_target_series,
    persistence_anchor,
    rolling_origin_dates,
)
from agri_data_service.method.ml.analog_ensemble import (
    METHOD_NAME,
    AnEnHyperparameters,
    generate_anen_forecast,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

FULL_VECTOR_VARIANT: Final = "full_vector"
TARGET_LAGS_ONLY_VARIANT: Final = "target_lags_only"


@cache
def training_code_checksum() -> str:
    """SHA-256 of this module's own source: which estimator implementation produced a receipt."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def target_lag_feature_mask(feature_names: Sequence[str], *, target_signal_name: str) -> tuple[bool, ...]:
    """Which pinned covariate columns are the target's own lag/rolling-mean shapes.

    The `agri_covariates_v1` layout names every shape `<signal_name>_lag_1`,
    `..._roll_mean_28`, etc., so a simple prefix match is exact and needs no extra schema lookup.
    """
    prefix = f"{target_signal_name}_"
    return tuple(name.startswith(prefix) for name in feature_names)


def _standardize(pool: np.ndarray, query: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Zero-mean/unit-variance moments from the analog POOL, applied to both pool and query.

    Without this, Euclidean k-NN distance is dominated by whichever feature happens to carry the
    largest numeric range (precipitation in mm beside relative humidity in percent), which has
    nothing to do with which historical day was meteorologically most similar. Mirrors
    `covariate_wind_model.fit_ridge`'s own standardization step.
    """
    mean = pool.mean(axis=0)
    scale = pool.std(axis=0)
    scale[scale == 0.0] = 1.0
    return (pool - mean) / scale, (query - mean) / scale


def eligible_analog_pool(
    matrix: CovariateMatrix, *, origin_date: date, horizon_days: int
) -> tuple[np.ndarray, tuple[date, ...]]:
    """Feature-complete days whose ENTIRE `horizon_days`-long successor path lies before `origin_date`.

    See the module docstring for why this is stricter than the pure method's own symmetric
    `temporal_exclusion_days` window.
    """
    boundary = origin_date - timedelta(days=horizon_days)
    positions = [
        position for position, day in enumerate(matrix.dates) if day < boundary and bool(matrix.complete[position])
    ]
    if not positions:
        return np.empty((0, matrix.values.shape[1])), ()
    return matrix.values[positions], tuple(matrix.dates[position] for position in positions)


@dataclass(frozen=True)
class AnEnOriginRun:
    """One rolling origin: the k-NN forecast issued from it, and what it scored on held-out days."""

    origin_date: date
    target_dates: tuple[date, ...]
    predictions: tuple[float, ...]
    lower_bounds: tuple[float, ...]
    upper_bounds: tuple[float, ...]
    actuals: tuple[float, ...]
    naive_predictions: tuple[float, ...]
    analog_pool_size: int
    analog_counts: tuple[int, ...]
    scores: ForecastEvaluation

    @property
    def naive_root_mean_squared_error(self) -> float | None:
        """Persistence-baseline RMSE, or None when no anchor was available before the origin."""
        if not self.naive_predictions:
            return None
        errors = np.asarray(self.naive_predictions) - np.asarray(self.actuals)
        return float(np.sqrt(np.mean(errors**2)))

    @property
    def skill_score(self) -> float | None:
        """One minus the ratio of AnEn to persistence RMSE; None when the baseline was perfect."""
        naive = self.naive_root_mean_squared_error
        if naive is None or naive == 0.0:
            return None
        return 1.0 - (self.scores.root_mean_squared_error / naive)

    def to_summary(self) -> dict[str, object]:
        """Render one origin's row for a receipt's JSONB, sample size beside every metric."""
        return {
            "origin_date": self.origin_date.isoformat(),
            "analog_pool_size": self.analog_pool_size,
            "mean_analog_count": float(np.mean(self.analog_counts)) if self.analog_counts else None,
            "naive_root_mean_squared_error": self.naive_root_mean_squared_error,
            "skill_score": self.skill_score,
            "mean_interval_width": (
                float(np.mean(np.asarray(self.upper_bounds) - np.asarray(self.lower_bounds)))
                if self.upper_bounds
                else None
            ),
            **self.scores.to_summary(),
        }


def run_anen_origin(
    matrix: CovariateMatrix,
    targets: Mapping[date, float],
    *,
    origin_date: date,
    feature_mask: Sequence[bool],
    hyperparams: AnEnHyperparameters,
) -> AnEnOriginRun:
    """Standardize the eligible analog pool, find k analogs, and score the held-out targets."""
    if origin_date not in matrix.dates:
        raise OriginNotEvaluableError(f"origin {origin_date} has no covariate row in the loaded window")
    origin_position = matrix.dates.index(origin_date)
    if not matrix.complete[origin_position]:
        raise OriginNotEvaluableError(
            f"origin {origin_date} has an incomplete covariate vector; refusing to forecast from it"
        )
    selected_columns = [index for index, keep in enumerate(feature_mask) if keep]
    if not selected_columns:
        raise ValueError("feature_mask selects no covariate columns")

    pool_values, pool_dates = eligible_analog_pool(
        matrix, origin_date=origin_date, horizon_days=hyperparams.horizon_days
    )
    if len(pool_dates) < hyperparams.k_neighbors:
        raise OriginNotEvaluableError(
            f"origin {origin_date} has only {len(pool_dates)} time-honest analog candidate(s), "
            f"fewer than k_neighbors={hyperparams.k_neighbors}"
        )

    standardized_pool, standardized_query = _standardize(
        pool_values[:, selected_columns], matrix.values[origin_position, selected_columns]
    )
    result = generate_anen_forecast(
        query_date=origin_date,
        query_vector=standardized_query,
        history_matrix=standardized_pool,
        history_dates=pool_dates,
        target_series=targets,
        recorded_residuals=None,
        hyperparams=hyperparams,
    )
    anchor = persistence_anchor(targets, origin_date)

    target_dates: list[date] = []
    predictions: list[float] = []
    lowers: list[float] = []
    uppers: list[float] = []
    actuals: list[float] = []
    naive: list[float] = []
    analog_counts: list[int] = []
    for step in result.steps:
        actual = targets.get(step.valid_day)
        if actual is None:
            continue
        target_dates.append(step.valid_day)
        predictions.append(step.median_value)
        lowers.append(step.low_value)
        uppers.append(step.high_value)
        actuals.append(actual)
        analog_counts.append(step.analog_count)
        if anchor is not None:
            naive.append(anchor)

    if not predictions:
        raise OriginNotEvaluableError(f"origin {origin_date} scored no horizon; no actual was available")

    return AnEnOriginRun(
        origin_date=origin_date,
        target_dates=tuple(target_dates),
        predictions=tuple(predictions),
        lower_bounds=tuple(lowers),
        upper_bounds=tuple(uppers),
        actuals=tuple(actuals),
        naive_predictions=tuple(naive),
        analog_pool_size=len(pool_dates),
        analog_counts=tuple(analog_counts),
        scores=evaluate(
            np.asarray(predictions), np.asarray(actuals), lower=np.asarray(lowers), upper=np.asarray(uppers)
        ),
    )


@dataclass(frozen=True)
class AnEnRollingBacktest:
    """Every scored origin for one feature-mask variant, plus the pooled aggregate."""

    variant_label: str
    origins: tuple[AnEnOriginRun, ...]
    skipped: tuple[SkippedOrigin, ...]
    aggregate: ForecastEvaluation
    horizon_days: int

    @property
    def newest_origin(self) -> date:
        return max(origin.origin_date for origin in self.origins)

    @property
    def earliest_origin(self) -> date:
        return min(origin.origin_date for origin in self.origins)

    @property
    def pooled_naive_root_mean_squared_error(self) -> float | None:
        """Persistence-baseline RMSE pooled across every origin that had an anchor.

        `run_anen_origin` appends one naive value per scored horizon step only when that
        origin's own persistence anchor was available, so an origin's `naive_predictions` is
        either empty or exactly as long as its `actuals` -- aligned pairwise, never mismatched.
        """
        naive: list[float] = []
        matched_actuals: list[float] = []
        for origin in self.origins:
            if not origin.naive_predictions:
                continue
            naive.extend(origin.naive_predictions)
            matched_actuals.extend(origin.actuals)
        if not naive:
            return None
        errors = np.asarray(naive) - np.asarray(matched_actuals)
        return float(np.sqrt(np.mean(errors**2)))

    def to_summary(self) -> dict[str, object]:
        """Render the whole variant sweep for a receipt's JSONB, sample sizes beside every metric."""
        return {
            "variant_label": self.variant_label,
            "origin_count": len(self.origins),
            "skipped_origin_count": len(self.skipped),
            "horizon_days": self.horizon_days,
            "earliest_origin": self.earliest_origin.isoformat(),
            "newest_origin": self.newest_origin.isoformat(),
            "pooled_naive_root_mean_squared_error": self.pooled_naive_root_mean_squared_error,
            "aggregate": self.aggregate.to_summary(),
            "origins": [origin.to_summary() for origin in self.origins],
            "skipped_origins": [skip.to_summary() for skip in self.skipped],
        }


def run_anen_rolling_backtest(  # noqa: PLR0913 - keyword-only sweep boundaries are the contract
    matrix: CovariateMatrix,
    targets: Mapping[date, float],
    *,
    origin_dates: Sequence[date],
    feature_mask: Sequence[bool],
    variant_label: str,
    hyperparams: AnEnHyperparameters,
) -> AnEnRollingBacktest:
    """Score every requested origin under one feature-mask variant; an unscoreable origin is skipped, not fatal."""
    scored: list[AnEnOriginRun] = []
    skipped: list[SkippedOrigin] = []
    for origin_date in sorted(origin_dates):
        try:
            scored.append(
                run_anen_origin(
                    matrix, targets, origin_date=origin_date, feature_mask=feature_mask, hyperparams=hyperparams
                )
            )
        except OriginNotEvaluableError as error:
            skipped.append(SkippedOrigin(origin_date, str(error)))
    if not scored:
        reasons = "; ".join(skip.reason for skip in skipped) or "no origins were requested"
        raise OriginNotEvaluableError(f"no rolling origin could be scored ({variant_label}): {reasons}")

    predictions = np.asarray([value for origin in scored for value in origin.predictions])
    actuals = np.asarray([value for origin in scored for value in origin.actuals])
    lowers = np.asarray([value for origin in scored for value in origin.lower_bounds])
    uppers = np.asarray([value for origin in scored for value in origin.upper_bounds])
    return AnEnRollingBacktest(
        variant_label=variant_label,
        origins=tuple(scored),
        skipped=tuple(skipped),
        aggregate=evaluate(predictions, actuals, lower=lowers, upper=uppers),
        horizon_days=hyperparams.horizon_days,
    )


@dataclass(frozen=True)
class AnEnEvaluationReport:
    """One invocation's evidence: the ablation pair, the coverage accounting, and the baseline read."""

    cell_id: str
    series_id: str | None
    schema_version: str
    target_signal_name: str
    hyperparams: AnEnHyperparameters
    coverage: FeatureCoverage
    manifest: Mapping[str, object]
    declared_gaps: object
    feature_names: tuple[str, ...]
    full_vector_backtest: AnEnRollingBacktest
    target_lags_only_backtest: AnEnRollingBacktest
    baseline_iterations: Sequence[Mapping[str, object]]

    def to_summary(self) -> dict[str, object]:
        """The evaluation-only JSON payload: both ablation variants side by side, plus the baseline read."""
        return {
            "method_name": METHOD_NAME,
            "cell_id": self.cell_id,
            "series_id": self.series_id,
            "schema_version": self.schema_version,
            "target_signal_name": self.target_signal_name,
            "hyperparameters": asdict(self.hyperparams),
            "covariate_manifest": dict(self.manifest),
            "declared_gaps": self.declared_gaps,
            "feature_coverage": self.coverage.to_summary(),
            "full_vector": self.full_vector_backtest.to_summary(),
            "target_lags_only": self.target_lags_only_backtest.to_summary(),
            "daily_increment_bootstrap_v1_baseline": [dict(row) for row in self.baseline_iterations],
        }


async def evaluate_analog_ensemble(  # noqa: PLR0913 - keyword-only window/gate parameters are the contract
    session: AsyncSession,
    *,
    cell_id: str,
    history_start: date,
    history_end: date,
    origin_date: date,
    as_of_time: datetime,
    series_id: str | None = None,
    origin_count: int = 1,
    origin_stride_days: int | None = None,
    hyperparams: AnEnHyperparameters | None = None,
    schema_version: str = SCHEMA_VERSION,
    target_signal_name: str = TARGET_SIGNAL_NAME,
) -> AnEnEvaluationReport:
    """Read the governed covariate/target planes, run both ablation variants, and report without writing."""
    params = hyperparams or AnEnHyperparameters()
    matrix = await load_covariate_matrix(
        session,
        cell_id=cell_id,
        window_start=history_start,
        window_end=history_end,
        as_of_time=as_of_time,
        schema_version=schema_version,
    )
    targets = await load_target_series(
        session,
        cell_id=cell_id,
        as_of_time=as_of_time,
        signal_name=target_signal_name,
    )
    baseline = (
        await load_baseline_evaluation(session, series_id=series_id, as_of_time=as_of_time)
        if series_id is not None
        else []
    )

    stride = origin_stride_days if origin_stride_days is not None else params.horizon_days
    origins = rolling_origin_dates(origin_date, origin_count=origin_count, origin_stride_days=stride)

    full_mask = tuple(True for _ in matrix.feature_names)
    lag_mask = target_lag_feature_mask(matrix.feature_names, target_signal_name=target_signal_name)
    if not any(lag_mask):
        raise ValueError(
            f"no covariate feature name begins with {target_signal_name!r}_; "
            "the target-lags-only ablation has nothing to select"
        )

    full_backtest = run_anen_rolling_backtest(
        matrix,
        targets,
        origin_dates=origins,
        feature_mask=full_mask,
        variant_label=FULL_VECTOR_VARIANT,
        hyperparams=params,
    )
    lag_backtest = run_anen_rolling_backtest(
        matrix,
        targets,
        origin_dates=origins,
        feature_mask=lag_mask,
        variant_label=TARGET_LAGS_ONLY_VARIANT,
        hyperparams=params,
    )

    return AnEnEvaluationReport(
        cell_id=cell_id,
        series_id=series_id,
        schema_version=schema_version,
        target_signal_name=target_signal_name,
        hyperparams=params,
        coverage=matrix.coverage(targets),
        manifest=matrix.reported_manifest(),
        declared_gaps=matrix.manifest.get("declared_gaps"),
        feature_names=matrix.feature_names,
        full_vector_backtest=full_backtest,
        target_lags_only_backtest=lag_backtest,
        baseline_iterations=baseline,
    )


def model_document(
    backtest: AnEnRollingBacktest,
    *,
    cell_id: str,
    feature_checksum: str,
    feature_names: Sequence[str],
    feature_mask: Sequence[bool],
) -> dict[str, object]:
    """The persisted model document: hyperparameters, selected features, and the governed data reference.

    AnEn is non-parametric -- there is no coefficient block to store -- so without `cell_id` and
    `feature_checksum` this document would be IDENTICAL across two structurally similar runs over
    DIFFERENT governed inputs (same hyperparameters, same origin date, same analog-pool size and
    scored-day count can coincide across two unrelated cells). `model_version` is derived from
    this document's digest, and `agri.forecast_model` is unique on `(model_key, model_version)`,
    so an undiscriminating document would make the second run's insert silently resolve to the
    first run's model row -- which then fails `agri.validate_forecast_training_run`'s output
    lineage check with "local training validation output lineage mismatch" (measured: two
    disposable-database runs over different synthetic cells collided on an identical document
    before this fix). Binding the cell and the covariate manifest checksum closes that gap the
    way the wind ridge's own fitted coefficients close it implicitly for that lane.
    """
    return {
        "document_version": "agri_analog_ensemble_v1",
        "algorithm": METHOD_NAME,
        "cell_id": cell_id,
        "feature_checksum": feature_checksum,
        "variant_label": backtest.variant_label,
        "selected_feature_names": [name for name, keep in zip(feature_names, feature_mask, strict=True) if keep],
        "horizon_days": backtest.horizon_days,
        "origins": [
            {
                "origin_date": origin.origin_date.isoformat(),
                "analog_pool_size": origin.analog_pool_size,
                "target_date_count": len(origin.target_dates),
            }
            for origin in backtest.origins
        ],
    }


# Known gaps in this lane, named rather than silently accepted:
#
# - Analog-space bias correction is wired but not exercised. `generate_anen_forecast` accepts a
#   `recorded_residuals` mapping (analog_date -> a prior forecast error for that day), and this
#   module always passes `None`. This warehouse holds no parallel NWP-forecast series for
#   wind_speed to derive such a per-analog bias term from -- see `execution/AGENTS.md`,
#   "Analog Ensemble" -- so `mean_bias_correction` is always 0.0 in every step this backtest
#   produces. Partial stays partial: inventing a bias source would be worse than leaving it unwired.
# - `as_of_time` is one global instant, not a per-observation-date knowledge cutoff, for exactly
#   the reason `covariate_wind_model.py` documents under "as_of_mode: global".
