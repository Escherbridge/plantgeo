"""Tests for the rolling-origin harness: schedule, leakage guard, metrics, bootstrap, abstention."""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pytest

from agri_data_service.method.ml.seasonal_candidates import (
    DailySeries,
    PersistenceCandidate,
    Prediction,
    SeasonalClimatologyCandidate,
    build_daily_series,
)
from agri_data_service.method.ml.seasonal_evaluation import (
    MINIMUM_DEVELOPMENT_ORIGINS,
    MINIMUM_FINAL_HOLDOUT_ORIGINS,
    ScoredOrigin,
    SeasonalLeakageError,
    _require_history_before,
    bootstrap_skill_interval,
    build_rolling_origin_plan,
    evaluate_abstention,
    pass_fraction,
    run_candidate,
    season_of,
    skill_versus_persistence,
    slice_by_season,
    summarize,
)

START = date(2022, 4, 30)


def _series(days: int = 1560, *, key: str = "boise|wind_speed|surface") -> DailySeries:
    observations = {
        START + timedelta(days=index): 4.0 + 2.0 * math.sin(2 * math.pi * index / 365.25) for index in range(days)
    }
    return build_daily_series(key, "m/s", observations)


def test_the_pre_registered_schedule_produces_the_pre_registered_counts() -> None:
    plan = build_rolling_origin_plan(
        history_start=START,
        last_observed=date(2026, 8, 6),
        holdout_boundary=date(2025, 8, 6),
    )
    assert len(plan.origins) == 27  # noqa: PLR2004
    assert len(plan.by_fold("development")) == 16  # noqa: PLR2004
    assert len(plan.by_fold("final_holdout")) == 11  # noqa: PLR2004
    assert plan.origins[0].origin == date(2024, 4, 29)
    # The last origin sits on the 30-day stride, not on the last calendar date whose target window
    # would still close inside the corpus (2026-07-07). The pre-registered counts are unaffected.
    assert plan.origins[-1].origin == date(2026, 6, 18)
    assert plan.origins[-1].target_days[-1] == date(2026, 7, 18)


def test_consecutive_target_windows_never_overlap() -> None:
    plan = build_rolling_origin_plan(
        history_start=START, last_observed=date(2026, 8, 6), holdout_boundary=date(2025, 8, 6)
    )
    for earlier, later in zip(plan.origins, plan.origins[1:], strict=False):
        assert earlier.target_days[-1] < later.target_days[0]


def test_a_stride_shorter_than_the_horizon_is_refused() -> None:
    with pytest.raises(ValueError, match="stride must be at least the horizon"):
        build_rolling_origin_plan(
            history_start=START,
            last_observed=date(2026, 8, 6),
            holdout_boundary=date(2025, 8, 6),
            horizon_steps=30,
            stride_days=7,
        )


def test_the_leakage_guard_raises_on_a_history_that_reaches_its_own_origin() -> None:
    series = _series(100)
    origin = START + timedelta(days=50)
    with pytest.raises(SeasonalLeakageError, match="history reaches"):
        _require_history_before(series, origin, series.series_key)


def test_the_leakage_guard_accepts_a_correctly_sliced_history() -> None:
    series = _series(100)
    origin = START + timedelta(days=50)
    _require_history_before(series.before(origin), origin, series.series_key)


def test_every_scored_origin_passed_through_the_leakage_guard() -> None:
    series = _series()
    plan = build_rolling_origin_plan(
        history_start=START, last_observed=date(2026, 8, 6), holdout_boundary=date(2025, 8, 6)
    )
    run = run_candidate(series, PersistenceCandidate(), plan)
    assert len(run.scored_origins) == 27  # noqa: PLR2004
    assert run.leakage_checked_calls > len(run.scored_origins)
    assert run.skipped_origins == ()


def test_an_origin_without_enough_training_history_is_skipped_not_scored() -> None:
    series = _series(800)
    plan = build_rolling_origin_plan(
        history_start=START,
        last_observed=START + timedelta(days=799),
        holdout_boundary=START + timedelta(days=760),
        minimum_history_days=790,
    )
    run = run_candidate(series, PersistenceCandidate(), plan)
    assert run.scored_origins == ()
    assert all(reason == "insufficient_training_history" for _, reason in run.skipped_origins)


def _scored(origin: date, series_key: str, median: list[float], actual: list[float]) -> ScoredOrigin:
    return ScoredOrigin(
        series_key=series_key,
        candidate_name="candidate",
        origin=origin,
        fold_kind="final_holdout",
        target_days=tuple(origin + timedelta(days=step) for step in range(1, len(median) + 1)),
        horizon_steps=tuple(range(1, len(median) + 1)),
        median=tuple(median),
        low=tuple(value - 1.0 for value in median),
        high=tuple(value + 1.0 for value in median),
        actual=tuple(actual),
    )


def test_summarize_computes_mae_rmse_bias_and_coverage() -> None:
    scored = [_scored(date(2025, 1, 1), "s", [1.0, 2.0, 3.0], [1.5, 2.0, 1.0])]
    summary = summarize(scored)
    assert summary.mae == pytest.approx((0.5 + 0.0 + 2.0) / 3)
    assert summary.rmse == pytest.approx(math.sqrt((0.25 + 0.0 + 4.0) / 3))
    assert summary.bias == pytest.approx((-0.5 + 0.0 + 2.0) / 3)
    assert summary.interval_coverage == pytest.approx(2 / 3)


