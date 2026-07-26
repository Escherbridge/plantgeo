"""Static contracts for the governed strategy-selection schema."""

from pathlib import Path

SERVICE_ROOT = Path(__file__).parents[1]
MIGRATION = SERVICE_ROOT / "alembic" / "versions" / "20260725_0013_strategy_selection_contract.py"
FUNCTION_ROOT = SERVICE_ROOT / "db" / "agri" / "functions"
TRIGGER_ROOT = SERVICE_ROOT / "db" / "agri" / "triggers"
EXPECTED_PARENT_LOCKS = 2


def _migration() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_strategy_selection_revision_is_additive_and_forward_only() -> None:
    migration = _migration()

    assert 'revision = "20260725_0013"' in migration
    assert 'down_revision = "20260725_0012"' in migration
    assert "raise NotImplementedError" in migration
    assert "DROP TABLE" not in migration
    assert "DROP COLUMN" not in migration
    assert '"job_output_id"' in migration
    assert '"strategy_label_release_id"' in migration
    assert '"strategy_label_checksum"' in migration


def test_strategy_selection_revision_adds_the_complete_receipt_plane() -> None:
    migration = _migration()

    for table in (
        "strategy_outcome_definition",
        "strategy_label_release",
        "strategy_label_episode",
        "strategy_selection_policy",
        "strategy_selection_receipt",
        "strategy_selection_candidate",
    ):
        assert f'"{table}"' in migration

    for contract in (
        "arm_kind = 'treatment'",
        "arm_kind = 'control'",
        "forecast_iteration_id",
        "forecast_receipt_id",
        "feasibility_candidate",
        "effect_candidate",
        "decision_state = 'abstained'",
        "min_treated_per_strategy",
        "min_control_count",
        "min_spatial_blocks",
        "min_effective_sample_size",
        "min_overlap_score",
        "max_weighted_smd",
        "min_coverage_fraction",
        "max_model_disagreement",
        "max_ood_score",
        "smallest_meaningful_effect",
        "feature_schema",
        "feature_schema_checksum",
        "cohort_key",
        "assigned_at",
        "covariate_snapshot",
        "covariate_checksum",
        "covariates_available_at",
    ):
        assert contract in migration


def test_strategy_selection_programmable_objects_are_canonical_and_private() -> None:
    migration = _migration()

    function_files = (
        "strategy_outcome_definition_checksum.sql",
        "strategy_selection_policy_checksum.sql",
        "strategy_label_release_checksum.sql",
        "strategy_label_episode_checksum.sql",
        "require_strategy_initial_state.sql",
        "guard_strategy_child_insert.sql",
        "guard_strategy_review_change.sql",
        "guard_strategy_label_release_change.sql",
        "finalize_strategy_label_release.sql",
        "export_strategy_label_bundle.sql",
        "strategy_label_bundle_checksum.sql",
        "strategy_selection_candidate_checksum.sql",
        "strategy_selection_receipt_checksum.sql",
        "guard_strategy_selection_receipt_change.sql",
        "finalize_strategy_selection_receipt.sql",
    )
    for name in function_files:
        assert (FUNCTION_ROOT / name).is_file()
        assert f'"functions/{name}"' in migration

    trigger_files = (
        "strategy_outcome_definition.sql",
        "strategy_label_release.sql",
        "strategy_label_episode.sql",
        "strategy_selection_policy.sql",
        "strategy_selection_receipt.sql",
        "strategy_selection_candidate.sql",
    )
    for name in trigger_files:
        assert (TRIGGER_ROOT / name).is_file()
        assert f'"triggers/{name}"' in migration

    assert "REVOKE EXECUTE ON FUNCTION agri.finalize_strategy_label_release" in migration
    assert "REVOKE EXECUTE ON FUNCTION agri.export_strategy_label_bundle" in migration
    assert "REVOKE EXECUTE ON FUNCTION agri.strategy_label_bundle_checksum" in migration
    assert "REVOKE EXECUTE ON FUNCTION agri.finalize_strategy_selection_receipt" in migration
    assert '"functions/validate_forecast_feature_snapshot.sql"' in migration
    assert '"functions/validate_forecast_training_run.sql"' in migration


def test_label_finalization_is_availability_and_lineage_gated() -> None:
    finalizer = (FUNCTION_ROOT / "finalize_strategy_label_release.sql").read_text(encoding="utf-8")

    for contract in (
        "baseline.analysis_subject_id <> episode.analysis_subject_id",
        "observed.analysis_subject_id <> episode.analysis_subject_id",
        "baseline.release_set_id <> label.release_set_id",
        "observed.release_set_id <> label.release_set_id",
        "baseline.evidence_kind <> 'observed_fact'",
        "observed.evidence_kind <> 'observed_fact'",
        "baseline.value_unit IS DISTINCT FROM outcome.metric_unit",
        "observed.value_unit IS DISTINCT FROM outcome.metric_unit",
        "episode.target_unit <> outcome.metric_unit",
        "observed.numeric_value - baseline.numeric_value",
        "strategy_label_episode_checksum",
        "baseline.data_available_at > label.as_of_time",
        "observed.data_available_at > label.as_of_time",
        "episode.data_available_at > label.as_of_time",
        "episode.covariates_available_at > episode.assigned_at",
        "jsonb_array_length(episode.covariate_snapshot)",
        "label.feature_schema_checksum",
        "strategy_outcome_definition_checksum",
        "count(DISTINCT episode.assigned_at) <> 1",
        "cohort maps to multiple assignment times",
        "strategy_taxonomy_snapshot",
        "strategy_label_release_checksum",
    ):
        assert contract in finalizer


