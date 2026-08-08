"""Database-free proofs for the covariate wind model's split, fit, and scoring arithmetic."""

import math
from datetime import date, timedelta

import numpy as np
import pytest

from agri_data_service.execution.covariate_wind_model import (
    CovariateMatrix,
    OriginNotEvaluableError,
    build_horizon_dataset,
    evaluate,
    fit_ridge,
    origin_split,
    persistence_anchor,
    predict,
    rolling_origin_dates,
    run_direct_multi_horizon,
    run_rolling_origin_backtest,
)

FEATURE_NAMES = ("feature_a", "feature_b")
DAY_COUNT = 20
INCOMPLETE_POSITION = 5
RIDGE_TOLERANCE = 0.05
EVALUATE_ROW_COUNT = 4

# A window long enough that three rolling origins each get their own fit and calibration span.
SWEEP_DAY_COUNT = 200
SWEEP_FEATURE_COUNT = 3
SWEEP_HORIZON_COUNT = 3
SWEEP_CALIBRATION_DAYS = 20
SWEEP_ORIGIN_COUNT = 3
SWEEP_ORIGIN_STRIDE_DAYS = 5
SWEEP_FIRST_DAY = date(2025, 1, 1)
SWEEP_ORIGIN_DATE = SWEEP_FIRST_DAY + timedelta(days=SWEEP_DAY_COUNT - SWEEP_HORIZON_COUNT - 1)
BLOCKED_DAY_COUNT = 2
TARGET_MISSING_DAY_COUNT = 3
SWEEP_SKIPPED_SURVIVOR_COUNT = 2
PERSISTENCE_ANCHOR_VALUE = 7.0


def _matrix(*, incomplete: tuple[int, ...] = ()) -> CovariateMatrix:
    dates = tuple(date(2026, 1, 1 + offset) for offset in range(DAY_COUNT))
    values = np.column_stack([np.arange(DAY_COUNT, dtype=float), np.arange(DAY_COUNT, dtype=float) ** 0.5])
    complete = np.ones(DAY_COUNT, dtype=bool)
    for position in incomplete:
        complete[position] = False
    return CovariateMatrix(dates, FEATURE_NAMES, values, complete, {})


def sweep_matrix(*, blocked_positions: tuple[int, ...] = ()) -> CovariateMatrix:
    """A long, dense synthetic matrix whose target is a clean linear function of its features."""
    dates = tuple(SWEEP_FIRST_DAY + timedelta(days=offset) for offset in range(SWEEP_DAY_COUNT))
    generator = np.random.default_rng(11)
    values = generator.normal(size=(SWEEP_DAY_COUNT, SWEEP_FEATURE_COUNT))
    complete = np.ones(SWEEP_DAY_COUNT, dtype=bool)
    blocking: dict[str, int] = {}
    for position in blocked_positions:
        complete[position] = False
        blocking["feature_c"] = blocking.get("feature_c", 0) + 1
    return CovariateMatrix(
        dates,
        ("feature_a", "feature_b", "feature_c"),
        values,
        complete,
        {"day_count": SWEEP_DAY_COUNT, "manifest_checksum": "0" * 64},
        blocking,
    )


def sweep_targets(matrix: CovariateMatrix, *, missing_tail: int = 0) -> dict[date, float]:
    """A strictly positive target per day, optionally leaving the last few days unobserved."""
    usable = matrix.dates if missing_tail == 0 else matrix.dates[:-missing_tail]
    return {day: 5.0 + 0.1 * position for position, day in enumerate(usable)}


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


def test_origin_split_puts_both_boundaries_strictly_before_the_origin() -> None:
    origin = date(2026, 6, 1)

    fit_target_last, calibration_target_last = origin_split(origin, calibration_days=SWEEP_CALIBRATION_DAYS)

    assert calibration_target_last < origin
    assert fit_target_last < calibration_target_last
    assert (calibration_target_last - fit_target_last).days == SWEEP_CALIBRATION_DAYS


