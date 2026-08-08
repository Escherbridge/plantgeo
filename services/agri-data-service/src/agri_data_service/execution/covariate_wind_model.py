"""Evaluation-only rolling-origin ridge forecaster over the 0016 covariate layer.

Rationale, scope limits, the leakage argument, the sample-size caveat and the
`as_of_mode: global` deviation live in `AGENTS.md` (this directory) under
`covariate_wind_model.py`. Receipt persistence lives in `covariate_wind_persist.py`.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Final

import numpy as np
from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

SCHEMA_VERSION: Final = "agri_covariates_v1"
TARGET_SIGNAL_NAME: Final = "wind_speed"
DEFAULT_RIDGE_ALPHA: Final = 10.0
DEFAULT_HORIZON_COUNT: Final = 30
DEFAULT_CALIBRATION_DAYS: Final = 180
DEFAULT_ORIGIN_COUNT: Final = 1
LOWER_QUANTILE: Final = 0.10
UPPER_QUANTILE: Final = 0.90
NON_NEGATIVE_FLOOR: Final = 0.0

# Horizon 0 is a NOWCAST: its target date equals the issue date, and every meteorological
# feature is lagged by at least one day, so it is a same-day estimate from yesterday's weather
# rather than a zero-lead prediction. The SQL baseline (`forecast_linear_regression`) numbers
# its steps from 1, so "30 horizons" here means days 0..29 and is one day shorter in lead time
# than the same figure over there. Reported as `horizon_origin_offset` so the convention
# travels with the numbers.
HORIZON_ORIGIN_OFFSET: Final = 0

# How far back the persistence baseline may reach for the newest target observed strictly
# before an origin. Bounded rather than unbounded: anchoring "yesterday's value" to a reading
# three months old would flatter the model against a baseline nobody would actually use.
PERSISTENCE_LOOKBACK_DAYS: Final = 14

# MAPE divides by the actual, so a near-zero wind speed makes it explode. It is reported only
# when EVERY actual clears this floor; a MAPE computed over the subset that happens to be large
# describes a different sample than the MAE beside it.
MAPE_MINIMUM_DENOMINATOR: Final = 0.1

_FEATURE_SCHEMA = text(
    "SELECT feature_index, feature_name FROM agri.covariate_feature_schema(CAST(:schema_version AS varchar))"
)
_DAILY_FEATURES = text(load_query_sql("execution/covariate_daily_features.sql"))
_VECTOR_MANIFEST = text(load_query_sql("execution/covariate_vector_manifest.sql"))
_TARGET_SERIES = text(load_query_sql("execution/target_signal_series.sql"))
_ITERATION_EVALUATION = text(
    "SELECT * FROM agri.forecast_iteration_evaluation(CAST(:series_id AS uuid), CAST(:as_of_time AS timestamptz))"
)

# The manifest columns carried into a receipt. The function returns seventeen; the array-valued
# ones (feature_names, source_release_ids) are omitted here because the manifest checksum
# already binds them and repeating forty names in every JSON payload buys nothing.
_REPORTED_MANIFEST_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "feature_count",
        "day_count",
        "emitted_row_count",
        "complete_row_count",
        "partial_row_count",
        "imputed_row_count",
        "complete_day_count",
        "max_data_available_at",
        "manifest_checksum",
    }
)


class OriginNotEvaluableError(ValueError):
    """Raised when one rolling origin cannot be scored; the sweep records it and moves on."""


@cache
def training_code_checksum() -> str:
    """SHA-256 of this module's own source: which estimator implementation produced a receipt."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def canonical_json(payload: Mapping[str, object]) -> str:
    """Render a checksum input deterministically: sorted keys, no padding, no NaN escape hatch."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_digest(payload: Mapping[str, object]) -> str:
    """SHA-256 over `canonical_json`, which is how every digest in this lane is derived."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FeatureCoverage:
    """Why the training set is the size it is: candidate days in, days excluded, and by which feature.

    Exists because the covariate completeness mask is whole-row -- one short feature discards the
    other thirty-nine present ones -- and that shrinkage used to be silent. See `AGENTS.md`.
    """

    candidate_day_count: int
    feature_complete_day_count: int
    excluded_day_count: int
    target_missing_day_count: int
    usable_day_count: int
    blocking_features: tuple[tuple[str, int], ...]
    manifest_day_count: int | None

    @property
    def feature_complete_fraction(self) -> float:
        """Share of candidate days whose whole covariate vector was present; 0.0 when none were."""
        if self.candidate_day_count == 0:
            return 0.0
        return self.feature_complete_day_count / self.candidate_day_count

    def to_summary(self) -> dict[str, object]:
        """Render the accounting for a receipt's JSONB, keeping the reason beside the count."""
        return {
            "candidate_day_count": self.candidate_day_count,
            "feature_complete_day_count": self.feature_complete_day_count,
            "excluded_day_count": self.excluded_day_count,
            "target_missing_day_count": self.target_missing_day_count,
            "usable_day_count": self.usable_day_count,
            "feature_complete_fraction": self.feature_complete_fraction,
            "manifest_day_count": self.manifest_day_count,
            "blocking_features": [
                {"feature_name": name, "excluded_day_count": count} for name, count in self.blocking_features
            ],
        }


