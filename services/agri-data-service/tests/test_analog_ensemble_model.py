"""Database-free proofs for the AnEn execution layer: the leakage boundary, standardization,
the rolling-origin sweep, and the target-lags-only ablation mask.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from agri_data_service.execution.analog_ensemble_model import (
    FULL_VECTOR_VARIANT,
    TARGET_LAGS_ONLY_VARIANT,
    AnEnOriginRun,
    _standardize,
    eligible_analog_pool,
    model_document,
    run_anen_origin,
    run_anen_rolling_backtest,
    target_lag_feature_mask,
)
from agri_data_service.execution.covariate_wind_model import CovariateMatrix, OriginNotEvaluableError
from agri_data_service.method.ml.analog_ensemble import AnEnHyperparameters

DAY_COUNT = 100
FIRST_DAY = date(2025, 1, 1)
ORIGIN_INDEX = 90
HORIZON_DAYS = 10
FEATURE_NAMES = ("wind_speed_lag_1", "wind_speed_roll_mean_7", "air_temperature_max_lag_1")


def _matrix(*, incomplete: tuple[int, ...] = ()) -> CovariateMatrix:
    dates = tuple(FIRST_DAY + timedelta(days=offset) for offset in range(DAY_COUNT))
    # feature 0 tracks the day's own index exactly, so an analog's identity is unambiguous.
    values = np.column_stack(
        [np.arange(DAY_COUNT, dtype=float), np.arange(DAY_COUNT, dtype=float) * 2.0, np.zeros(DAY_COUNT)]
    )
    complete = np.ones(DAY_COUNT, dtype=bool)
    for position in incomplete:
        complete[position] = False
    return CovariateMatrix(dates, FEATURE_NAMES, values, complete, {"day_count": DAY_COUNT})


def _targets(matrix: CovariateMatrix) -> dict[date, float]:
    """One target value per day: 1000x its index, so a successor's origin is unambiguous."""
    return {day: float(index) * 1000.0 for index, day in enumerate(matrix.dates)}


def test_target_lag_feature_mask_selects_only_the_targets_own_shapes() -> None:
    mask = target_lag_feature_mask(FEATURE_NAMES, target_signal_name="wind_speed")

    assert mask == (True, True, False)


def test_eligible_analog_pool_excludes_the_whole_horizon_window_before_the_origin() -> None:
    """The leakage boundary is `analog_date < origin_date - horizon_days`, not merely `< origin_date`.

    Proven empirically: the ten days immediately preceding the origin (indices 80..89) are
    naively "before the origin" and would pass a weaker `d < origin_date` filter, but each of
    their horizon_days=10-long successor paths reaches at or past the origin, so none may appear.
    """
    matrix = _matrix()
    origin_date = matrix.dates[ORIGIN_INDEX]

    pool_values, pool_dates = eligible_analog_pool(matrix, origin_date=origin_date, horizon_days=HORIZON_DAYS)

    assert len(pool_dates) == ORIGIN_INDEX - HORIZON_DAYS
    assert max(pool_dates) == matrix.dates[ORIGIN_INDEX - HORIZON_DAYS - 1]
    excluded_zone = set(matrix.dates[ORIGIN_INDEX - HORIZON_DAYS : ORIGIN_INDEX])
    assert not (set(pool_dates) & excluded_zone)
    assert pool_values.shape[0] == len(pool_dates)


def test_eligible_analog_pool_skips_incomplete_days_even_when_time_honest() -> None:
    matrix = _matrix(incomplete=(10,))

    _values, pool_dates = eligible_analog_pool(
        matrix, origin_date=matrix.dates[ORIGIN_INDEX], horizon_days=HORIZON_DAYS
    )

    assert matrix.dates[10] not in pool_dates


def test_standardize_produces_zero_mean_unit_variance_on_the_pool_and_applies_the_same_moments_to_the_query() -> None:
    pool = np.array([[1.0], [2.0], [3.0], [4.0]])
    query = np.array([2.5])

    standardized_pool, standardized_query = _standardize(pool, query)

    assert standardized_pool.mean(axis=0) == pytest.approx([0.0], abs=1e-9)
    assert standardized_pool.std(axis=0) == pytest.approx([1.0], abs=1e-9)
    assert standardized_query == pytest.approx([(2.5 - 2.5) / np.std([1.0, 2.0, 3.0, 4.0])])


def test_standardize_guards_a_zero_variance_column_against_division_by_zero() -> None:
    pool = np.array([[5.0], [5.0], [5.0]])
    query = np.array([5.0])

    standardized_pool, standardized_query = _standardize(pool, query)

    assert np.all(np.isfinite(standardized_pool))
    assert np.all(np.isfinite(standardized_query))
    assert standardized_pool.tolist() == [[0.0], [0.0], [0.0]]


def test_run_anen_origin_refuses_an_origin_with_no_covariate_row() -> None:
    matrix = _matrix()

    with pytest.raises(OriginNotEvaluableError, match="no covariate row"):
        run_anen_origin(
            matrix,
            _targets(matrix),
            origin_date=date(2030, 1, 1),
            feature_mask=(True, True, True),
            hyperparams=AnEnHyperparameters(k_neighbors=1, horizon_days=HORIZON_DAYS),
        )


def test_run_anen_origin_refuses_an_incomplete_origin_row() -> None:
    matrix = _matrix(incomplete=(ORIGIN_INDEX,))

    with pytest.raises(OriginNotEvaluableError, match="incomplete covariate vector"):
        run_anen_origin(
            matrix,
            _targets(matrix),
            origin_date=matrix.dates[ORIGIN_INDEX],
            feature_mask=(True, True, True),
            hyperparams=AnEnHyperparameters(k_neighbors=1, horizon_days=HORIZON_DAYS),
        )


