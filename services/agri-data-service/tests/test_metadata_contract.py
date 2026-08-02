"""Schema and durability invariants that do not require a database."""

from sqlalchemy import CheckConstraint, Enum, ForeignKeyConstraint, UniqueConstraint

from agri_data_service import models as model_registry
from agri_data_service.db.base import AGRI_SCHEMA, Base


def _check_sql(table_name: str) -> set[str]:
    table = Base.metadata.tables[f"{AGRI_SCHEMA}.{table_name}"]
    return {str(constraint.sqltext) for constraint in table.constraints if isinstance(constraint, CheckConstraint)}


def test_every_registered_table_is_schema_qualified() -> None:
    assert model_registry.JobWorkItem.__table__.schema == AGRI_SCHEMA
    assert Base.metadata.tables
    assert all(table.schema == AGRI_SCHEMA for table in Base.metadata.tables.values())


def test_every_foreign_key_targets_the_agri_schema() -> None:
    targets = {
        foreign_key.target_fullname for table in Base.metadata.tables.values() for foreign_key in table.foreign_keys
    }
    assert targets
    assert all(target.startswith(f"{AGRI_SCHEMA}.") for target in targets)


def test_lineage_and_execution_tables_are_in_the_registry() -> None:
    expected = {
        "artifact",
        "data_source",
        "release_set",
        "release_set_item",
        "source_release",
        "job_definition",
        "job_run",
        "job_dependency",
        "job_work_item",
        "job_attempt",
        "job_checkpoint",
        "job_output",
        "publication_pointer",
        "job_outbox",
        "job_event",
        "job_incident",
    }
    actual = {table.name for table in Base.metadata.tables.values()}
    assert expected <= actual


def test_historical_observation_plane_is_release_pinned_and_coverage_audited() -> None:
    expected = {
        "spatial_cell",
        "cell_source_crosswalk",
        "signal_observation",
        "signal_coverage_audit",
        "source_coverage_audit",
        "drought_polygon_snapshot",
        "historical_promotion_bundle",
        "historical_promotion_chunk_receipt",
        "historical_promotion_artifact_receipt",
        "historical_promotion_data_source_receipt",
        "historical_promotion_source_release_receipt",
    }
    actual = {table.name for table in Base.metadata.tables.values()}
    assert expected <= actual

    observation_targets = {
        foreign_key.target_fullname for foreign_key in Base.metadata.tables["agri.signal_observation"].foreign_keys
    }
    assert {"agri.source_release.id", "agri.spatial_cell.id"} <= observation_targets
    assert "source_parameter" in Base.metadata.tables["agri.signal_observation"].c
    assert "source_parameter" in Base.metadata.tables["agri.signal_coverage_audit"].c
    assert "transform_version" in Base.metadata.tables["agri.source_release"].c
    assert any(
        "received_observation_count <= expected_observation_count" in check
        for check in _check_sql("signal_coverage_audit")
    )
    assert any("status = 'complete'" in check for check in _check_sql("signal_coverage_audit"))
    assert any(
        "received_feature_count <= expected_feature_count" in check for check in _check_sql("source_coverage_audit")
    )
    assert any("status = 'complete'" in check for check in _check_sql("source_coverage_audit"))
    assert Base.metadata.tables["agri.drought_polygon_snapshot"].c.impact_type.nullable is False


def test_historical_promotion_receipts_are_release_set_bound_and_resumable() -> None:
    bundle = Base.metadata.tables["agri.historical_promotion_bundle"]
    receipt = Base.metadata.tables["agri.historical_promotion_chunk_receipt"]
    artifact = Base.metadata.tables["agri.historical_promotion_artifact_receipt"]
    data_source = Base.metadata.tables["agri.historical_promotion_data_source_receipt"]
    source_release = Base.metadata.tables["agri.historical_promotion_source_release_receipt"]

    assert bundle.c.manifest_checksum.unique
    assert {"release_set_id", "expected_chunk_count", "expected_record_count"} <= set(bundle.c.keys())
    assert {"bundle_id", "sequence", "payload_sha256"} <= set(receipt.c.keys())
    assert {"bundle_id", "source_release_id", "artifact_id", "checksum_sha256"} <= set(artifact.c.keys())
    assert {"bundle_id", "source_key"} <= set(data_source.c.keys())
    assert {"bundle_id", "source_release_id"} <= set(source_release.c.keys())


def test_crosswalks_retain_immutable_spatial_support_for_serving() -> None:
    crosswalk = Base.metadata.tables["agri.cell_source_crosswalk"]
    assert {"native_resolution_m", "spatial_support_kind", "mapping_method", "coverage_fraction"} <= set(
        crosswalk.c.keys()
    )
    assert any(
        "spatial_support_kind IN" in str(constraint.sqltext)
        for constraint in crosswalk.constraints
        if isinstance(constraint, CheckConstraint)
    )