def test_origin_split_refuses_a_zero_length_calibration_window() -> None:
    with pytest.raises(ValueError, match="calibration_days"):
        origin_split(date(2026, 6, 1), calibration_days=0)


def test_run_direct_multi_horizon_never_lets_a_fitted_target_reach_its_own_origin() -> None:
    """The split boundary the teardown flagged as untested: this pins it for every horizon."""
    matrix = sweep_matrix()
    targets = sweep_targets(matrix)
    fit_target_last, calibration_target_last = origin_split(SWEEP_ORIGIN_DATE, calibration_days=SWEEP_CALIBRATION_DAYS)

    backtest = run_direct_multi_horizon(
        matrix,
        targets,
        fit_target_last=fit_target_last,
        calibration_target_last=calibration_target_last,
        origin_date=SWEEP_ORIGIN_DATE,
        horizon_count=SWEEP_HORIZON_COUNT,
    )

    assert len(backtest.horizon_runs) == SWEEP_HORIZON_COUNT
    for run in backtest.horizon_runs:
        assert run.fit_target_dates, "a horizon with no fit rows would make the assertions below vacuous"
        assert run.calibration_target_dates
        # No fit target may reach into the calibration window.
        assert max(run.fit_target_dates) <= fit_target_last
        # No calibration target may reach back into the fit window, nor forward to the origin.
        assert min(run.calibration_target_dates) > fit_target_last
        assert max(run.calibration_target_dates) <= calibration_target_last
        # The two windows are disjoint, and neither touches the day the model is scored from.
        assert not set(run.fit_target_dates) & set(run.calibration_target_dates)
        assert max((*run.fit_target_dates, *run.calibration_target_dates)) < SWEEP_ORIGIN_DATE


def test_rolling_origin_backtest_refits_per_origin_and_no_origin_crosses_its_own_cutoff() -> None:
    matrix = sweep_matrix()
    targets = sweep_targets(matrix)
    origins = rolling_origin_dates(
        SWEEP_ORIGIN_DATE, origin_count=SWEEP_ORIGIN_COUNT, origin_stride_days=SWEEP_ORIGIN_STRIDE_DAYS
    )

    sweep = run_rolling_origin_backtest(
        matrix,
        targets,
        origin_dates=origins,
        calibration_days=SWEEP_CALIBRATION_DAYS,
        horizon_count=SWEEP_HORIZON_COUNT,
    )

    assert len(sweep.origins) == SWEEP_ORIGIN_COUNT
    assert sweep.newest_origin == SWEEP_ORIGIN_DATE
    assert sweep.earliest_origin == min(origins)
    for origin in sweep.origins:
        # A model refitted at an EARLIER origin must never have seen a LATER origin's days.
        assert origin.calibration_target_last < origin.origin_date
        assert origin.fit_target_last < origin.calibration_target_last
        for run in origin.horizon_runs:
            assert max((*run.fit_target_dates, *run.calibration_target_dates)) < origin.origin_date
        # Refitting means one fitted model per horizon at this origin, not one reused across origins.
        assert len(origin.models) == len(origin.horizon_runs)
    first, second = sweep.origins[0], sweep.origins[1]
    assert not np.array_equal(first.models[0].coefficients, second.models[0].coefficients)


def test_rolling_origin_dates_walk_back_from_the_newest_and_come_out_ascending() -> None:
    origins = rolling_origin_dates(date(2026, 6, 30), origin_count=3, origin_stride_days=7)

    assert origins == (date(2026, 6, 16), date(2026, 6, 23), date(2026, 6, 30))
    assert list(origins) == sorted(origins)


def test_rolling_origin_backtest_records_a_skipped_origin_rather_than_failing_the_sweep() -> None:
    matrix = sweep_matrix()
    targets = sweep_targets(matrix)
    # An origin with no history behind it cannot be scored; the rest of the sweep still must be.
    origins = (SWEEP_FIRST_DAY, *rolling_origin_dates(SWEEP_ORIGIN_DATE, origin_count=2, origin_stride_days=5))

    sweep = run_rolling_origin_backtest(
        matrix,
        targets,
        origin_dates=origins,
        calibration_days=SWEEP_CALIBRATION_DAYS,
        horizon_count=SWEEP_HORIZON_COUNT,
    )

    assert len(sweep.skipped) == 1
    assert sweep.skipped[0].origin_date == SWEEP_FIRST_DAY
    assert len(sweep.origins) == SWEEP_SKIPPED_SURVIVOR_COUNT


