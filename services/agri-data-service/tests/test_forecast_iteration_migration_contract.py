"""Static contract checks for the generic forecast iteration revision."""

from pathlib import Path


def _migration() -> str:
    return (
        Path(__file__).parents[1] / "alembic" / "versions" / "20260723_0010_forecast_iteration_pipeline.py"
    ).read_text(encoding="utf-8")


def test_iteration_revision_is_additive_forward_only_and_not_publishable() -> None:
    migration = _migration()

    assert 'revision = "20260723_0010"' in migration
    assert 'down_revision = "20260723_0009"' in migration
    assert "raise NotImplementedError" in migration
    assert "DROP TABLE" not in migration
    assert "DROP CONSTRAINT" not in migration
    assert "ALTER TABLE agri.forecast_publication" not in migration
    assert "ALTER TABLE agri.forecast_receipt" not in migration
    assert "INSERT INTO agri.forecast_publication" not in migration
    assert "forecast_publication_id" not in migration
    assert "strategy_selection" not in migration
    assert "recommendation" not in migration


def test_timeseries_contract_carries_governance_support_and_daily_alignment() -> None:
    migration = _migration()

    for required in (
        "CREATE VIEW agri.v_forecast_timeseries_contract",
        "CREATE FUNCTION agri.forecast_timeseries_contract",
        "data_source_key",
        "data_source_name",
        "data_source_owner",
        "data_source_review_state",
        "license_name",
        "license_url",
        "citation",
        "source_variant_key",
        "representation_kind",
        "spatial_support_kind",
        "source_spatial_resolution_m",
        "output_spatial_resolution_m",
        "source_temporal_support",
        "desired_temporal_grain",
        "contract_checksum",
        "contract_snapshot",
        "source_release_license_snapshot",
        "forecast_timeseries_base(p_release_set_id, p_as_of_time)",
    ):
        assert required in migration

    assert "CREATE FUNCTION agri.forecast_date_spine" in migration
    assert "CREATE TABLE agri.forecast_input_recorded_at" in migration
    assert "CREATE FUNCTION agri.record_forecast_input_change" in migration
    assert "CREATE FUNCTION agri.record_forecast_release_content_insert" in migration
    assert "REFERENCING NEW TABLE AS new_rows" in migration
    assert "SECURITY DEFINER" in migration
    assert "source_release_record.recorded_at <= p_as_of_time" in migration
    assert "contract.data_source_recorded_at::timestamptz <= p_as_of_time" in migration
    assert "REVOKE INSERT, UPDATE, DELETE, TRUNCATE" in migration
    assert "supports exactly one-day UTC grain" in migration
    assert "CREATE FUNCTION agri.forecast_aligned_daily_series" in migration
    assert "p_gap_policy NOT IN ('strict', 'locf')" in migration
    assert "previous.bucket_start = daily.bucket_start - interval '1 day'" in migration
    assert "refuses to fabricate daily precision" in migration
    assert "source_support > interval '1 day'" in migration
    assert "requires explicit mean aggregation for subdaily sources" in migration
    assert "governed source support conflicts" in migration
    assert "SELECT DISTINCT source_release_id FROM new_rows" in migration
    assert "SELECT DISTINCT release_set_id FROM new_rows" in migration
    assert "SELECT 'forecast_observation', id::text" not in migration
    assert "SELECT 'signal_observation', id::text" not in migration
    assert "contract.contract_recorded_at::timestamptz <= p_as_of_time" in migration
    assert "source_release_ids" in migration
    assert "observation_checksums" in migration