@dataclass(frozen=True)
class CovariateMatrix:
    """Dense (date x pinned-feature) design matrix plus its per-row completeness mask."""

    dates: tuple[date, ...]
    feature_names: tuple[str, ...]
    values: np.ndarray
    complete: np.ndarray
    manifest: Mapping[str, object]
    blocking_features: Mapping[str, int] = field(default_factory=dict)

    def coverage(self, targets: Mapping[date, float]) -> FeatureCoverage:
        """Account for every candidate day: feature-complete, feature-excluded, or target-missing."""
        complete_positions = [position for position in range(len(self.dates)) if bool(self.complete[position])]
        target_missing = sum(1 for position in complete_positions if self.dates[position] not in targets)
        manifest_day_count = self.manifest.get("day_count")
        return FeatureCoverage(
            candidate_day_count=len(self.dates),
            feature_complete_day_count=len(complete_positions),
            excluded_day_count=len(self.dates) - len(complete_positions),
            target_missing_day_count=target_missing,
            usable_day_count=len(complete_positions) - target_missing,
            blocking_features=tuple(sorted(self.blocking_features.items(), key=lambda entry: (-entry[1], entry[0]))),
            manifest_day_count=manifest_day_count if isinstance(manifest_day_count, int) else None,
        )

    def reported_manifest(self) -> dict[str, object]:
        """The manifest fields a receipt carries, rendered JSON-safe."""
        return {
            key: (value.isoformat() if isinstance(value, datetime) else value)
            for key, value in self.manifest.items()
            if key in _REPORTED_MANIFEST_COLUMNS
        }


@dataclass(frozen=True)
class RidgeModel:
    """Standardize-then-ridge coefficients with the fit-window moments they were built on."""

    feature_mean: np.ndarray
    feature_scale: np.ndarray
    coefficients: np.ndarray
    target_mean: float


@dataclass(frozen=True)
class ForecastEvaluation:
    """Point and interval scores over one evaluated set of (prediction, actual) pairs."""

    evaluated_count: int
    mean_absolute_error: float
    root_mean_squared_error: float
    interval_coverage: float | None
    bias: float = 0.0
    mean_absolute_percentage_error: float | None = None

    def to_summary(self) -> dict[str, object]:
        """Render the scores for a receipt's JSONB."""
        return {
            "evaluated_count": self.evaluated_count,
            "mean_absolute_error": self.mean_absolute_error,
            "root_mean_squared_error": self.root_mean_squared_error,
            "interval_coverage": self.interval_coverage,
            "bias": self.bias,
            "mean_absolute_percentage_error": self.mean_absolute_percentage_error,
        }


@dataclass(frozen=True)
class HorizonRun:
    """One fitted horizon: its split boundaries, its row counts, and its calibration band."""

    horizon: int
    fit_row_count: int
    calibration_row_count: int
    lower_offset: float
    upper_offset: float
    fit_target_dates: tuple[date, ...] = ()
    calibration_target_dates: tuple[date, ...] = ()