def test_forecasting_plane_is_release_job_and_source_variant_bound() -> None:
    expected = {
        "forecast_series",
        "forecast_entity_state",
        "forecast_observation",
        "forecast_feature_snapshot",
        "forecast_model",
        "forecast_training_run",
        "forecast_quality_policy",
        "forecast_run",
        "forecast_backtest_metric",
        "forecast_receipt",
        "forecast_value",
        "forecast_publication",
        "forecast_publication_item",
        "forecast_hindcast_run",
        "forecast_hindcast_value",
    }
    assert expected <= {table.name for table in Base.metadata.tables.values()}

    series = Base.metadata.tables["agri.forecast_series"]
    assert {
        "source_variant_key",
        "input_adapter",
        "data_source_id",
        "source_transform_version",
        "representation_kind",
        "spatial_support_kind",
        "source_temporal_support",
        "output_temporal_support",
    } <= set(series.c.keys())
    assert "agri.data_source.id" in {key.target_fullname for key in series.foreign_keys}

    snapshot = Base.metadata.tables["agri.forecast_feature_snapshot"]
    assert {"release_set_id", "job_run_id", "input_release_checksum", "feature_checksum"} <= set(snapshot.c.keys())
    receipt = Base.metadata.tables["agri.forecast_receipt"]
    assert {"forecast_run_id", "job_output_id", "receipt_checksum", "quantile_levels"} <= set(receipt.c.keys())
    assert "agri.job_output.id" in {key.target_fullname for key in receipt.foreign_keys}
    hindcast = Base.metadata.tables["agri.forecast_hindcast_run"]
    assert {
        "forecast_run_id",
        "series_id",
        "release_set_id",
        "simulated_cutoff_time",
        "receipt_digest_version",
        "receipt_checksum",
        "actual_knowledge_as_of",
    } <= set(hindcast.c.keys())
    assert str(hindcast.c.receipt_digest_version.server_default.arg) == "'hindcast_v3'"


def test_forecast_ml_and_preaggregate_metadata_remain_explicitly_gated() -> None:
    training = Base.metadata.tables["agri.forecast_training_run"]
    assert str(training.c.execution_mode.server_default.arg) == "'local'"
    assert str(training.c.status.server_default.arg) == "'gated'"
    assert {"job_output_id", "input_release_checksum", "feature_checksum", "training_code_checksum"} <= set(
        training.c.keys()
    )

    model = Base.metadata.tables["agri.forecast_model"]
    assert "model_purpose" in model.c
    assert any("strategy_selection" in check for check in _check_sql("forecast_model"))

    series = Base.metadata.tables["agri.forecast_series"]
    assert "allow_ml_daily_aggregate" in series.c
    assert any("representation_kind" in check for check in _check_sql("forecast_series"))
    assert any("aggregation_method" in check for check in _check_sql("forecast_series"))

    publication = Base.metadata.tables["agri.forecast_publication"]
    assert {"job_output_id", "release_set_id", "scope_key"} <= set(publication.c.keys())


def test_strategy_selection_metadata_binds_labels_models_and_abstention() -> None:
    expected = {
        "strategy_outcome_definition",
        "strategy_label_release",
        "strategy_label_episode",
        "strategy_selection_policy",
        "strategy_selection_receipt",
        "strategy_selection_candidate",
    }
    actual = {table.name for table in Base.metadata.tables.values()}
    assert expected <= actual

    feature = Base.metadata.tables["agri.forecast_feature_snapshot"]
    training = Base.metadata.tables["agri.forecast_training_run"]
    assert "job_output_id" in feature.c
    assert "agri.job_output.id" in {key.target_fullname for key in feature.c.job_output_id.foreign_keys}
    assert "strategy_label_release_id" in training.c
    assert "strategy_label_checksum" in training.c
    assert "agri.strategy_label_release.id" in {
        key.target_fullname for key in training.c.strategy_label_release_id.foreign_keys
    }

    episode = Base.metadata.tables["agri.strategy_label_episode"]
    assert {
        "label_release_id",
        "analysis_subject_id",
        "strategy_id",
        "cohort_key",
        "assigned_at",
        "baseline_evidence_input_id",
        "outcome_evidence_input_id",
        "spatial_block_key",
        "covariate_snapshot",
        "covariate_checksum",
        "covariates_available_at",
        "episode_checksum",
    } <= set(episode.c.keys())
    episode_checks = _check_sql("strategy_label_episode")
    assert any("arm_kind = 'treatment'" in check and "arm_kind = 'control'" in check for check in episode_checks)

    receipt = Base.metadata.tables["agri.strategy_selection_receipt"]
    assert {
        "forecast_receipt_id",
        "forecast_iteration_id",
        "feature_snapshot_id",
        "training_run_id",
        "selection_policy_id",
        "claim_tier",
        "decision_state",
        "abstention_reason",
    } <= set(receipt.c.keys())
    receipt_checks = _check_sql("strategy_selection_receipt")
    assert any("decision_state = 'abstained'" in check for check in receipt_checks)
    assert any("effect_candidate" in check and "publishable" in check for check in receipt_checks)

    policy = Base.metadata.tables["agri.strategy_selection_policy"]
    assert {
        "min_treated_per_strategy",
        "min_control_count",
        "min_spatial_blocks",
        "min_effective_sample_size",
        "min_overlap_score",
        "max_weighted_smd",
        "min_coverage_fraction",
        "min_conservative_value_gain",
        "max_model_disagreement",
        "max_ood_score",
        "allow_effect_claims",
    } <= set(policy.c.keys())

    outcome = Base.metadata.tables["agri.strategy_outcome_definition"]
    assert "smallest_meaningful_effect" in outcome.c
    label = Base.metadata.tables["agri.strategy_label_release"]
    assert {"feature_schema", "feature_schema_checksum"} <= set(label.c.keys())