def test_run_anen_origin_never_selects_an_analog_whose_successor_would_leak_past_the_origin() -> None:
    """`run_anen_origin`'s query vector is always the origin's OWN feature row (feature 0 == 90.0
    here), so its trivially-nearest historical match by raw distance is the immediately preceding
    day (index 89, distance 1) -- exactly the "yesterday looks like today" match a temporal
    exclusion window exists to guard against, and whose 1-day successor (index 90) IS the origin
    itself. The leakage-safe pool must instead select the nearest ELIGIBLE analog (index 79,
    the closest day outside the excluded horizon-reach zone) and score from it.
    """
    matrix = _matrix()
    origin_date = matrix.dates[ORIGIN_INDEX]
    naive_nearest_index = ORIGIN_INDEX - 1  # index 89: the trivial "yesterday" match, excluded
    eligible_nearest_index = ORIGIN_INDEX - HORIZON_DAYS - 1  # index 79, the closest ELIGIBLE day

    origin_run = run_anen_origin(
        matrix,
        _targets(matrix),
        origin_date=origin_date,
        feature_mask=(True, False, False),  # feature 0 == day index, an unambiguous distance metric
        hyperparams=AnEnHyperparameters(k_neighbors=1, temporal_exclusion_days=0, horizon_days=HORIZON_DAYS),
    )

    # step 1's prediction is the single analog's own successor value (1000x its index); the
    # naive nearest day's successor would have been (naive_nearest_index + 1) * 1000.0, which is
    # the origin's own day and therefore not knowable yet at the origin.
    expected_value = float(eligible_nearest_index + 1) * 1000.0
    leaked_value = float(naive_nearest_index + 1) * 1000.0
    assert origin_run.predictions[0] == pytest.approx(expected_value)
    assert origin_run.predictions[0] != pytest.approx(leaked_value)
    assert isinstance(origin_run, AnEnOriginRun)
    assert origin_run.analog_pool_size == ORIGIN_INDEX - HORIZON_DAYS


def test_run_anen_rolling_backtest_skips_an_unscoreable_origin_rather_than_failing_the_sweep() -> None:
    matrix = _matrix()
    targets = _targets(matrix)
    # one origin far too early in the window to have any eligible analog pool at all.
    early_origin = matrix.dates[2]
    late_origin = matrix.dates[ORIGIN_INDEX]

    backtest = run_anen_rolling_backtest(
        matrix,
        targets,
        origin_dates=(early_origin, late_origin),
        feature_mask=(True, True, True),
        variant_label=FULL_VECTOR_VARIANT,
        hyperparams=AnEnHyperparameters(k_neighbors=5, horizon_days=HORIZON_DAYS),
    )

    assert len(backtest.origins) == 1
    assert backtest.origins[0].origin_date == late_origin
    assert len(backtest.skipped) == 1
    assert backtest.skipped[0].origin_date == early_origin


def test_run_anen_rolling_backtest_raises_when_no_origin_could_be_scored() -> None:
    matrix = _matrix()

    with pytest.raises(OriginNotEvaluableError, match="no rolling origin could be scored"):
        run_anen_rolling_backtest(
            matrix,
            _targets(matrix),
            origin_dates=(matrix.dates[1],),
            feature_mask=(True, True, True),
            variant_label=TARGET_LAGS_ONLY_VARIANT,
            hyperparams=AnEnHyperparameters(k_neighbors=5, horizon_days=HORIZON_DAYS),
        )


def test_model_document_records_only_the_selected_features_and_the_governed_origins() -> None:
    matrix = _matrix()
    targets = _targets(matrix)
    mask = target_lag_feature_mask(FEATURE_NAMES, target_signal_name="wind_speed")
    backtest = run_anen_rolling_backtest(
        matrix,
        targets,
        origin_dates=(matrix.dates[ORIGIN_INDEX],),
        feature_mask=mask,
        variant_label=TARGET_LAGS_ONLY_VARIANT,
        hyperparams=AnEnHyperparameters(k_neighbors=3, horizon_days=HORIZON_DAYS),
    )

    document = model_document(
        backtest,
        cell_id="cell-under-test",
        feature_checksum="f" * 64,
        feature_names=FEATURE_NAMES,
        feature_mask=mask,
    )

    origins = document["origins"]
    assert isinstance(origins, list)
    assert document["cell_id"] == "cell-under-test"
    assert document["feature_checksum"] == "f" * 64
    assert document["selected_feature_names"] == ["wind_speed_lag_1", "wind_speed_roll_mean_7"]
    assert document["variant_label"] == TARGET_LAGS_ONLY_VARIANT
    assert len(origins) == 1


def test_model_document_discriminates_two_structurally_identical_runs_over_different_cells() -> None:
    """The regression this guards: two runs sharing every structural field (hyperparameters,
    origin date, analog-pool size, scored-day count) but reading DIFFERENT governed cells must
    not collide on the document a receipt's model_version is derived from.
    """
    matrix = _matrix()
    targets = _targets(matrix)
    mask = (True, True, True)
    backtest = run_anen_rolling_backtest(
        matrix,
        targets,
        origin_dates=(matrix.dates[ORIGIN_INDEX],),
        feature_mask=mask,
        variant_label=FULL_VECTOR_VARIANT,
        hyperparams=AnEnHyperparameters(k_neighbors=3, horizon_days=HORIZON_DAYS),
    )

    first = model_document(
        backtest, cell_id="cell-a", feature_checksum="a" * 64, feature_names=FEATURE_NAMES, feature_mask=mask
    )
    second = model_document(
        backtest, cell_id="cell-b", feature_checksum="b" * 64, feature_names=FEATURE_NAMES, feature_mask=mask
    )

    assert first != second
