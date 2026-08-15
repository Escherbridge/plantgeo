"""Database-free proofs for the split-conformal calibration arithmetic."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agri_data_service.method.ml.conformal_calibration import (
    ConformalCalibrationResult,
    EmptyConformalFoldError,
    ResidualObservation,
    calibrate_intervals,
    compute_conformal_margin,
)

AS_OF_TIME = datetime(2026, 8, 10, tzinfo=UTC)
BEFORE_AS_OF = "2026-08-05T00:00:00+00:00"
AFTER_AS_OF = "2026-08-15T00:00:00+00:00"  # strictly after AS_OF_TIME -- must never be admitted
SHA256_HEX_LENGTH = 64


def _observation(  # noqa: PLR0913 - one parameter per residual field is the contract
    *,
    iteration_key: str,
    origin: str = "2026-01-01T00:00:00+00:00",
    predicted_low: float,
    predicted_median: float,
    predicted_high: float,
    actual_value: float,
    recorded_at: str = BEFORE_AS_OF,
) -> ResidualObservation:
    return ResidualObservation(
        iteration_key=iteration_key,
        origin_cutoff_time=origin,
        target_date="2026-01-15T00:00:00+00:00",
        predicted_low=predicted_low,
        predicted_median=predicted_median,
        predicted_high=predicted_high,
        actual_value=actual_value,
        recorded_at=recorded_at,
    )


def test_residual_observation_derives_residual_and_nominal_coverage() -> None:
    covered = _observation(
        iteration_key="i1", predicted_low=9.0, predicted_median=10.0, predicted_high=11.0, actual_value=10.4
    )
    uncovered = _observation(
        iteration_key="i2", predicted_low=9.0, predicted_median=10.0, predicted_high=11.0, actual_value=12.0
    )

    assert covered.residual == pytest.approx(0.4)
    assert covered.absolute_residual == pytest.approx(0.4)
    assert covered.nominal_interval_covered is True
    assert uncovered.nominal_interval_covered is False


def test_compute_conformal_margin_matches_the_finite_sample_quantile_formula() -> None:
    # abs residuals [0.0, 0.5, 0.5, 0.2]; n=4, alpha=0.2 -> level = ceil(5*0.8)/4 = 1.0 -> the max.
    margin = compute_conformal_margin([0.0, 0.5, -0.5, 0.2], alpha=0.2)

    assert margin == pytest.approx(0.5)


def test_compute_conformal_margin_is_zero_over_no_residuals() -> None:
    assert compute_conformal_margin([], alpha=0.2) == 0.0


def test_compute_conformal_margin_refuses_an_alpha_outside_the_open_unit_interval() -> None:
    with pytest.raises(ValueError, match="alpha must be strictly between"):
        compute_conformal_margin([0.1], alpha=0.0)
    with pytest.raises(ValueError, match="alpha must be strictly between"):
        compute_conformal_margin([0.1], alpha=1.0)


def _calibration_fold() -> list[ResidualObservation]:
    """Four calibration residuals whose abs values are [0.0, 0.5, 0.5, 0.2] -> margin 0.5 at 80%."""
    medians_and_actuals = [(10.0, 10.0), (10.0, 10.5), (10.0, 9.5), (10.0, 10.2)]
    return [
        _observation(
            iteration_key=f"cal-{index}",
            predicted_low=9.9,
            predicted_median=median,
            predicted_high=10.1,
            actual_value=actual,
        )
        for index, (median, actual) in enumerate(medians_and_actuals)
    ]


def test_calibrate_intervals_scores_before_and_after_coverage_on_the_disjoint_held_out_fold() -> None:
    held_out = [
        # nominal band [9.9, 10.05] misses; |residual|=0.3 <= margin 0.5 -> covered after.
        _observation(
            iteration_key="held-1", predicted_low=9.9, predicted_median=10.0, predicted_high=10.05, actual_value=10.3
        ),
        # nominal band [9.8, 10.2] misses; |residual|=0.6 > margin 0.5 -> still uncovered after.
        _observation(
            iteration_key="held-2", predicted_low=9.8, predicted_median=10.0, predicted_high=10.2, actual_value=10.6
        ),
    ]

    result = calibrate_intervals(_calibration_fold(), held_out, as_of_time=AS_OF_TIME, nominal_coverage=0.80)

    assert isinstance(result, ConformalCalibrationResult)
    assert result.conformal_margin == pytest.approx(0.5)
    assert result.calibration_sample_count == len(_calibration_fold())
    assert result.held_out_sample_count == len(held_out)
    assert result.before_coverage == pytest.approx(0.0)
    assert result.after_coverage == pytest.approx(0.5)
    assert len(result.digest) == SHA256_HEX_LENGTH
    assert set(result.digest) <= set("0123456789abcdef")


def test_calibrate_intervals_availability_gate_drops_a_residual_recorded_after_as_of_time() -> None:
    late = _observation(
        iteration_key="late",
        predicted_low=9.0,
        predicted_median=10.0,
        predicted_high=11.0,
        actual_value=10.0,
        recorded_at=AFTER_AS_OF,
    )
    calibration = [*_calibration_fold(), late]
    held_out = [
        _observation(
            iteration_key="held-1", predicted_low=9.0, predicted_median=10.0, predicted_high=11.0, actual_value=10.4
        )
    ]

    result = calibrate_intervals(calibration, held_out, as_of_time=AS_OF_TIME, nominal_coverage=0.80)

    # the late row must never enter the fold, so the sample count is unchanged by adding it
    assert result.calibration_sample_count == len(_calibration_fold())


def test_calibrate_intervals_refuses_an_empty_held_out_fold_rather_than_fabricating_zero_coverage() -> None:
    """Inverts the old pinning test: a 0.0 before/after coverage over zero held-out rows is not a
    measurement, it is indistinguishable from a real (if poor) result, so this must refuse loudly
    rather than return a signed digest over nothing.
    """
    with pytest.raises(EmptyConformalFoldError, match="held-out fold is empty"):
        calibrate_intervals(_calibration_fold(), [], as_of_time=AS_OF_TIME, nominal_coverage=0.80)


def test_calibrate_intervals_refuses_an_empty_calibration_fold_rather_than_fabricating_a_zero_margin() -> None:
    held_out = [
        _observation(
            iteration_key="held-1", predicted_low=9.0, predicted_median=10.0, predicted_high=11.0, actual_value=10.4
        )
    ]

    with pytest.raises(EmptyConformalFoldError, match="calibration fold is empty"):
        calibrate_intervals([], held_out, as_of_time=AS_OF_TIME, nominal_coverage=0.80)


def test_calibrate_intervals_refuses_a_calibration_fold_emptied_by_the_availability_gate() -> None:
    """A fold that is non-empty BEFORE gating but empty AFTER it must still refuse -- the gate, not
    the raw row count, is what `EmptyConformalFoldError` is protecting.
    """
    late_only = [
        _observation(
            iteration_key="late",
            predicted_low=9.0,
            predicted_median=10.0,
            predicted_high=11.0,
            actual_value=10.0,
            recorded_at=AFTER_AS_OF,
        )
    ]
    held_out = [
        _observation(
            iteration_key="held-1", predicted_low=9.0, predicted_median=10.0, predicted_high=11.0, actual_value=10.4
        )
    ]

    with pytest.raises(EmptyConformalFoldError, match="calibration fold is empty"):
        calibrate_intervals(late_only, held_out, as_of_time=AS_OF_TIME, nominal_coverage=0.80)


def test_calibrate_intervals_margin_depends_only_on_the_calibration_fold() -> None:
    held_out_a = [
        _observation(
            iteration_key="a", predicted_low=0.0, predicted_median=10.0, predicted_high=20.0, actual_value=10.0
        )
    ]
    held_out_b = [
        _observation(
            iteration_key="b1", predicted_low=0.0, predicted_median=10.0, predicted_high=20.0, actual_value=10.0
        ),
        _observation(
            iteration_key="b2", predicted_low=0.0, predicted_median=10.0, predicted_high=20.0, actual_value=50.0
        ),
    ]

    result_a = calibrate_intervals(_calibration_fold(), held_out_a, as_of_time=AS_OF_TIME)
    result_b = calibrate_intervals(_calibration_fold(), held_out_b, as_of_time=AS_OF_TIME)

    assert result_a.conformal_margin == result_b.conformal_margin == pytest.approx(0.5)


def test_calibrate_intervals_refuses_a_nominal_coverage_outside_the_open_unit_interval() -> None:
    with pytest.raises(ValueError, match="nominal_coverage must be strictly between"):
        calibrate_intervals([], [], as_of_time=AS_OF_TIME, nominal_coverage=1.0)
