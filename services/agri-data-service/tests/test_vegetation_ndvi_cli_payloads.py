"""Contract tests for the vegetation NDVI CLI report payloads and as-of guard."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

import pytest

from agri_data_service.execution.vegetation_ndvi_plane import ErrorMetrics, HoldoutEvaluation
from agri_data_service.interface.cli.commands import (
    VEGETATION_HORIZON_BUCKETS,
    _error_metrics_payload,
    _holdout_payload,
    _resolved_as_of_time,
    _skill_score,
)

_CUTOFF_DAY = date(2026, 7, 1)


def _metrics(label: str, rmse: float) -> ErrorMetrics:
    return ErrorMetrics(
        label=label,
        point_count=100,
        mean_absolute_error=rmse * 0.8,
        root_mean_squared_error=rmse,
        bias=0.001,
    )


def _evaluation() -> HoldoutEvaluation:
    return HoldoutEvaluation(
        cutoff_days=(_CUTOFF_DAY,),
        iteration_count=24,
        reconciled_actual_count=100,
        interval_coverage_fraction=0.66,
        method_metrics=_metrics("ndvi_seasonal_anomaly_bootstrap_v1", 0.045),
        persistence_metrics=_metrics("persistence_last_observed", 0.060),
        climatology_metrics=_metrics("seasonal_naive_climatology", 0.046),
        metrics_by_horizon_bucket=tuple(
            (name, _metrics(name, 0.045)) for name, _lower, _upper in VEGETATION_HORIZON_BUCKETS
        ),
    )


def test_holdout_payload_keeps_the_method_metrics_separate_from_the_method_name() -> None:
    payload = _holdout_payload(_evaluation())
    payload.update({"method": "ndvi_seasonal_anomaly_bootstrap_v1"})
    assert payload["method_metrics"]["rmse"] == pytest.approx(0.045)
    assert payload["baseline_persistence_metrics"]["rmse"] == pytest.approx(0.060)
    assert payload["baseline_climatology_metrics"]["rmse"] == pytest.approx(0.046)
    assert payload["method"] == "ndvi_seasonal_anomaly_bootstrap_v1"


def test_skill_score_reports_both_baselines_and_refuses_a_degenerate_one() -> None:
    evaluation = _evaluation()
    assert _skill_score(evaluation.method_metrics, evaluation.persistence_metrics) == pytest.approx(0.25)
    assert _skill_score(evaluation.method_metrics, evaluation.climatology_metrics) == pytest.approx(0.021739, abs=1e-6)
    degenerate = ErrorMetrics(
        label="degenerate",
        point_count=10,
        mean_absolute_error=0.0,
        root_mean_squared_error=0.0,
        bias=0.0,
    )
    assert _skill_score(evaluation.method_metrics, degenerate) is None


def test_error_metrics_payload_reports_no_number_for_an_empty_evaluation_set() -> None:
    empty = ErrorMetrics(
        label="empty",
        point_count=0,
        mean_absolute_error=math.nan,
        root_mean_squared_error=math.nan,
        bias=math.nan,
    )
    payload = _error_metrics_payload(empty)
    assert payload["point_count"] == 0
    assert payload["mae"] is None
    assert payload["rmse"] is None


def test_resolved_as_of_time_defaults_to_now_and_refuses_dishonest_boundaries() -> None:
    resolved = _resolved_as_of_time(None, _CUTOFF_DAY)
    assert resolved.tzinfo is not None
    assert resolved <= datetime.now(tz=UTC)

    pinned = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
    assert _resolved_as_of_time(pinned, _CUTOFF_DAY) == pinned

    with pytest.raises(ValueError, match="cannot be in the future"):
        _resolved_as_of_time(datetime.now(tz=UTC) + timedelta(days=1), _CUTOFF_DAY)

    with pytest.raises(ValueError, match="cannot precede the cutoff day"):
        _resolved_as_of_time(datetime(2026, 6, 30, 0, 0, tzinfo=UTC), _CUTOFF_DAY)