def test_receipt_finalization_is_cutoff_honest_and_effect_disabled() -> None:
    finalizer = (FUNCTION_ROOT / "finalize_strategy_selection_receipt.sql").read_text(encoding="utf-8")

    for contract in (
        "model.model_purpose <> 'strategy_selection'",
        "publication.state = 'published'",
        "label.as_of_time > receipt.data_cutoff",
        "feature.training_window_end > receipt.data_cutoff",
        "strategy_selection_policy_checksum",
        "training.strategy_label_checksum IS DISTINCT FROM label.receipt_checksum",
        "effect_candidate finalization is disabled in strategy_selection_v1",
        "cluster-bootstrap",
        "placebo",
        "negative-control",
        "best-vs-second lower-bound",
    ):
        assert contract in finalizer

    assert "abstained strategy selection cannot retain ranked candidates" in finalizer
    assert "feasibility receipt cannot contain effect-tier candidates" in finalizer
    assert "receipt.data_cutoff > label.as_of_time" not in finalizer
    assert "receipt.data_cutoff > feature.training_window_end" not in finalizer


def test_training_validation_binds_artifact_output_to_label_receipt() -> None:
    validator = (FUNCTION_ROOT / "validate_forecast_training_run.sql").read_text(encoding="utf-8")
    receipt = (FUNCTION_ROOT / "strategy_selection_receipt_checksum.sql").read_text(encoding="utf-8")

    for contract in (
        "strategy_label_release_checksum(label_release.id)",
        "training.strategy_label_checksum IS DISTINCT FROM",
        "output.metadata_json ->> 'strategy_label_checksum' IS DISTINCT FROM",
        "strategy_label_bundle_checksum(label_release.id)",
        "output.metadata_json ->> 'label_bundle_checksum' IS DISTINCT FROM",
        "metric-forecast training cannot bind a strategy label release",
    ):
        assert contract in validator
    assert "training.strategy_label_checksum" in receipt


def test_review_and_child_writes_are_state_and_server_checksum_gated() -> None:
    review_guard = (FUNCTION_ROOT / "guard_strategy_review_change.sql").read_text(encoding="utf-8")
    initial_guard = (FUNCTION_ROOT / "require_strategy_initial_state.sql").read_text(encoding="utf-8")
    child_guard = (FUNCTION_ROOT / "guard_strategy_child_insert.sql").read_text(encoding="utf-8")

    assert "strategy_outcome_definition_checksum(NEW)" in review_guard
    assert "strategy_selection_policy_checksum(NEW)" in review_guard
    assert "NEW.review_state <> 'draft'" in initial_guard
    assert "NEW.status <> 'staging'" in initial_guard
    assert "parent_status IS DISTINCT FROM 'staging'" in child_guard
    assert child_guard.count("FOR UPDATE") == EXPECTED_PARENT_LOCKS
    assert "strategy_label_episode" in child_guard
    assert "strategy_selection_candidate" in child_guard

    for trigger_name in (
        "strategy_outcome_definition.sql",
        "strategy_selection_policy.sql",
        "strategy_label_release.sql",
        "strategy_selection_receipt.sql",
    ):
        assert "require_strategy_initial_state()" in (TRIGGER_ROOT / trigger_name).read_text(encoding="utf-8")
    for trigger_name in ("strategy_label_episode.sql", "strategy_selection_candidate.sql"):
        assert "guard_strategy_child_insert()" in (TRIGGER_ROOT / trigger_name).read_text(encoding="utf-8")


def test_validated_label_export_matches_the_strict_trainer_bundle() -> None:
    export = (FUNCTION_ROOT / "export_strategy_label_bundle.sql").read_text(encoding="utf-8")
    checksum = (FUNCTION_ROOT / "strategy_label_bundle_checksum.sql").read_text(encoding="utf-8")

    for contract in (
        "'schema_version', 'strategy_labels_v1'",
        "'label_release_checksum', label.receipt_checksum",
        "'as_of_time', label.as_of_time",
        "'smallest_meaningful_effect'",
        "'feature_names', label.feature_schema",
        "'episode_id'",
        "'subject_id'",
        "'strategy_id'",
        "'arm'",
        "'cohort'",
        "'spatial_block'",
        "'assigned_at'",
        "'covariates_available_at'",
        "'data_available_at'",
        "'baseline_value'",
        "'outcome_value'",
        "'features', episode.covariate_snapshot",
        "ORDER BY episode.episode_key",
        "label.status <> 'validated'",
    ):
        assert contract in export

    assert "export_strategy_label_bundle(p_label_release_id)::text" in checksum
    assert "public.digest" in checksum
