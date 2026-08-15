"""Database-free proofs for the recalibration origin split.

The SQL residual read (`select_forecast_iteration_residuals.sql`) is proven end-to-end by the
real read-only run against prod NDVI data recorded in the track's evidence report, rather than
by a synthetic disposable-database fixture here -- see
`conductor/tracks/ml_recommendation_models_20260808/` for the real numbers and receipt.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agri_data_service.execution.conformal_recalibration import RecalibrationSplit, split_by_origin
from agri_data_service.method.ml.conformal_calibration import ResidualObservation


def _observation(*, iteration_key: str, cutoff: str) -> ResidualObservation:
    return ResidualObservation(
        iteration_key=iteration_key,
        origin_cutoff_time=cutoff,
        target_date="2026-06-01T00:00:00+00:00",
        predicted_low=0.0,
        predicted_median=1.0,
        predicted_high=2.0,
        actual_value=1.0,
        recorded_at="2026-06-15T00:00:00+00:00",
    )


def test_recalibration_split_refuses_an_inverted_boundary() -> None:
    with pytest.raises(ValueError, match="must not precede"):
        RecalibrationSplit(
            calibration_cutoff_before=datetime(2026, 6, 1, tzinfo=UTC),
            held_out_cutoff_at_or_after=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_split_by_origin_keeps_every_horizon_step_of_one_iteration_in_the_same_fold() -> None:
    """Two rows sharing one origin (two horizon steps of the same iteration) must land together."""
    split = RecalibrationSplit(
        calibration_cutoff_before=datetime(2026, 5, 1, tzinfo=UTC),
        held_out_cutoff_at_or_after=datetime(2026, 5, 1, tzinfo=UTC),
    )
    early_a = _observation(iteration_key="early-a", cutoff="2026-01-01T00:00:00+00:00")
    early_b = _observation(iteration_key="early-b", cutoff="2026-01-01T00:00:00+00:00")
    late = _observation(iteration_key="late", cutoff="2026-07-01T00:00:00+00:00")

    calibration, held_out = split_by_origin((early_a, early_b, late), split)

    assert calibration == (early_a, early_b)
    assert held_out == (late,)


def test_split_by_origin_drops_a_row_whose_origin_falls_in_a_deliberate_buffer_gap() -> None:
    split = RecalibrationSplit(
        calibration_cutoff_before=datetime(2026, 3, 1, tzinfo=UTC),
        held_out_cutoff_at_or_after=datetime(2026, 5, 1, tzinfo=UTC),
    )
    mid_gap = _observation(iteration_key="mid", cutoff="2026-04-01T00:00:00+00:00")

    calibration, held_out = split_by_origin((mid_gap,), split)

    assert calibration == ()
    assert held_out == ()


def test_split_by_origin_boundary_is_exclusive_before_and_inclusive_at_or_after() -> None:
    split = RecalibrationSplit(
        calibration_cutoff_before=datetime(2026, 5, 1, tzinfo=UTC),
        held_out_cutoff_at_or_after=datetime(2026, 5, 1, tzinfo=UTC),
    )
    exactly_on_boundary = _observation(iteration_key="boundary", cutoff="2026-05-01T00:00:00+00:00")

    calibration, held_out = split_by_origin((exactly_on_boundary,), split)

    assert calibration == ()
    assert held_out == (exactly_on_boundary,)
