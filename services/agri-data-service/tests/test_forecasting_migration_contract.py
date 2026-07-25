"""Forecasting schema, SQL safety, and no-fabrication contract checks."""

from pathlib import Path


def _migration() -> str:
    return (
        Path(__file__).parents[1] / "alembic" / "versions" / "20260722_0005_sql_forecasting_framework.py"
    ).read_text(encoding="utf-8")


def test_forecasting_revision_is_forward_only_and_additive() -> None:
    migration = _migration()

    assert 'revision = "20260722_0005"' in migration
    assert 'down_revision = "20260720_0004"' in migration
    assert "raise NotImplementedError" in migration
    assert "DROP TABLE" not in migration
    assert "TRUNCATE" not in migration
    assert "INSERT INTO agri.forecast_value" not in migration
    assert "INSERT INTO agri.forecast_receipt" not in migration
    assert "hot_projection" not in migration


def test_normalized_series_preserve_source_variant_state_and_support() -> None:
    migration = _migration()

    for required in (
        "source_variant_key",
        "input_adapter",
        "data_source_id",
        "source_transform_version",
        "forecast_entity_state",
        "state_checksum",
        "representation_kind",
        "spatial_support_kind",
        "source_spatial_resolution_m",
        "output_spatial_resolution_m",
        "source_temporal_support",
        "output_temporal_support",
        "aggregation_method",
    ):
        assert required in migration

    assert "v_signal_timeseries_contract(p_as_of_time, p_release_set_id)" in migration
    assert "series.input_adapter = 'signal_observation'" in migration
    assert "series.input_adapter = 'forecast_observation'" in migration
    assert "release_set.state IN ('validated', 'published')" in migration
    assert "source_release.validation_state = 'valid'" in migration


def test_sql_baseline_uses_elapsed_time_and_quality_gates() -> None:
    migration = _migration()

    for function_name in (
        "forecast_percentile",
        "forecast_normalized_series",
        "forecast_rolling_stats",
        "forecast_linear_regression",
        "forecast_linear_backtest",
        "forecast_linear_residual_bands",
        "validate_forecast_run",
    ):
        assert f"FUNCTION agri.{function_name}" in migration

    assert "regr_slope(y, x)" in migration
    assert "regr_intercept(y, x)" in migration
    assert "extract(epoch FROM base.observed_at)" in migration
    assert "date_bin" not in migration  # fixed buckets are generated explicitly, including missing buckets
    assert "percentile_cont(0.1) WITHIN GROUP (ORDER BY residuals.residual)" in migration
    assert "min_training_points >= 3" in migration
    assert "min_backtest_points > 0" in migration
    assert "coverage_fraction >= policy.min_coverage_fraction" in migration
    assert "metric.skill_score >= policy.min_skill_score" in migration
    assert "forecast backtest quality gates did not pass" in migration


def test_ml_execution_and_publication_require_validated_lineage() -> None:
    migration = _migration()

    for checksum in (
        "input_release_checksum",
        "feature_code_checksum",
        "feature_checksum",
        "training_code_checksum",
        "model_checksum",
        "validation_checksum",
        "receipt_checksum",
        "manifest_checksum",
    ):
        assert checksum in migration

    assert "execution_mode = 'local'" in migration
    assert "local training validation output lineage mismatch" in migration
    assert "local training model artifact checksum mismatch" in migration
    assert "ML execution remains gated without a validated local training run" in migration
    assert "strategy-selection models require a separate reviewed selection contract" in migration
    assert "forecast receipt requires a validated forecast run" in migration
    assert "forecast receipt series has no passing backtest evidence" in migration
    assert "forecast publication requires finalized receipts" in migration
    assert "mismatched_release_count" in migration
    assert "guard_forecast_immutable_rows" in migration
    assert "require_initial_forecast_state" in migration
    assert "verify_forecast_validated_transition" in migration
    assert "verify_forecast_receipt_finalization" in migration
    assert "verify_forecast_publication_transition" in migration
    assert "FOR SHARE" in migration
    assert "forecast_value_write_guard" in migration


def test_serving_contract_distinguishes_points_from_preaggregates() -> None:
    migration = _migration()

    assert "v_forecast_series_serving" in migration
    assert "mv_forecast_ml_daily_serving" in migration
    assert "allow_ml_daily_aggregate" in migration
    assert "'forecast_point'::text AS serving_representation" in migration
    assert "'preaggregated_forecast'::text AS serving_representation" in migration
    assert "source_representation_kind" in migration
    assert "source_spatial_support_kind" in migration
    assert "contributing_forecast_points" in migration
    assert "WITH NO DATA" in migration
    assert "forecast_quantiles_valid" in migration
    assert "lower.quantile < upper.quantile" in migration


def test_strategy_selection_is_documented_as_gated_feasibility_work() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    design = (repository_root / "docs" / "sql-forecasting-framework.md").read_text(encoding="utf-8")
    migration = _migration()

    assert "model_purpose" in migration
    assert "strategy_selection" in migration
    assert "feasibility_candidate" in design
    assert "not causal effect estimates" in design
    assert "focused requirements interview" in design