def _utc_midnight(day: date) -> datetime:
    """Read a calendar day as the UTC instant it starts at, which is the grain every window uses."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


async def load_covariate_matrix(  # noqa: PLR0913 - keyword-only window/gate parameters are the contract
    session: AsyncSession,
    *,
    cell_id: str,
    window_start: date,
    window_end: date,
    as_of_time: datetime,
    schema_version: str = SCHEMA_VERSION,
) -> CovariateMatrix:
    """Pivot `agri.covariate_daily_features` into a dense matrix in pinned feature order."""
    schema_rows = (await session.execute(_FEATURE_SCHEMA, {"schema_version": schema_version})).all()
    feature_names = tuple(str(row.feature_name) for row in schema_rows)
    index_of = {int(row.feature_index): position for position, row in enumerate(schema_rows)}

    window = {
        "cell_id": cell_id,
        "window_start": _utc_midnight(window_start),
        "window_end": _utc_midnight(window_end),
        "as_of_time": as_of_time,
        "schema_version": schema_version,
    }
    feature_rows = (await session.execute(_DAILY_FEATURES, window)).all()
    manifest_row = (await session.execute(_VECTOR_MANIFEST, window)).mappings().first()
    manifest: Mapping[str, object] = dict(manifest_row) if manifest_row is not None else {}

    dates = tuple(sorted({row.observed_date for row in feature_rows}))
    row_of = {day: position for position, day in enumerate(dates)}
    values = np.full((len(dates), len(feature_names)), np.nan, dtype=float)
    complete = np.ones(len(dates), dtype=bool)
    blocking: Counter[str] = Counter()
    for row in feature_rows:
        position = row_of[row.observed_date]
        column = index_of[int(row.feature_index)]
        if row.feature_value is None or row.input_count != row.expected_input_count:
            # Whole-row mask: the fit needs the full pinned vector, so one short feature discards
            # the day. The blocking feature is counted rather than dropped silently -- that count
            # is the difference between "N was small" and "N was small BECAUSE of this feature".
            complete[position] = False
            blocking[str(row.feature_name)] += 1
            continue
        values[position, column] = float(row.feature_value)
    return CovariateMatrix(dates, feature_names, values, complete, manifest, dict(blocking))


async def load_target_series(
    session: AsyncSession,
    *,
    cell_id: str,
    as_of_time: datetime,
    signal_name: str = TARGET_SIGNAL_NAME,
) -> dict[date, float]:
    """Return the availability-gated observed target value per UTC day."""
    rows = (
        await session.execute(
            _TARGET_SERIES,
            {"cell_id": cell_id, "signal_name": signal_name, "as_of_time": as_of_time},
        )
    ).all()
    return {row.observed_date: float(row.normalized_value) for row in rows}


async def load_baseline_evaluation(
    session: AsyncSession,
    *,
    series_id: str,
    as_of_time: datetime,
) -> list[dict[str, object]]:
    """Read the existing statistical bootstrap baseline through its own leakage-gated function."""
    rows = (await session.execute(_ITERATION_EVALUATION, {"series_id": series_id, "as_of_time": as_of_time})).mappings()
    return [
        {
            "iteration_key": row["iteration_key"],
            "cutoff_time": row["cutoff_time"].isoformat(),
            "horizon_days": row["horizon_days"],
            "evaluated_count": row["evaluated_count"],
            "actual_count": row["actual_count"],
            "mean_absolute_error": row["mean_absolute_error"],
            "root_mean_squared_error": row["root_mean_squared_error"],
            "interval_coverage": row["interval_coverage"],
            "evaluation_checksum": row["evaluation_checksum"],
        }
        for row in rows
    ]


def feature_code_checksum(feature_names: Sequence[str], *, schema_version: str) -> str:
    """Digest the pinned feature schema itself, so a receipt names the recipe that built its columns."""
    return canonical_digest(
        {
            "digest_version": "agri_covariate_feature_schema_v1",
            "schema_version": schema_version,
            "feature_names": list(feature_names),
        }
    )


def build_horizon_dataset(
    matrix: CovariateMatrix,
    targets: Mapping[date, float],
    *,
    horizon: int,
    target_first: date,
    target_last: date,
) -> tuple[np.ndarray, np.ndarray, tuple[date, ...]]:
    """Rows for `target(issue_date + horizon)` whose target date lies in the given window."""
    feature_rows: list[np.ndarray] = []
    target_values: list[float] = []
    issue_dates: list[date] = []
    for position, issue_date in enumerate(matrix.dates):
        if not matrix.complete[position]:
            continue
        target_date = issue_date + timedelta(days=horizon)
        if target_date < target_first or target_date > target_last:
            continue
        value = targets.get(target_date)
        if value is None:
            continue
        feature_rows.append(matrix.values[position])
        target_values.append(value)
        issue_dates.append(issue_date)
    if not feature_rows:
        return np.empty((0, matrix.values.shape[1])), np.empty(0), ()
    return np.vstack(feature_rows), np.asarray(target_values, dtype=float), tuple(issue_dates)


def fit_ridge(features: np.ndarray, targets: np.ndarray, *, alpha: float = DEFAULT_RIDGE_ALPHA) -> RidgeModel:
    """Closed-form ridge on standardized features with a centered target (no intercept penalty)."""
    feature_mean = features.mean(axis=0)
    feature_scale = features.std(axis=0)
    feature_scale[feature_scale == 0.0] = 1.0
    standardized = (features - feature_mean) / feature_scale
    target_mean = float(targets.mean())
    centered = targets - target_mean
    gram = standardized.T @ standardized + alpha * np.eye(standardized.shape[1])
    coefficients = np.linalg.solve(gram, standardized.T @ centered)
    return RidgeModel(feature_mean, feature_scale, coefficients, target_mean)


def predict(model: RidgeModel, features: np.ndarray) -> np.ndarray:
    """Point predictions, floored at zero because wind speed cannot be negative."""
    standardized = (features - model.feature_mean) / model.feature_scale
    predictions: np.ndarray = np.maximum(standardized @ model.coefficients + model.target_mean, NON_NEGATIVE_FLOOR)
    return predictions


def evaluate(
    predictions: np.ndarray,
    actuals: np.ndarray,
    *,
    lower: np.ndarray | None = None,
    upper: np.ndarray | None = None,
) -> ForecastEvaluation:
    """MAE / RMSE / bias, plus p10-p90 interval coverage when a band was supplied."""
    errors = predictions - actuals
    coverage: float | None = None
    if lower is not None and upper is not None:
        coverage = float(np.mean((actuals >= lower) & (actuals <= upper)))
    percentage_error: float | None = None
    if actuals.size and bool(np.all(np.abs(actuals) >= MAPE_MINIMUM_DENOMINATOR)):
        percentage_error = float(np.mean(np.abs(errors / actuals)))
    return ForecastEvaluation(
        evaluated_count=int(actuals.size),
        mean_absolute_error=float(np.mean(np.abs(errors))),
        root_mean_squared_error=float(np.sqrt(np.mean(errors**2))),
        interval_coverage=coverage,
        bias=float(np.mean(errors)) if errors.size else 0.0,
        mean_absolute_percentage_error=percentage_error,
    )


def persistence_anchor(targets: Mapping[date, float], origin_date: date) -> float | None:
    """The newest target observed STRICTLY before the origin: the honest "no-change" baseline.

    Strictly before, because horizon 0 forecasts the origin day itself and every covariate is
    lagged at least one day -- the origin's own value is not knowable at the origin.
    """
    for offset in range(1, PERSISTENCE_LOOKBACK_DAYS + 1):
        value = targets.get(origin_date - timedelta(days=offset))
        if value is not None:
            return value
    return None


@dataclass(frozen=True)
class OriginBacktest:
    """One rolling origin: the model refitted at its cutoff, and what it scored on held-out days."""

    origin_date: date
    fit_target_last: date
    calibration_target_last: date
    horizon_runs: tuple[HorizonRun, ...]
    target_dates: tuple[date, ...]
    predictions: tuple[float, ...]
    lower_bounds: tuple[float, ...]
    upper_bounds: tuple[float, ...]
    actuals: tuple[float, ...]
    naive_predictions: tuple[float, ...]
    scores: ForecastEvaluation
    models: tuple[RidgeModel, ...] = ()

    @property
    def training_point_count(self) -> int:
        """The smallest fit-window row count across this origin's horizons; the binding figure."""
        return min((run.fit_row_count for run in self.horizon_runs), default=0)

    @property
    def naive_root_mean_squared_error(self) -> float | None:
        """Persistence-baseline RMSE, or None when no anchor was available before the origin."""
        if not self.naive_predictions:
            return None
        errors = np.asarray(self.naive_predictions) - np.asarray(self.actuals)
        return float(np.sqrt(np.mean(errors**2)))

    @property
    def skill_score(self) -> float | None:
        """One minus the ratio of model to persistence RMSE; None when the baseline was perfect."""
        naive = self.naive_root_mean_squared_error
        if naive is None or naive == 0.0:
            return None
        return 1.0 - (self.scores.root_mean_squared_error / naive)

    def to_summary(self) -> dict[str, object]:
        """Render one origin's row for a receipt's JSONB, sample size beside every metric."""
        return {
            "origin_date": self.origin_date.isoformat(),
            "fit_target_last": self.fit_target_last.isoformat(),
            "calibration_target_last": self.calibration_target_last.isoformat(),
            "horizon_count": len(self.horizon_runs),
            "horizon_origin_offset": HORIZON_ORIGIN_OFFSET,
            "training_point_count": self.training_point_count,
            "calibration_row_count_min": min((run.calibration_row_count for run in self.horizon_runs), default=0),
            "naive_root_mean_squared_error": self.naive_root_mean_squared_error,
            "skill_score": self.skill_score,
            "mean_interval_width": (
                float(np.mean(np.asarray(self.upper_bounds) - np.asarray(self.lower_bounds)))
                if self.upper_bounds
                else None
            ),
            **self.scores.to_summary(),
        }


