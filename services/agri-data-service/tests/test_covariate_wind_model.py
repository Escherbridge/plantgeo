"""Database-free proofs for the covariate wind model's split, fit, and scoring arithmetic."""

import math
from datetime import date

import numpy as np

from agri_data_service.execution.covariate_wind_model import (
    CovariateMatrix,
    build_horizon_dataset,
    evaluate,
    fit_ridge,
    predict,
)

FEATURE_NAMES = ("feature_a", "feature_b")
DAY_COUNT = 20
INCOMPLETE_POSITION = 5
RIDGE_TOLERANCE = 0.05
EVALUATE_ROW_COUNT = 4


def _matrix(*, incomplete: tuple[int, ...] = ()) -> CovariateMatrix:
    dates = tuple(date(2026, 1, 1 + offset) for offset in range(DAY_COUNT))
    values = np.column_stack([np.arange(DAY_COUNT, dtype=float), np.arange(DAY_COUNT, dtype=float) ** 0.5])
    complete = np.ones(DAY_COUNT, dtype=bool)
    for position in incomplete:
        complete[position] = False
    return CovariateMatrix(dates, FEATURE_NAMES, values, complete, {})


def test_build_horizon_dataset_never_reaches_past_the_target_window() -> None:
    matrix = _matrix()
    targets = {day: float(index) for index, day in enumerate(matrix.dates)}
    target_last = date(2026, 1, 10)

    features, values, issue_dates = build_horizon_dataset(
        matrix, targets, horizon=3, target_first=matrix.dates[0], target_last=target_last
    )

    assert features.shape[0] == values.size == len(issue_dates)
    # every included row's target date is inside the window: no later day can influence the fit
    assert max(issue_dates).toordinal() + 3 <= target_last.toordinal()
    assert values.tolist() == [float(matrix.dates.index(issue) + 3) for issue in issue_dates]


def test_build_horizon_dataset_drops_incomplete_covariate_rows() -> None:
    matrix = _matrix(incomplete=(INCOMPLETE_POSITION,))
    targets = {day: float(index) for index, day in enumerate(matrix.dates)}

    _, _, issue_dates = build_horizon_dataset(
        matrix, targets, horizon=0, target_first=matrix.dates[0], target_last=matrix.dates[-1]
    )

    assert matrix.dates[INCOMPLETE_POSITION] not in issue_dates
    assert len(issue_dates) == DAY_COUNT - 1


def test_fit_ridge_recovers_a_linear_relationship_and_predict_floors_at_zero() -> None:
    generator = np.random.default_rng(7)
    features = generator.normal(size=(400, 2))
    targets = 3.0 + 2.0 * features[:, 0] - 1.0 * features[:, 1]

    model = fit_ridge(features, targets, alpha=1e-6)
    predictions = predict(model, features)

    assert math.isclose(model.target_mean, float(targets.mean()))
    assert float(np.max(np.abs(predictions - np.maximum(targets, 0.0)))) < RIDGE_TOLERANCE
    assert float(np.min(predict(model, np.array([[-100.0, 100.0]])))) == 0.0


def test_evaluate_reports_mae_rmse_and_interval_coverage() -> None:
    predictions = np.array([1.0, 2.0, 3.0, 4.0])
    actuals = np.array([1.5, 2.5, 2.0, 4.0])
    lower = np.array([0.5, 1.5, 2.5, 3.5])
    upper = np.array([1.5, 2.5, 3.5, 4.5])

    scores = evaluate(predictions, actuals, lower=lower, upper=upper)

    assert scores.evaluated_count == EVALUATE_ROW_COUNT
    assert math.isclose(scores.mean_absolute_error, (0.5 + 0.5 + 1.0 + 0.0) / 4)
    assert math.isclose(scores.root_mean_squared_error, math.sqrt((0.25 + 0.25 + 1.0 + 0.0) / 4))
    assert scores.interval_coverage is not None
    assert math.isclose(scores.interval_coverage, 3 / 4)


def test_evaluate_omits_coverage_when_no_band_was_supplied() -> None:
    scores = evaluate(np.array([1.0, 2.0]), np.array([1.0, 3.0]))

    assert scores.interval_coverage is None
    assert math.isclose(scores.mean_absolute_error, 0.5)
