"""Evaluation-only direct multi-horizon ridge forecaster over the 0016 covariate layer.

Rationale, scope limits, and the leakage argument live in `AGENTS.md` (this
directory) under `covariate_wind_model.py`.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import numpy as np
import psycopg2

if TYPE_CHECKING:
    from collections.abc import Sequence

SCHEMA_VERSION = "agri_covariates_v1"
TARGET_SIGNAL_NAME = "wind_speed"
DEFAULT_RIDGE_ALPHA = 10.0
DEFAULT_HORIZON_COUNT = 30
DEFAULT_CALIBRATION_DAYS = 180
LOWER_QUANTILE = 0.10
UPPER_QUANTILE = 0.90
NON_NEGATIVE_FLOOR = 0.0
STATEMENT_TIMEOUT_MS = 120_000


@dataclass(frozen=True)
class CovariateMatrix:
    """Dense (date x pinned-feature) design matrix plus its per-row completeness mask."""

    dates: tuple[date, ...]
    feature_names: tuple[str, ...]
    values: np.ndarray
    complete: np.ndarray
    manifest: dict[str, Any]


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


def _utc_midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def load_covariate_matrix(  # noqa: PLR0913 - keyword-only window/gate parameters are the contract
    connection: psycopg2.extensions.connection,
    *,
    cell_id: str,
    window_start: date,
    window_end: date,
    as_of_time: datetime,
    schema_version: str = SCHEMA_VERSION,
) -> CovariateMatrix:
    """Pivot `agri.covariate_daily_features` into a dense matrix in pinned feature order."""
    with connection.cursor() as cursor:
        cursor.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
        cursor.execute(
            "SELECT feature_index, feature_name FROM agri.covariate_feature_schema(%s)",
            (schema_version,),
        )
        schema_rows = cursor.fetchall()
        feature_names = tuple(name for _, name in schema_rows)
        index_of = {feature_index: position for position, (feature_index, _) in enumerate(schema_rows)}

        cursor.execute(
            """
            SELECT observed_date, feature_index, feature_value, input_count, expected_input_count
            FROM agri.covariate_daily_features(%s, %s, %s, %s, %s)
            """,
            (cell_id, _utc_midnight(window_start), _utc_midnight(window_end), as_of_time, schema_version),
        )
        rows = cursor.fetchall()

        cursor.execute(
            "SELECT * FROM agri.covariate_vector_manifest(%s, %s, %s, %s, %s)",
            (cell_id, _utc_midnight(window_start), _utc_midnight(window_end), as_of_time, schema_version),
        )
        manifest_description = cursor.description or ()
        manifest_columns = [column.name for column in manifest_description]
        manifest_row = cursor.fetchone()
        manifest = dict(zip(manifest_columns, manifest_row, strict=True)) if manifest_row else {}

    dates = tuple(sorted({row[0] for row in rows}))
    row_of = {day: position for position, day in enumerate(dates)}
    values = np.full((len(dates), len(feature_names)), np.nan, dtype=float)
    complete = np.ones(len(dates), dtype=bool)
    for observed_date, feature_index, feature_value, input_count, expected_input_count in rows:
        row = row_of[observed_date]
        column = index_of[feature_index]
        if feature_value is None or input_count != expected_input_count:
            complete[row] = False
            continue
        values[row, column] = float(feature_value)
    return CovariateMatrix(dates, feature_names, values, complete, manifest)


def load_target_series(
    connection: psycopg2.extensions.connection,
    *,
    cell_id: str,
    as_of_time: datetime,
    signal_name: str = TARGET_SIGNAL_NAME,
) -> dict[date, float]:
    """Return the availability-gated observed target value per UTC day."""
    with connection.cursor() as cursor:
        cursor.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
        cursor.execute(
            """
            SELECT (observed_at AT TIME ZONE 'UTC')::date AS observed_date, normalized_value
            FROM agri.signal_observation
            WHERE cell_id = %s
              AND signal_name = %s
              AND support_key = 'surface'
              AND quality_flag = 'accepted'
              AND is_observed
              AND normalized_value IS NOT NULL
              AND data_available_at <= %s
            ORDER BY observed_at
            """,
            (cell_id, signal_name, as_of_time),
        )
        return {observed_date: float(value) for observed_date, value in cursor.fetchall()}


def build_horizon_dataset(
    matrix: CovariateMatrix,
    targets: dict[date, float],
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
    """MAE / RMSE, plus p10-p90 interval coverage when a band was supplied."""
    errors = predictions - actuals
    coverage: float | None = None
    if lower is not None and upper is not None:
        coverage = float(np.mean((actuals >= lower) & (actuals <= upper)))
    return ForecastEvaluation(
        evaluated_count=int(actuals.size),
        mean_absolute_error=float(np.mean(np.abs(errors))),
        root_mean_squared_error=float(np.sqrt(np.mean(errors**2))),
        interval_coverage=coverage,
    )


@dataclass(frozen=True)
class HorizonRun:
    """One fitted horizon: its model, its calibration residual band, and its holdout row."""

    horizon: int
    fit_row_count: int
    calibration_row_count: int
    lower_offset: float
    upper_offset: float


def run_direct_multi_horizon(  # noqa: PLR0913 - keyword-only split boundaries are the contract
    matrix: CovariateMatrix,
    targets: dict[date, float],
    *,
    fit_target_last: date,
    calibration_target_last: date,
    origin_date: date,
    horizon_count: int = DEFAULT_HORIZON_COUNT,
    alpha: float = DEFAULT_RIDGE_ALPHA,
) -> dict[str, Any]:
    """Fit one ridge per horizon, calibrate its band, then score the single held-out origin."""
    history_first = matrix.dates[0]
    origin_position = matrix.dates.index(origin_date)
    origin_features = matrix.values[origin_position].reshape(1, -1)
    if not matrix.complete[origin_position]:
        raise ValueError(f"origin {origin_date} has an incomplete covariate vector; refusing to forecast from it")

    runs: list[HorizonRun] = []
    predictions: list[float] = []
    lowers: list[float] = []
    uppers: list[float] = []
    actuals: list[float] = []
    target_dates: list[date] = []

    for horizon in range(horizon_count):
        fit_features, fit_targets, _ = build_horizon_dataset(
            matrix, targets, horizon=horizon, target_first=history_first, target_last=fit_target_last
        )
        calibration_features, calibration_targets, _ = build_horizon_dataset(
            matrix,
            targets,
            horizon=horizon,
            target_first=fit_target_last + timedelta(days=1),
            target_last=calibration_target_last,
        )
        if fit_features.shape[0] == 0 or calibration_features.shape[0] == 0:
            raise ValueError(f"horizon {horizon} has no fit or calibration rows; widen the training window")

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
            )
        )
        predictions.append(point)
        lowers.append(max(point + lower_offset, NON_NEGATIVE_FLOOR))
        uppers.append(point + upper_offset)
        actuals.append(actual)
        target_dates.append(target_date)

    scores = evaluate(
        np.asarray(predictions),
        np.asarray(actuals),
        lower=np.asarray(lowers),
        upper=np.asarray(uppers),
    )
    return {
        "origin_date": origin_date.isoformat(),
        "horizon_count": len(predictions),
        "fit_target_last": fit_target_last.isoformat(),
        "calibration_target_last": calibration_target_last.isoformat(),
        "fit_row_count_min": min((run.fit_row_count for run in runs), default=0),
        "calibration_row_count_min": min((run.calibration_row_count for run in runs), default=0),
        "evaluated_count": scores.evaluated_count,
        "mean_absolute_error": scores.mean_absolute_error,
        "root_mean_squared_error": scores.root_mean_squared_error,
        "interval_coverage": scores.interval_coverage,
        "mean_interval_width": float(np.mean(np.asarray(uppers) - np.asarray(lowers))) if uppers else None,
        "target_dates": [day.isoformat() for day in target_dates],
        "predictions": predictions,
        "lower_bounds": lowers,
        "upper_bounds": uppers,
        "actuals": actuals,
    }


def run_out_of_fit_point_scores(  # noqa: PLR0913 - keyword-only split boundaries are the contract
    matrix: CovariateMatrix,
    targets: dict[date, float],
    *,
    fit_target_last: date,
    evaluation_target_last: date,
    horizon_count: int = DEFAULT_HORIZON_COUNT,
    alpha: float = DEFAULT_RIDGE_ALPHA,
) -> dict[str, Any]:
    """Point-only stability check: one fit per horizon, scored on every out-of-fit target date.

    Deliberately not a rolling-origin backtest -- the model is never refitted as the origin
    advances, so this measures out-of-fit point skill, not re-estimation stability.
    """
    history_first = matrix.dates[0]
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


def load_baseline_evaluation(
    connection: psycopg2.extensions.connection,
    *,
    series_id: str,
    as_of_time: datetime,
) -> list[dict[str, Any]]:
    """Read the existing statistical bootstrap baseline through its own leakage-gated function."""
    with connection.cursor() as cursor:
        cursor.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
        cursor.execute(
            "SELECT * FROM agri.forecast_iteration_evaluation(%s, %s)",
            (series_id, as_of_time),
        )
        columns = [column.name for column in cursor.description or ()]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _parse_as_of_time(value: str) -> datetime:
    """Parse an ISO-8601 as-of instant, defaulting a naive value to UTC."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the evaluation-only comparison and print one JSON report to stdout."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--cell-id", required=True)
    parser.add_argument("--series-id", required=True)
    parser.add_argument("--history-start", type=_parse_date, required=True)
    parser.add_argument("--history-end", type=_parse_date, required=True)
    parser.add_argument("--origin-date", type=_parse_date, required=True)
    parser.add_argument(
        "--as-of-time",
        type=_parse_as_of_time,
        default=None,
        help="availability gate fed to p_as_of_time; defaults to now(). Pin it to reproduce a manifest checksum.",
    )
    parser.add_argument("--horizon-count", type=int, default=DEFAULT_HORIZON_COUNT)
    parser.add_argument("--calibration-days", type=int, default=DEFAULT_CALIBRATION_DAYS)
    parser.add_argument("--alpha", type=float, default=DEFAULT_RIDGE_ALPHA)
    arguments = parser.parse_args(argv)

    as_of_time = arguments.as_of_time or datetime.now(tz=UTC)
    connection = psycopg2.connect(arguments.dsn)
    connection.autocommit = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        matrix = load_covariate_matrix(
            connection,
            cell_id=arguments.cell_id,
            window_start=arguments.history_start,
            window_end=arguments.history_end,
            as_of_time=as_of_time,
        )
        targets = load_target_series(connection, cell_id=arguments.cell_id, as_of_time=as_of_time)
        baseline = load_baseline_evaluation(connection, series_id=arguments.series_id, as_of_time=as_of_time)
    finally:
        connection.rollback()
        connection.close()

    calibration_target_last = arguments.origin_date - timedelta(days=1)
    fit_target_last = calibration_target_last - timedelta(days=arguments.calibration_days)
    primary = run_direct_multi_horizon(
        matrix,
        targets,
        fit_target_last=fit_target_last,
        calibration_target_last=calibration_target_last,
        origin_date=arguments.origin_date,
        horizon_count=arguments.horizon_count,
        alpha=arguments.alpha,
    )
    out_of_fit = run_out_of_fit_point_scores(
        matrix,
        targets,
        fit_target_last=fit_target_last,
        evaluation_target_last=calibration_target_last,
        horizon_count=arguments.horizon_count,
        alpha=arguments.alpha,
    )

    report = {
        "label": "evaluation_only",
        "disclaimer": (
            "Pipeline/evaluation evidence for a single cell and metric. Not an operational "
            "forecast, not life-safety validated, never joined to a serving or publication surface."
        ),
        "as_of_time": as_of_time.isoformat(),
        "schema_version": SCHEMA_VERSION,
        "cell_id": arguments.cell_id,
        "series_id": arguments.series_id,
        "covariate_manifest": {
            key: (str(value) if isinstance(value, list | dict) else value)
            for key, value in matrix.manifest.items()
            if key
            in {
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
        },
        "declared_gaps": matrix.manifest.get("declared_gaps"),
        "target_day_count": len(targets),
        "model": primary,
        "model_out_of_fit_point_only": out_of_fit,
        "baseline_iterations": [
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
            for row in baseline
        ],
    }
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