def origin_split(origin_date: date, *, calibration_days: int) -> tuple[date, date]:
    """The two boundaries every origin's fit obeys: (fit_target_last, calibration_target_last).

    Both lie strictly before the origin. The calibration window ends the day before the origin
    so no day the band was calibrated on is ever a day the band is scored on, and the fit window
    ends `calibration_days` earlier still so no fit target overlaps the calibration window.
    """
    if calibration_days < 1:
        raise ValueError("calibration_days must be at least one day")
    calibration_target_last = origin_date - timedelta(days=1)
    return calibration_target_last - timedelta(days=calibration_days), calibration_target_last


def rolling_origin_dates(newest_origin: date, *, origin_count: int, origin_stride_days: int) -> tuple[date, ...]:
    """Ascending origins ending at `newest_origin`, one every `origin_stride_days` back from it."""
    if origin_count < 1:
        raise ValueError("origin_count must be at least one origin")
    if origin_stride_days < 1:
        raise ValueError("origin_stride_days must be at least one day")
    return tuple(sorted(newest_origin - timedelta(days=origin_stride_days * step) for step in range(origin_count)))


def run_direct_multi_horizon(  # noqa: PLR0913 - keyword-only split boundaries are the contract
    matrix: CovariateMatrix,
    targets: Mapping[date, float],
    *,
    fit_target_last: date,
    calibration_target_last: date,
    origin_date: date,
    horizon_count: int = DEFAULT_HORIZON_COUNT,
    alpha: float = DEFAULT_RIDGE_ALPHA,
) -> OriginBacktest:
    """Fit one ridge per horizon, calibrate its band, then score the single held-out origin."""
    history_first = matrix.dates[0] if matrix.dates else origin_date
    if origin_date not in matrix.dates:
        raise OriginNotEvaluableError(f"origin {origin_date} has no covariate row in the loaded window")
    origin_position = matrix.dates.index(origin_date)
    if not matrix.complete[origin_position]:
        raise OriginNotEvaluableError(
            f"origin {origin_date} has an incomplete covariate vector; refusing to forecast from it"
        )
    origin_features = matrix.values[origin_position].reshape(1, -1)
    anchor = persistence_anchor(targets, origin_date)

    runs: list[HorizonRun] = []
    models: list[RidgeModel] = []
    predictions: list[float] = []
    lowers: list[float] = []
    uppers: list[float] = []
    actuals: list[float] = []
    naive: list[float] = []
    target_dates: list[date] = []

    for horizon in range(horizon_count):
        fit_features, fit_targets, fit_issue_dates = build_horizon_dataset(
            matrix, targets, horizon=horizon, target_first=history_first, target_last=fit_target_last
        )
        calibration_features, calibration_targets, calibration_issue_dates = build_horizon_dataset(
            matrix,
            targets,
            horizon=horizon,
            target_first=fit_target_last + timedelta(days=1),
            target_last=calibration_target_last,
        )
        if fit_features.shape[0] == 0 or calibration_features.shape[0] == 0:
            raise OriginNotEvaluableError(
                f"origin {origin_date} horizon {horizon} has no fit or calibration rows; widen the window"
            )

        model = fit_ridge(fit_features, fit_targets, alpha=alpha)
        residuals = calibration_targets - predict(model, calibration_features)
        lower_offset = float(np.quantile(residuals, LOWER_QUANTILE))
        upper_offset = float(np.quantile(residuals, UPPER_QUANTILE))

        point = float(predict(model, origin_features)[0])
        target_date = origin_date + timedelta(days=horizon)
        actual = targets.get(target_date)
        if actual is None:
            continue
        runs.append(
            HorizonRun(
                horizon,
                int(fit_features.shape[0]),
                int(calibration_features.shape[0]),
                lower_offset,
                upper_offset,
                tuple(issue + timedelta(days=horizon) for issue in fit_issue_dates),
                tuple(issue + timedelta(days=horizon) for issue in calibration_issue_dates),
            )
        )
        models.append(model)
        predictions.append(point)
        lowers.append(max(point + lower_offset, NON_NEGATIVE_FLOOR))
        uppers.append(point + upper_offset)
        actuals.append(actual)
        target_dates.append(target_date)
        if anchor is not None:
            naive.append(anchor)

    if not predictions:
        raise OriginNotEvaluableError(f"origin {origin_date} scored no horizon; no actual was available")
    return OriginBacktest(
        origin_date=origin_date,
        fit_target_last=fit_target_last,
        calibration_target_last=calibration_target_last,
        horizon_runs=tuple(runs),
        target_dates=tuple(target_dates),
        predictions=tuple(predictions),
        lower_bounds=tuple(lowers),
        upper_bounds=tuple(uppers),
        actuals=tuple(actuals),
        naive_predictions=tuple(naive),
        scores=evaluate(
            np.asarray(predictions),
            np.asarray(actuals),
            lower=np.asarray(lowers),
            upper=np.asarray(uppers),
        ),
        models=tuple(models),
    )