def test_bootstrap_is_deterministic_cutoff_bound_and_checksum_governed() -> None:
    migration = _migration()

    assert "CREATE FUNCTION agri.forecast_daily_bootstrap" in migration
    assert "p_horizon_days integer DEFAULT 30" in migration
    assert "p_simulation_count integer DEFAULT 1000" in migration
    assert "p_simulation_count > 10000" in migration
    assert "forecast_bootstrap_sample_v1" in migration
    assert "digest(" in migration
    assert "::bit(60)::bigint" in migration
    assert "random()" not in migration.lower()
    assert "contract.observed_at < p_cutoff_time + interval '1 day'" in migration
    assert "bucket_start = previous_bucket + interval '1 day'" in migration
    assert "percentile_cont(0.1)" in migration
    assert "percentile_cont(0.5)" in migration
    assert "percentile_cont(0.9)" in migration
    assert "forecast_daily_bootstrap_v1" in migration
    assert "'daily_mean'" in migration
    assert "'p10_p50_p90'" in migration
    assert "aligned_history_checksum" in migration
    assert "history_checksum varchar(64) NOT NULL" in migration


def test_iteration_and_actual_planes_are_terminally_immutable_and_signal_ready() -> None:
    migration = _migration()

    for relation in (
        "forecast_iteration",
        "forecast_iteration_value",
        "forecast_iteration_actual",
        "forecast_iteration_actual_input",
    ):
        assert f"CREATE TABLE agri.{relation}" in migration

    assert "CREATE PROCEDURE agri.materialize_forecast_iteration" in migration
    assert "INOUT p_iteration_id uuid" in migration
    assert "pg_advisory_xact_lock" in migration
    assert "forecast iteration as-of boundary cannot be in the future" in migration
    assert "CREATE PROCEDURE agri.reconcile_forecast_iteration_actuals" in migration
    assert "INOUT p_inserted_count integer" in migration
    assert "IN p_actual_release_set_id uuid" in migration
    assert "actual_input_release_checksum" in migration
    assert "actual_digest_version varchar(40) NOT NULL" in migration
    assert "'forecast_iteration_actual_v2'" in migration
    assert "license_snapshot text NOT NULL" in migration
    assert "target.actual_release_set_id::text" in migration
    assert "p_actual_release_set_id::text" in migration
    assert "actual reconciliation as-of boundary cannot be in the future" in migration
    assert "value.valid_time + interval '1 day' <= p_as_of_time" in migration
    assert "count(DISTINCT contract.observed_at)" in migration
    assert "purpose varchar(32) NOT NULL DEFAULT 'evaluation_only'" in migration
    assert "'as_of_pinned_release'" in migration
    assert "'retrospective_pinned_release'" in migration
    assert "candidates.actual_id::text" not in migration
    assert "forecast iteration key already has different immutable parameters" in migration
    assert "existing.history_checksum <> computed_history_checksum" in migration
    assert "input_license_snapshots jsonb NOT NULL" in migration
    assert "contract_snapshot jsonb NOT NULL" in migration
    assert "contract.data_source_review_state = 'approved'" in migration
    assert "source-release license snapshots do not match the approved contract" in migration
    assert "btrim(coalesce(contract.license_url, '')) <> ''" in migration
    assert "contract.source_release_license_snapshot" in migration
    assert "SET intervalstyle = 'postgres'" in migration
    assert "SET extra_float_digits = 1" in migration
    assert "finalized forecast iterations are immutable" in migration
    assert "forecast iteration actual evidence is append-only" in migration
    assert "DEFERRABLE INITIALLY DEFERRED" in migration
    assert "forecast iteration actual lineage does not match governed inputs" in migration
    receipt_body = migration.split(
        "'forecast_iteration_receipt_v1'",
        maxsplit=1,
    )[1].split("FROM agri.forecast_iteration AS iteration", maxsplit=1)[0]
    assert "iteration.id::text" not in receipt_body
    assert "iteration.iteration_key" in receipt_body
    assert "CREATE VIEW agri.v_forecast_iteration_outcome" in migration
    assert "CREATE FUNCTION agri.forecast_iteration_signal_timeseries" in migration
    for signal_kind in (
        "forecast_low",
        "forecast_median",
        "forecast_high",
        "actual",
        "residual_actual_minus_forecast",
        "forecast_error",
        "absolute_error",
        "squared_error",
        "interval_covered",
    ):
        assert f"'{signal_kind}'" in migration