def test_mape_is_withheld_when_an_actual_sits_near_zero() -> None:
    summary = summarize([_scored(date(2025, 1, 1), "s", [1.0, 2.0], [0.2, 2.0])])
    assert summary.mape is None
    assert summary.mape_reason == "mape_undefined_near_zero"


def test_mape_is_reported_when_every_actual_is_far_from_zero() -> None:
    summary = summarize([_scored(date(2025, 1, 1), "s", [11.0, 20.0], [10.0, 20.0])])
    assert summary.mape == pytest.approx(5.0)
    assert summary.mape_reason is None


def test_an_empty_scored_set_reports_undefined_rather_than_zero() -> None:
    summary = summarize([])
    assert summary.is_empty
    assert math.isnan(summary.mae)
    assert summary.interval_coverage is None


def test_skill_against_a_degenerate_baseline_is_undefined() -> None:
    perfect = summarize([_scored(date(2025, 1, 1), "s", [1.0], [1.0])])
    assert skill_versus_persistence(perfect, perfect) is None


def test_the_bootstrap_of_a_baseline_against_itself_is_exactly_zero() -> None:
    """The regression guard for a real defect: keying the baseline on the origin date alone
    silently paired every series with whichever one was indexed last, and produced a non-zero
    interval for a candidate compared with itself."""
    scored = [
        _scored(date(2025, 1, 1) + timedelta(days=30 * index), key, [1.0, 2.0], [1.5, 2.5])
        for index in range(5)
        for key in ("series-a", "series-b")
    ]
    interval = bootstrap_skill_interval(scored, scored, draw_count=200)
    assert interval is not None
    assert interval.point == pytest.approx(0.0)
    assert interval.lower == pytest.approx(0.0)
    assert interval.upper == pytest.approx(0.0)
    assert interval.block_count == 5  # noqa: PLR2004


def test_the_bootstrap_blocks_on_origins_not_on_scored_days() -> None:
    scored = [
        _scored(date(2025, 1, 1) + timedelta(days=30 * index), "series-a", [1.0] * 30, [1.0] * 30) for index in range(4)
    ]
    interval = bootstrap_skill_interval(scored, scored, draw_count=100)
    assert interval is not None
    assert interval.block_count == 4  # noqa: PLR2004


def test_the_bootstrap_needs_at_least_two_blocks() -> None:
    scored = [_scored(date(2025, 1, 1), "series-a", [1.0], [2.0])]
    assert bootstrap_skill_interval(scored, scored, draw_count=10) is None


def test_pass_fraction_pairs_on_series_and_origin() -> None:
    origin = date(2025, 1, 1)
    candidate = [_scored(origin, "series-a", [1.0], [1.0]), _scored(origin, "series-b", [9.0], [1.0])]
    baseline = [_scored(origin, "series-a", [5.0], [1.0]), _scored(origin, "series-b", [1.0], [1.0])]
    assert pass_fraction(candidate, baseline) == pytest.approx(0.5)


def test_season_slicing_splits_a_target_window_that_crosses_a_boundary() -> None:
    scored = [_scored(date(2025, 2, 25), "series-a", [1.0] * 10, [1.0] * 10)]
    sliced = slice_by_season(scored)
    assert set(sliced) == {"DJF", "MAM"}
    assert sum(origin.scored_day_count for origins in sliced.values() for origin in origins) == 10  # noqa: PLR2004


def test_season_labels_follow_meteorological_convention() -> None:
    assert season_of(date(2025, 12, 15)) == "DJF"
    assert season_of(date(2025, 3, 1)) == "MAM"
    assert season_of(date(2025, 7, 4)) == "JJA"
    assert season_of(date(2025, 10, 31)) == "SON"


def test_abstention_fires_when_a_fold_has_too_few_origins() -> None:
    series = _series(1000)
    plan = build_rolling_origin_plan(
        history_start=START,
        last_observed=START + timedelta(days=999),
        holdout_boundary=START + timedelta(days=960),
    )
    run = run_candidate(series, PersistenceCandidate(), plan)
    check = evaluate_abstention(run, plan)
    assert check.must_abstain
    assert any("final_holdout_origins" in reason for reason in check.reasons)
    assert check.final_holdout_origin_count < MINIMUM_FINAL_HOLDOUT_ORIGINS
    assert check.development_origin_count >= MINIMUM_DEVELOPMENT_ORIGINS


def test_abstention_stays_silent_on_the_full_corpus() -> None:
    series = _series()
    plan = build_rolling_origin_plan(
        history_start=START, last_observed=date(2026, 8, 6), holdout_boundary=date(2025, 8, 6)
    )
    check = evaluate_abstention(run_candidate(series, SeasonalClimatologyCandidate(), plan), plan)
    assert not check.must_abstain
    assert check.target_day_coverage == pytest.approx(1.0)


def test_a_residual_calibrated_candidate_produces_a_finite_band() -> None:
    series = _series()
    plan = build_rolling_origin_plan(
        history_start=START, last_observed=date(2026, 8, 6), holdout_boundary=date(2025, 8, 6)
    )
    run = run_candidate(series, SeasonalClimatologyCandidate(), plan)
    lows = np.asarray([value for scored in run.scored_origins for value in scored.low])
    highs = np.asarray([value for scored in run.scored_origins for value in scored.high])
    assert not bool(np.isnan(lows).any())
    assert bool((highs >= lows).all())


def test_a_prediction_reports_its_own_horizon_length() -> None:
    prediction = Prediction(median=np.zeros(7), native_low=None, native_high=None)
    assert prediction.horizon_steps == 7  # noqa: PLR2004