@dataclass(frozen=True)
class SkippedOrigin:
    """One origin the sweep could not score, and the reason, so a thin run reads as thin."""

    origin_date: date
    reason: str

    def to_summary(self) -> dict[str, object]:
        """Render the skip for a receipt's JSONB."""
        return {"origin_date": self.origin_date.isoformat(), "reason": self.reason}


@dataclass(frozen=True)
class RollingOriginBacktest:
    """Every scored origin, the pooled aggregate, and the per-horizon breakdown across origins."""

    origins: tuple[OriginBacktest, ...]
    skipped: tuple[SkippedOrigin, ...]
    aggregate: ForecastEvaluation
    horizon_count: int

    @property
    def newest_origin(self) -> date:
        """The most recent scored origin; the run's issue instant is derived from it."""
        return max(origin.origin_date for origin in self.origins)

    @property
    def earliest_origin(self) -> date:
        """The oldest scored origin; the run's valid_from is derived from it."""
        return min(origin.origin_date for origin in self.origins)

    def per_horizon(self) -> list[dict[str, object]]:
        """Pool each lead time across origins, so a horizon's error carries its own origin count."""
        by_horizon: dict[int, list[tuple[float, float]]] = {}
        for origin in self.origins:
            for offset, target_date in enumerate(origin.target_dates):
                horizon = (target_date - origin.origin_date).days
                by_horizon.setdefault(horizon, []).append((origin.predictions[offset], origin.actuals[offset]))
        summaries: list[dict[str, object]] = []
        for horizon in sorted(by_horizon):
            pairs = by_horizon[horizon]
            scores = evaluate(
                np.asarray([prediction for prediction, _ in pairs]),
                np.asarray([actual for _, actual in pairs]),
            )
            summaries.append({"horizon": horizon, "origin_count": len(pairs), **scores.to_summary()})
        return summaries

    def to_summary(self) -> dict[str, object]:
        """Render the whole sweep for a receipt's JSONB, sample sizes beside every metric."""
        return {
            "origin_count": len(self.origins),
            "skipped_origin_count": len(self.skipped),
            "horizon_count": self.horizon_count,
            "horizon_origin_offset": HORIZON_ORIGIN_OFFSET,
            "earliest_origin": self.earliest_origin.isoformat(),
            "newest_origin": self.newest_origin.isoformat(),
            "aggregate": self.aggregate.to_summary(),
            "per_horizon": self.per_horizon(),
            "origins": [origin.to_summary() for origin in self.origins],
            "skipped_origins": [skip.to_summary() for skip in self.skipped],
        }


