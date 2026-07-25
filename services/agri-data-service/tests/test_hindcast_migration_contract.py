"""Historical hindcast persistence and leakage-boundary contract checks."""

from pathlib import Path


def _migration() -> str:
    return (
        Path(__file__).parents[1] / "alembic" / "versions" / "20260722_0006_historical_hindcast_signals.py"
    ).read_text(encoding="utf-8")


def _hardening_migration() -> str:
    return (
        Path(__file__).parents[1] / "alembic" / "versions" / "20260722_0007_hindcast_calibration_guard.py"
    ).read_text(encoding="utf-8")


def _governance_migration() -> str:
    return (
        Path(__file__).parents[1] / "alembic" / "versions" / "20260722_0008_hindcast_governance_hardening.py"
    ).read_text(encoding="utf-8")


def test_hindcast_revision_is_forward_only_and_does_not_seed_outputs() -> None:
    migration = _migration()

    assert 'revision = "20260722_0006"' in migration
    assert 'down_revision = "20260722_0005"' in migration
    assert "raise NotImplementedError" in migration
    assert "DROP TABLE" not in migration
    assert "TRUNCATE" not in migration
    assert "INSERT INTO agri.forecast_hindcast" not in migration


def test_hindcasts_separate_simulated_cutoff_from_real_availability() -> None:
    migration = _migration()

    assert "simulated_cutoff_time" in migration
    assert "availability_mode IN ('as_recorded', 'retrospective_pinned_release')" in migration
    assert "recorded_at = clock_timestamp()" in migration
    assert "signal_available_at <= p_as_of_time" in migration
    assert "as-recorded hindcast inputs were not available at the simulated cutoff" in migration


def test_finalization_recomputes_sql_points_bands_actuals_and_naive_baseline() -> None:
    migration = _migration()

    assert "initial hindcast receipts support only the reviewed SQL metric baseline" in migration
    assert "agri.forecast_linear_regression(" in migration
    assert "agri.forecast_linear_residual_bands(" in migration
    assert "agri.forecast_timeseries_base(" in migration
    assert "point_value IS DISTINCT FROM expected_point_value" in migration
    assert "naive_value IS DISTINCT FROM expected_naive_value" in migration
    assert "actual_observation_checksum IS DISTINCT FROM expected_actual_observation_checksum" in migration
    assert "hindcast receipt checksum mismatch" in migration


def test_calibration_horizon_cannot_cross_the_simulated_cutoff() -> None:
    hardening = _hardening_migration()

    assert 'revision = "20260722_0007"' in hardening
    assert 'down_revision = "20260722_0006"' in hardening
    assert "uncertainty_calibration_cutoff_time + step_interval * horizon_steps" in hardening
    assert "<= simulated_cutoff_time" in hardening
    assert "raise NotImplementedError" in hardening


def test_governance_hardening_preserves_v1_and_requires_v2_for_new_receipts() -> None:
    governance = _governance_migration()

    assert 'revision = "20260722_0008"' in governance
    assert 'down_revision = "20260722_0007"' in governance
    assert "DEFAULT 'hindcast_v1'" in governance
    assert "SET DEFAULT 'hindcast_v2'" in governance
    assert "WHEN 'hindcast_v1'" in governance
    assert "WHEN 'hindcast_v2'" in governance
    assert "jsonb_build_array" in governance
    assert "new hindcast runs must begin in staging status" in governance
    assert "new hindcast runs require receipt digest version hindcast_v2" in governance
    assert "new hindcast finalizations require receipt digest version hindcast_v2" in governance
    assert "REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA agri FROM PUBLIC" in governance
    assert "raise NotImplementedError" in governance


def test_v2_digest_binds_declared_identity_horizon_cutoff_and_actual_availability() -> None:
    governance = _governance_migration()

    for required in (
        "plantgeo-forecast-hindcast-receipt-v2",
        "run.forecast_run_id::text",
        "run.series_id::text",
        "series.series_key",
        "parent.model_id::text",
        "model.model_key",
        "parent.quality_policy_id::text",
        "policy.policy_key",
        "plantgeo-forecast-quality-policy-v1",
        "policy.is_active::text",
        "policy.min_training_points::text",
        "policy.min_backtest_points::text",
        "policy.min_coverage_fraction::text",
        "policy.max_mae::text",
        "policy.max_rmse::text",
        "policy.max_mape::text",
        "policy.min_skill_score::text",
        "to_jsonb(policy.required_quantiles)",
        "run.release_set_id::text",
        "run.simulated_cutoff_time",
        "run.horizon_steps::text",
        "run.step_interval::text",
        "run.minimum_training_points::text",
        "run.training_point_count::text",
        "value.actual_data_available_at",
    ):
        assert required in governance


def test_hindcast_finalization_requires_active_policy_and_calibration_samples() -> None:
    governance = _governance_migration()

    assert "policy.is_active" in governance
    assert "policy.min_backtest_points" in governance
    assert "bands.backtest_point_count" in governance
    assert "actual_calibration_samples, 0) < policy.min_backtest_points" in governance
    assert "FOR SHARE OF policy_row" in governance
    assert "NEW.quality_passed := current_quality_passed" in governance
    assert "NEW.rmse <= policy.max_rmse" in governance
    assert "unexpected predecessor hindcast finalizer definition" in governance
    assert "target.receipt_digest_version = 'hindcast_v1'" in governance
    assert "target.receipt_digest_version = 'hindcast_v2'" in governance
    assert "SELECT bands.backtest_point_count" in governance
    assert "hindcast quality policy is inactive" in governance
    assert "quality policies referenced by finalized hindcast receipts are immutable" in governance


def test_outcome_signal_is_typed_append_only_and_not_operationally_published() -> None:
    migration = _migration()

    assert "v_forecast_hindcast_outcome" in migration
    assert "forecast_hindcast_signal_timeseries" in migration
    assert "residual_actual_minus_forecast" in migration
    assert "hindcast values are append-only" in migration
    assert "finalized hindcast runs are immutable" in migration
    assert "v_forecast_series_serving" not in migration
    assert "forecast_publication" not in migration
    assert "FROM PUBLIC" in migration