def test_logical_runs_and_shards_have_idempotency_keys() -> None:
    run = Base.metadata.tables["agri.job_run"]
    shard = Base.metadata.tables["agri.job_work_item"]
    assert run.c.logical_run_key.unique
    shard_uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in shard.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("job_run_id", "shard_key") in shard_uniques


def test_work_item_state_and_fencing_constraints_match_the_ledger() -> None:
    table = Base.metadata.tables["agri.job_work_item"]
    status_type = table.c.status.type
    assert isinstance(status_type, Enum)
    assert set(status_type.enums) == {
        "queued",
        "leased",
        "running",
        "retry_wait",
        "deferred",
        "succeeded",
        "dead_letter",
        "cancelled",
    }
    checks = _check_sql("job_work_item")
    assert any("fencing_token > 0" in check for check in checks)
    assert any("next_attempt_at IS NOT NULL" in check for check in checks)
    assert any("attempt_count <= max_attempts" in check for check in checks)


def test_checkpoint_and_output_are_bound_to_attempt_fences() -> None:
    checkpoint_targets = {
        foreign_key.target_fullname for foreign_key in Base.metadata.tables["agri.job_checkpoint"].foreign_keys
    }
    output_targets = {
        foreign_key.target_fullname for foreign_key in Base.metadata.tables["agri.job_output"].foreign_keys
    }
    expected = {
        "agri.job_attempt.id",
        "agri.job_attempt.job_work_item_id",
        "agri.job_attempt.fencing_token",
    }
    assert expected <= checkpoint_targets
    assert expected <= output_targets
    assert "agri.artifact.id" in output_targets
    output = Base.metadata.tables["agri.job_output"]
    lineage_fks = {
        tuple(element.parent.name for element in constraint.elements): tuple(
            element.target_fullname for element in constraint.elements
        )
        for constraint in output.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert lineage_fks[("job_work_item_id", "job_run_id")] == (
        "agri.job_work_item.id",
        "agri.job_work_item.job_run_id",
    )
    assert any("fencing_token IS NOT NULL" in check for check in _check_sql("job_output"))


def test_work_item_exposes_composite_run_identity() -> None:
    work_item = Base.metadata.tables["agri.job_work_item"]
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in work_item.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("id", "job_run_id") in unique_columns


def test_inline_artifacts_are_size_checked() -> None:
    checks = _check_sql("artifact")
    assert any("octet_length(content_bytes) = size_bytes" in check for check in checks)
    assert any("database_inline" in check for check in checks)


def test_operational_events_are_partitioned_and_redacted_by_contract() -> None:
    table = Base.metadata.tables["agri.job_event"]
    assert table.dialect_options["postgresql"]["partition_by"] == "RANGE (occurred_at)"
    assert "detail" in table.c
    assert "payload" not in table.c


def test_lookup_approval_requires_reviewed_evidence() -> None:
    strategy_checks = _check_sql("strategies")
    companion_checks = _check_sql("companion_relationships")
    assert any("evidence_citation IS NOT NULL" in check for check in strategy_checks)
    assert any("evidence_citation IS NOT NULL" in check for check in companion_checks)
    assert Base.metadata.tables["agri.strategies"].c.review_state.server_default.arg == "draft"