def run_rolling_origin_backtest(  # noqa: PLR0913 - keyword-only sweep boundaries are the contract
    matrix: CovariateMatrix,
    targets: Mapping[date, float],
    *,
    origin_dates: Sequence[date],
    calibration_days: int = DEFAULT_CALIBRATION_DAYS,
    horizon_count: int = DEFAULT_HORIZON_COUNT,
    alpha: float = DEFAULT_RIDGE_ALPHA,
) -> RollingOriginBacktest:
    """Refit the whole model at every origin and pool the held-out pairs across them.

    The model is genuinely re-estimated per origin -- each origin's fit and calibration windows
    are recomputed from its OWN cutoff -- so an earlier origin never sees a later origin's data.
    An origin that cannot be scored is recorded as skipped rather than failing the sweep, but a
    sweep that scored nothing raises, because reporting an empty aggregate as a result is worse
    than refusing.
    """
    scored: list[OriginBacktest] = []
    skipped: list[SkippedOrigin] = []
    for origin_date in sorted(origin_dates):
        fit_target_last, calibration_target_last = origin_split(origin_date, calibration_days=calibration_days)
        try:
            scored.append(
                run_direct_multi_horizon(
                    matrix,
                    targets,
                    fit_target_last=fit_target_last,
                    calibration_target_last=calibration_target_last,
                    origin_date=origin_date,
                    horizon_count=horizon_count,
                    alpha=alpha,
                )
            )
        except OriginNotEvaluableError as error:
            skipped.append(SkippedOrigin(origin_date, str(error)))
    if not scored:
        reasons = "; ".join(skip.reason for skip in skipped) or "no origins were requested"
        raise OriginNotEvaluableError(f"no rolling origin could be scored: {reasons}")

    predictions = np.asarray([value for origin in scored for value in origin.predictions])
    actuals = np.asarray([value for origin in scored for value in origin.actuals])
    lowers = np.asarray([value for origin in scored for value in origin.lower_bounds])
    uppers = np.asarray([value for origin in scored for value in origin.upper_bounds])
    return RollingOriginBacktest(
        origins=tuple(scored),
        skipped=tuple(skipped),
        aggregate=evaluate(predictions, actuals, lower=lowers, upper=uppers),
        horizon_count=horizon_count,
    )