def test_rolling_origin_backtest_refuses_a_sweep_that_scored_nothing() -> None:
    matrix = sweep_matrix()

    with pytest.raises(OriginNotEvaluableError, match="no rolling origin could be scored"):
        run_rolling_origin_backtest(
            matrix,
            sweep_targets(matrix),
            origin_dates=(SWEEP_FIRST_DAY,),
            calibration_days=SWEEP_CALIBRATION_DAYS,
            horizon_count=SWEEP_HORIZON_COUNT,
        )


def test_per_horizon_pools_across_origins_and_carries_its_own_origin_count() -> None:
    matrix = sweep_matrix()
    sweep = run_rolling_origin_backtest(
        matrix,
        sweep_targets(matrix),
        origin_dates=rolling_origin_dates(
            SWEEP_ORIGIN_DATE, origin_count=SWEEP_ORIGIN_COUNT, origin_stride_days=SWEEP_ORIGIN_STRIDE_DAYS
        ),
        calibration_days=SWEEP_CALIBRATION_DAYS,
        horizon_count=SWEEP_HORIZON_COUNT,
    )

    per_horizon = sweep.per_horizon()

    assert [entry["horizon"] for entry in per_horizon] == list(range(SWEEP_HORIZON_COUNT))
    # The sample size travels with the metric: this is the count a reader must weigh MAE against.
    assert all(entry["origin_count"] == SWEEP_ORIGIN_COUNT for entry in per_horizon)


def test_coverage_counts_the_excluded_days_and_names_the_feature_that_blocked_them() -> None:
    matrix = sweep_matrix(blocked_positions=tuple(range(BLOCKED_DAY_COUNT)))
    targets = sweep_targets(matrix, missing_tail=TARGET_MISSING_DAY_COUNT)

    coverage = matrix.coverage(targets)

    assert coverage.candidate_day_count == SWEEP_DAY_COUNT
    assert coverage.excluded_day_count == BLOCKED_DAY_COUNT
    assert coverage.feature_complete_day_count == SWEEP_DAY_COUNT - BLOCKED_DAY_COUNT
    # The training set did not silently shrink: days lost to a missing target are counted apart
    # from days lost to an incomplete covariate vector.
    assert coverage.target_missing_day_count == TARGET_MISSING_DAY_COUNT
    assert coverage.usable_day_count == SWEEP_DAY_COUNT - BLOCKED_DAY_COUNT - TARGET_MISSING_DAY_COUNT
    assert coverage.blocking_features == (("feature_c", BLOCKED_DAY_COUNT),)
    assert coverage.manifest_day_count == SWEEP_DAY_COUNT
    summary = coverage.to_summary()
    assert summary["blocking_features"] == [{"feature_name": "feature_c", "excluded_day_count": BLOCKED_DAY_COUNT}]


def test_coverage_of_a_fully_present_window_excludes_nothing() -> None:
    matrix = sweep_matrix()

    coverage = matrix.coverage(sweep_targets(matrix))

    assert coverage.excluded_day_count == 0
    assert coverage.blocking_features == ()
    assert coverage.feature_complete_fraction == 1.0


def test_persistence_anchor_never_reads_the_origin_day_it_forecasts() -> None:
    origin = date(2026, 6, 10)
    targets = {origin: 99.0, origin - timedelta(days=1): 7.0}

    assert persistence_anchor(targets, origin) == PERSISTENCE_ANCHOR_VALUE


def test_persistence_anchor_gives_up_rather_than_reaching_back_indefinitely() -> None:
    origin = date(2026, 6, 10)

    assert persistence_anchor({origin - timedelta(days=90): 7.0}, origin) is None
