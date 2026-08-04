"""Static contracts for the governed strategy-selection schema.

Revision ``20260803_0018`` retired this plane's enforcement layer while keeping
its tables and every checksum function, so the programmable objects 0013 loaded
now split in two: those still on disk, which 0013 keeps loading through
``load_object_sql``, and those whose canonical file is gone, whose DDL 0013 now
carries itself as a module-level raw string. Both halves are checked here.
"""

from pathlib import Path

SERVICE_ROOT = Path(__file__).parents[1]
MIGRATION = SERVICE_ROOT / "alembic" / "versions" / "20260725_0013_strategy_selection_contract.py"
FUNCTION_ROOT = SERVICE_ROOT / "db" / "agri" / "functions"
TRIGGER_ROOT = SERVICE_ROOT / "db" / "agri" / "triggers"
# An embedded body must appear exactly twice: its definition and its one execution.
EMBEDDED_BODY_MENTIONS = 2


def _migration() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _embedded_body(migration: str, constant_name: str) -> str:
    """The raw-string DDL 0013 carries for a function whose canonical file 0018 removed."""
    opener = f'{constant_name} = r"""'
    start = migration.index(opener) + len(opener)
    return migration[start : migration.index('"""', start)]


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

    surviving_function_files = (
        "strategy_outcome_definition_checksum.sql",
        "strategy_selection_policy_checksum.sql",
        "strategy_label_release_checksum.sql",
        "strategy_label_episode_checksum.sql",
        "require_strategy_initial_state.sql",
        "guard_strategy_review_change.sql",
        "export_strategy_label_bundle.sql",
        "strategy_label_bundle_checksum.sql",
        "strategy_selection_candidate_checksum.sql",
        "strategy_selection_receipt_checksum.sql",
    )
    # Created by 0013, dropped by 0018: the canonical file is gone and must stay gone,
    # so 0013 carries the DDL itself. Each entry is (file name, constant, CREATE header).
    retired_function_bodies = (
        (
            "guard_strategy_child_insert.sql",
            "_GUARD_STRATEGY_CHILD_INSERT",
            "CREATE FUNCTION agri.guard_strategy_child_insert() RETURNS trigger",
        ),
        (
            "guard_strategy_label_release_change.sql",
            "_GUARD_STRATEGY_LABEL_RELEASE_CHANGE",
            "CREATE FUNCTION agri.guard_strategy_label_release_change() RETURNS trigger",
        ),
        (
            "finalize_strategy_label_release.sql",
            "_FINALIZE_STRATEGY_LABEL_RELEASE",
            "CREATE FUNCTION agri.finalize_strategy_label_release("
            "p_label_release_id uuid, p_expected_checksum character varying)"
            " RETURNS agri.strategy_label_release",
        ),
        (
            "guard_strategy_selection_receipt_change.sql",
            "_GUARD_STRATEGY_SELECTION_RECEIPT_CHANGE",
            "CREATE FUNCTION agri.guard_strategy_selection_receipt_change() RETURNS trigger",
        ),
        (
            "finalize_strategy_selection_receipt.sql",
            "_FINALIZE_STRATEGY_SELECTION_RECEIPT",
            "CREATE FUNCTION agri.finalize_strategy_selection_receipt("
            "p_selection_receipt_id uuid, p_expected_checksum character varying)"
            " RETURNS agri.strategy_selection_receipt",
        ),
    )
    for name in surviving_function_files:
        assert (FUNCTION_ROOT / name).is_file()
        assert f'"functions/{name}"' in migration
    for file_name, constant_name, create_header in retired_function_bodies:
        assert not (FUNCTION_ROOT / file_name).exists(), file_name
        # Loading a file 0018 deleted would break a chain replayed from scratch.
        assert f'"functions/{file_name}"' not in migration, file_name
        body = _embedded_body(migration, constant_name)
        assert create_header in body, create_header
        assert "RAISE EXCEPTION" in body, constant_name
        # Defined once, executed once: a body that stops being applied fails here.
        assert migration.count(constant_name) == EMBEDDED_BODY_MENTIONS, constant_name

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
    """``guard_strategy_review_change`` is the sole writer of both review checksums.

    The child-insert parent-state rule left with ``guard_strategy_child_insert`` in
    revision 20260803_0018; the two child tables keep only their append-only guard.
    """
    review_guard = (FUNCTION_ROOT / "guard_strategy_review_change.sql").read_text(encoding="utf-8")
    initial_guard = (FUNCTION_ROOT / "require_strategy_initial_state.sql").read_text(encoding="utf-8")

    assert "strategy_outcome_definition_checksum(NEW)" in review_guard
    assert "strategy_selection_policy_checksum(NEW)" in review_guard
    assert "NEW.review_state <> 'draft'" in initial_guard
    assert "NEW.status <> 'staging'" in initial_guard

    for trigger_name in (
        "strategy_outcome_definition.sql",
        "strategy_selection_policy.sql",
        "strategy_label_release.sql",
        "strategy_selection_receipt.sql",
    ):
        assert "require_strategy_initial_state()" in (TRIGGER_ROOT / trigger_name).read_text(encoding="utf-8")
    for trigger_name in ("strategy_label_episode.sql", "strategy_selection_candidate.sql"):
        assert "guard_forecast_immutable_rows()" in (TRIGGER_ROOT / trigger_name).read_text(encoding="utf-8")


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