def run_out_of_fit_point_scores(  # noqa: PLR0913 - keyword-only split boundaries are the contract
    matrix: CovariateMatrix,
    targets: Mapping[date, float],
    *,
    fit_target_last: date,
    evaluation_target_last: date,
    horizon_count: int = DEFAULT_HORIZON_COUNT,
    alpha: float = DEFAULT_RIDGE_ALPHA,
) -> dict[str, object]:
    """Point-only stability check: one fit per horizon, scored on every out-of-fit target date.

    Deliberately not a rolling-origin backtest -- the model is never refitted as the origin
    advances, so this measures out-of-fit point skill, not re-estimation stability.
    `run_rolling_origin_backtest` is the one that refits.
    """
    history_first = matrix.dates[0] if matrix.dates else fit_target_last
    absolute_errors: list[float] = []
    squared_errors: list[float] = []
    for horizon in range(horizon_count):
        fit_features, fit_targets, _ = build_horizon_dataset(
            matrix, targets, horizon=horizon, target_first=history_first, target_last=fit_target_last
        )
        eval_features, eval_targets, _ = build_horizon_dataset(
            matrix,
            targets,
            horizon=horizon,
            target_first=fit_target_last + timedelta(days=1),
            target_last=evaluation_target_last,
        )
        if fit_features.shape[0] == 0 or eval_features.shape[0] == 0:
            continue
        model = fit_ridge(fit_features, fit_targets, alpha=alpha)
        errors = predict(model, eval_features) - eval_targets
        absolute_errors.extend(np.abs(errors).tolist())
        squared_errors.extend((errors**2).tolist())
    return {
        "evaluated_count": len(absolute_errors),
        "mean_absolute_error": float(np.mean(absolute_errors)) if absolute_errors else None,
        "root_mean_squared_error": float(np.sqrt(np.mean(squared_errors))) if squared_errors else None,
    }


def model_document(
    backtest: RollingOriginBacktest,
    *,
    feature_names: Sequence[str],
    alpha: float,
) -> dict[str, object]:
    """The fitted model itself, canonically shaped: one coefficient block per (origin, horizon).

    This is what gets stored as the checksummed inline artifact, so it must contain everything
    needed to reproduce a prediction and nothing that varies between two identical fits.
    """
    return {
        "document_version": "agri_covariate_wind_ridge_v1",
        "algorithm": "standardized_closed_form_ridge",
        "ridge_alpha": alpha,
        "feature_names": list(feature_names),
        "non_negative_floor": NON_NEGATIVE_FLOOR,
        "band_quantiles": [LOWER_QUANTILE, UPPER_QUANTILE],
        "origins": [
            {
                "origin_date": origin.origin_date.isoformat(),
                "fit_target_last": origin.fit_target_last.isoformat(),
                "calibration_target_last": origin.calibration_target_last.isoformat(),
                "horizons": [
                    {
                        "horizon": run.horizon,
                        "fit_row_count": run.fit_row_count,
                        "calibration_row_count": run.calibration_row_count,
                        "lower_offset": run.lower_offset,
                        "upper_offset": run.upper_offset,
                        "target_mean": model.target_mean,
                        "feature_mean": model.feature_mean.tolist(),
                        "feature_scale": model.feature_scale.tolist(),
                        "coefficients": model.coefficients.tolist(),
                    }
                    for run, model in zip(origin.horizon_runs, origin.models, strict=True)
                ],
            }
            for origin in backtest.origins
        ],
    }


def leading_coefficients(
    backtest: RollingOriginBacktest,
    *,
    feature_names: Sequence[str],
    limit: int = 10,
) -> list[dict[str, object]]:
    """The largest-magnitude standardized coefficients of the newest origin's horizon-1 fit.

    A bounded readable summary for the receipt's JSONB; the full coefficient set lives in the
    checksummed artifact. Standardized, so magnitudes are comparable across features.
    """
    newest = max(backtest.origins, key=lambda origin: origin.origin_date)
    if not newest.models:
        return []
    coefficients = newest.models[0].coefficients
    ranked = sorted(range(len(coefficients)), key=lambda index: -abs(float(coefficients[index])))
    return [
        {"feature_name": feature_names[index], "standardized_coefficient": float(coefficients[index])}
        for index in ranked[:limit]
    ]
