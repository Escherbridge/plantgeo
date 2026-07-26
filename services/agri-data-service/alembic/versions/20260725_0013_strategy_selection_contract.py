"""Add governed intervention labels and strategy-selection receipts.

Revision ID: 20260725_0013
Revises: 20260725_0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from agri_data_service.db.sql_objects import load_object_sql
from alembic import op

revision = "20260725_0013"
down_revision = "20260725_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "agri"


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
        primary_key=True,
    )


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def upgrade() -> None:
    op.add_column(
        "forecast_feature_snapshot",
        sa.Column("job_output_id", postgresql.UUID(as_uuid=True)),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        op.f("fk_forecast_feature_snapshot_job_output_id_job_output"),
        "forecast_feature_snapshot",
        "job_output",
        ["job_output_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )

    op.create_table(
        "strategy_outcome_definition",
        _uuid_pk(),
        sa.Column("definition_key", sa.String(length=255), nullable=False),
        sa.Column("definition_version", sa.String(length=100), nullable=False),
        sa.Column("metric_name", sa.String(length=150), nullable=False),
        sa.Column("metric_unit", sa.String(length=64), nullable=False),
        sa.Column("benefit_direction", sa.String(length=16), nullable=False),
        sa.Column("smallest_meaningful_effect", sa.Float(), nullable=False),
        sa.Column("baseline_window", sa.Interval(), nullable=False),
        sa.Column("outcome_window", sa.Interval(), nullable=False),
        sa.Column("aggregation_method", sa.String(length=100), nullable=False),
        sa.Column("transform_method", sa.String(length=100), nullable=False),
        sa.Column(
            "eligibility_policy",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("definition_checksum", sa.String(length=64)),
        sa.Column(
            "review_state",
            sa.String(length=24),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.String(length=255)),
        _created_at(),
        sa.UniqueConstraint(
            "definition_key",
            "definition_version",
            name="uq_strategy_outcome_definition_version",
        ),
        sa.CheckConstraint(
            "benefit_direction IN ('increase', 'decrease')",
            name=op.f("ck_strategy_outcome_definition_benefit_direction"),
        ),
        sa.CheckConstraint(
            "smallest_meaningful_effect >= 0 "
            "AND smallest_meaningful_effect::text NOT IN ('NaN', 'Infinity', '-Infinity')",
            name=op.f("ck_strategy_outcome_definition_smallest_meaningful_effect"),
        ),
        sa.CheckConstraint(
            "baseline_window > INTERVAL '0' AND outcome_window > INTERVAL '0'",
            name=op.f("ck_strategy_outcome_definition_positive_windows"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(eligibility_policy) = 'object'",
            name=op.f("ck_strategy_outcome_definition_eligibility_policy_object"),
        ),
        sa.CheckConstraint(
            "definition_checksum IS NULL OR definition_checksum ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_strategy_outcome_definition_checksum_sha256"),
        ),
        sa.CheckConstraint(
            "review_state IN ('draft', 'approved', 'rejected')",
            name=op.f("ck_strategy_outcome_definition_review_state"),
        ),
        sa.CheckConstraint(
            "review_state = 'draft' OR (reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL "
            "AND definition_checksum ~ '^[0-9a-f]{64}$')",
            name=op.f("ck_strategy_outcome_definition_review_evidence"),
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "strategy_label_release",
        _uuid_pk(),
        sa.Column("release_key", sa.String(length=255), nullable=False, unique=True),
        sa.Column("release_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outcome_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("as_of_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("strategy_taxonomy_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("strategy_taxonomy_checksum", sa.String(length=64), nullable=False),
        sa.Column("feature_schema", postgresql.JSONB(), nullable=False),
        sa.Column("feature_schema_checksum", sa.String(length=64), nullable=False),
        sa.Column("extraction_plan_checksum", sa.String(length=64), nullable=False),
        sa.Column("extraction_code_checksum", sa.String(length=64), nullable=False),
        sa.Column("spatial_block_scheme", sa.String(length=120), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        sa.Column("treated_count", sa.BigInteger(), nullable=False),
        sa.Column("control_count", sa.BigInteger(), nullable=False),
        sa.Column("strategy_count", sa.Integer(), nullable=False),
        sa.Column("spatial_block_count", sa.Integer(), nullable=False),
        sa.Column(
            "validation_summary",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'staging'"),
            nullable=False,
        ),
        sa.Column("receipt_checksum", sa.String(length=64)),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        _created_at(),
        sa.ForeignKeyConstraint(["release_set_id"], ["agri.release_set.id"]),
        sa.ForeignKeyConstraint(
            ["outcome_definition_id"],
            ["agri.strategy_outcome_definition.id"],
        ),
        sa.CheckConstraint(
            "strategy_taxonomy_checksum ~ '^[0-9a-f]{64}$' "
            "AND feature_schema_checksum ~ '^[0-9a-f]{64}$' "
            "AND extraction_plan_checksum ~ '^[0-9a-f]{64}$' "
            "AND extraction_code_checksum ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_strategy_label_release_input_checksums"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(strategy_taxonomy_snapshot) = 'array' "
            "AND jsonb_array_length(strategy_taxonomy_snapshot) > 0 "
            "AND jsonb_typeof(feature_schema) = 'array' "
            "AND jsonb_array_length(feature_schema) > 0 "
            "AND jsonb_typeof(validation_summary) = 'object'",
            name=op.f("ck_strategy_label_release_json_contracts"),
        ),
        sa.CheckConstraint(
            "row_count >= 0 AND treated_count >= 0 AND control_count >= 0 "
            "AND row_count = treated_count + control_count "
            "AND strategy_count >= 0 AND spatial_block_count >= 0",
            name=op.f("ck_strategy_label_release_counts"),
        ),
        sa.CheckConstraint(
            "status IN ('staging', 'validated', 'rejected')",
            name=op.f("ck_strategy_label_release_status"),
        ),
        sa.CheckConstraint(
            "status <> 'validated' OR (row_count > 0 AND treated_count > 0 "
            "AND control_count > 0 AND strategy_count > 0 AND spatial_block_count > 0 "
            "AND receipt_checksum ~ '^[0-9a-f]{64}$' AND validated_at IS NOT NULL)",
            name=op.f("ck_strategy_label_release_validated_evidence"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_strategy_label_release_outcome_asof",
        "strategy_label_release",
        ["outcome_definition_id", sa.text("as_of_time DESC")],
        schema=SCHEMA,
    )

    op.add_column(
        "forecast_training_run",
        sa.Column("strategy_label_release_id", postgresql.UUID(as_uuid=True)),
        schema=SCHEMA,
    )
    op.add_column(
        "forecast_training_run",
        sa.Column("strategy_label_checksum", sa.String(length=64)),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        op.f("fk_forecast_training_run_strategy_label_release_id_strategy_label_release"),
        "forecast_training_run",
        "strategy_label_release",
        ["strategy_label_release_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_forecast_training_strategy_label_binding"),
        "forecast_training_run",
        "((strategy_label_release_id IS NULL AND strategy_label_checksum IS NULL) "
        "OR (strategy_label_release_id IS NOT NULL "
        "AND strategy_label_checksum ~ '^[0-9a-f]{64}$'))",
        schema=SCHEMA,
    )

    op.create_table(
        "strategy_label_episode",
        _uuid_pk(),
        sa.Column("episode_key", sa.String(length=255), nullable=False, unique=True),
        sa.Column("label_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True)),
        sa.Column("arm_kind", sa.String(length=16), nullable=False),
        sa.Column("cohort_key", sa.String(length=255), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("intervention_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("intervention_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("baseline_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("baseline_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("baseline_evidence_input_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outcome_evidence_input_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_value", sa.Float(), nullable=False),
        sa.Column("target_unit", sa.String(length=64), nullable=False),
        sa.Column("assignment_mechanism", sa.String(length=64), nullable=False),
        sa.Column("known_assignment_probability", sa.Float()),
        sa.Column("spatial_block_key", sa.String(length=255), nullable=False),
        sa.Column("covariate_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("covariate_checksum", sa.String(length=64), nullable=False),
        sa.Column("covariates_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("episode_checksum", sa.String(length=64), nullable=False, unique=True),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["label_release_id"],
            ["agri.strategy_label_release.id"],
        ),
        sa.ForeignKeyConstraint(
            ["analysis_subject_id"],
            ["agri.analysis_subject.id"],
        ),
        sa.ForeignKeyConstraint(["strategy_id"], ["agri.strategies.id"]),
        sa.ForeignKeyConstraint(
            ["baseline_evidence_input_id"],
            ["agri.intervention_evidence_input.id"],
        ),
        sa.ForeignKeyConstraint(
            ["outcome_evidence_input_id"],
            ["agri.intervention_evidence_input.id"],
        ),
        sa.CheckConstraint(
            "(arm_kind = 'treatment' AND strategy_id IS NOT NULL) "
            "OR (arm_kind = 'control' AND strategy_id IS NULL)",
            name=op.f("ck_strategy_label_episode_arm_strategy"),
        ),
        sa.CheckConstraint(
            "baseline_start < baseline_end AND baseline_end <= assigned_at "
            "AND assigned_at <= intervention_start "
            "AND intervention_start < intervention_end "
            "AND intervention_end <= outcome_start AND outcome_start < outcome_end "
            "AND outcome_end <= data_available_at "
            "AND covariates_available_at <= assigned_at",
            name=op.f("ck_strategy_label_episode_ordered_windows"),
        ),
        sa.CheckConstraint(
            "target_value::text NOT IN ('NaN', 'Infinity', '-Infinity')",
            name=op.f("ck_strategy_label_episode_finite_target"),
        ),
        sa.CheckConstraint(
            "known_assignment_probability IS NULL "
            "OR (known_assignment_probability > 0 AND known_assignment_probability < 1)",
            name=op.f("ck_strategy_label_episode_assignment_probability"),
        ),
        sa.CheckConstraint(
            "btrim(cohort_key) <> '' AND jsonb_typeof(covariate_snapshot) = 'array' "
            "AND jsonb_array_length(covariate_snapshot) > 0 "
            "AND covariate_checksum ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_strategy_label_episode_covariate_contract"),
        ),
        sa.CheckConstraint(
            "episode_checksum ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_strategy_label_episode_checksum_sha256"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_strategy_label_episode_release_arm",
        "strategy_label_episode",
        ["label_release_id", "arm_kind", "strategy_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_strategy_label_episode_release_block",
        "strategy_label_episode",
        ["label_release_id", "spatial_block_key"],
        schema=SCHEMA,
    )

    op.create_table(
        "strategy_selection_policy",
        _uuid_pk(),
        sa.Column("policy_key", sa.String(length=255), nullable=False),
        sa.Column("policy_version", sa.String(length=100), nullable=False),
        sa.Column("min_treated_per_strategy", sa.Integer(), nullable=False),
        sa.Column("min_control_count", sa.Integer(), nullable=False),
        sa.Column("min_spatial_blocks", sa.Integer(), nullable=False),
        sa.Column("min_effective_sample_size", sa.Float(), nullable=False),
        sa.Column("min_overlap_score", sa.Float(), nullable=False),
        sa.Column("max_weighted_smd", sa.Float(), nullable=False),
        sa.Column("min_coverage_fraction", sa.Float(), nullable=False),
        sa.Column("max_data_age", sa.Interval(), nullable=False),
        sa.Column("min_conservative_value_gain", sa.Float(), nullable=False),
        sa.Column("max_model_disagreement", sa.Float(), nullable=False),
        sa.Column("max_ood_score", sa.Float(), nullable=False),
        sa.Column("score_weights", postgresql.JSONB(), nullable=False),
        sa.Column(
            "allow_effect_claims",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("policy_checksum", sa.String(length=64)),
        sa.Column(
            "review_state",
            sa.String(length=24),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.String(length=255)),
        _created_at(),
        sa.UniqueConstraint(
            "policy_key",
            "policy_version",
            name="uq_strategy_selection_policy_version",
        ),
        sa.CheckConstraint(
            "min_treated_per_strategy > 0 AND min_control_count > 0 "
            "AND min_spatial_blocks > 0 AND min_effective_sample_size > 0",
            name=op.f("ck_strategy_selection_policy_positive_support"),
        ),
        sa.CheckConstraint(
            "min_overlap_score BETWEEN 0 AND 1 AND min_coverage_fraction BETWEEN 0 AND 1 "
            "AND max_weighted_smd >= 0 AND max_model_disagreement >= 0 "
            "AND max_ood_score >= 0 AND max_data_age > INTERVAL '0'",
            name=op.f("ck_strategy_selection_policy_valid_thresholds"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(score_weights) = 'object'",
            name=op.f("ck_strategy_selection_policy_score_weights_object"),
        ),
        sa.CheckConstraint(
            "policy_checksum IS NULL OR policy_checksum ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_strategy_selection_policy_checksum_sha256"),
        ),
        sa.CheckConstraint(
            "review_state IN ('draft', 'approved', 'rejected')",
            name=op.f("ck_strategy_selection_policy_review_state"),
        ),
        sa.CheckConstraint(
            "review_state = 'draft' OR (reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL "
            "AND policy_checksum ~ '^[0-9a-f]{64}$')",
            name=op.f("ck_strategy_selection_policy_review_evidence"),
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "strategy_selection_receipt",
        _uuid_pk(),
        sa.Column("selection_key", sa.String(length=255), nullable=False, unique=True),
        sa.Column("analysis_subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("forecast_receipt_id", postgresql.UUID(as_uuid=True)),
        sa.Column("forecast_iteration_id", postgresql.UUID(as_uuid=True)),
        sa.Column("feature_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("training_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("selection_policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issue_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applicability_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applicability_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "execution_mode",
            sa.String(length=24),
            server_default=sa.text("'evaluation_only'"),
            nullable=False,
        ),
        sa.Column(
            "claim_tier",
            sa.String(length=32),
            server_default=sa.text("'feasibility_candidate'"),
            nullable=False,
        ),
        sa.Column("decision_state", sa.String(length=24), nullable=False),
        sa.Column("abstention_reason", sa.Text()),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("receipt_checksum", sa.String(length=64)),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'staging'"),
            nullable=False,
        ),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["analysis_subject_id"],
            ["agri.analysis_subject.id"],
        ),
        sa.ForeignKeyConstraint(
            ["forecast_receipt_id"],
            ["agri.forecast_receipt.id"],
        ),
        sa.ForeignKeyConstraint(
            ["forecast_iteration_id"],
            ["agri.forecast_iteration.id"],
        ),
        sa.ForeignKeyConstraint(
            ["feature_snapshot_id"],
            ["agri.forecast_feature_snapshot.id"],
        ),
        sa.ForeignKeyConstraint(
            ["training_run_id"],
            ["agri.forecast_training_run.id"],
        ),
        sa.ForeignKeyConstraint(
            ["selection_policy_id"],
            ["agri.strategy_selection_policy.id"],
        ),
        sa.CheckConstraint(
            "(execution_mode = 'evaluation_only' AND forecast_iteration_id IS NOT NULL "
            "AND forecast_receipt_id IS NULL) OR "
            "(execution_mode = 'publishable' AND forecast_receipt_id IS NOT NULL "
            "AND forecast_iteration_id IS NULL)",
            name=op.f("ck_strategy_selection_receipt_forecast_source"),
        ),
        sa.CheckConstraint(
            "claim_tier IN ('feasibility_candidate', 'effect_candidate') "
            "AND (claim_tier <> 'effect_candidate' OR execution_mode = 'publishable')",
            name=op.f("ck_strategy_selection_receipt_claim_tier"),
        ),
        sa.CheckConstraint(
            "decision_state IN ('ranked', 'abstained') "
            "AND ((decision_state = 'ranked' AND abstention_reason IS NULL AND candidate_count > 0) "
            "OR (decision_state = 'abstained' AND abstention_reason IS NOT NULL AND candidate_count >= 0))",
            name=op.f("ck_strategy_selection_receipt_decision"),
        ),
        sa.CheckConstraint(
            "data_cutoff <= issue_time AND applicability_start >= issue_time "
            "AND applicability_end > applicability_start",
            name=op.f("ck_strategy_selection_receipt_ordered_times"),
        ),
        sa.CheckConstraint(
            "status IN ('staging', 'finalized')",
            name=op.f("ck_strategy_selection_receipt_status"),
        ),
        sa.CheckConstraint(
            "status <> 'finalized' OR "
            "(receipt_checksum ~ '^[0-9a-f]{64}$' AND finalized_at IS NOT NULL)",
            name=op.f("ck_strategy_selection_receipt_finalized_evidence"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_strategy_selection_receipt_subject_issue",
        "strategy_selection_receipt",
        ["analysis_subject_id", sa.text("issue_time DESC")],
        schema=SCHEMA,
    )

    op.create_table(
        "strategy_selection_candidate",
        _uuid_pk(),
        sa.Column("selection_receipt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("strategy_snapshot_checksum", sa.String(length=64), nullable=False),
        sa.Column("eligibility_state", sa.String(length=32), nullable=False),
        sa.Column("exclusion_reason", sa.Text()),
        sa.Column("predicted_control_outcome", sa.Float()),
        sa.Column("predicted_strategy_outcome", sa.Float()),
        sa.Column("expected_effect", sa.Float()),
        sa.Column("effect_lower_bound", sa.Float()),
        sa.Column("effect_upper_bound", sa.Float()),
        sa.Column("overlap_score", sa.Float()),
        sa.Column("effective_sample_size", sa.Float()),
        sa.Column("weighted_smd", sa.Float()),
        sa.Column("coverage_fraction", sa.Float()),
        sa.Column("model_disagreement", sa.Float()),
        sa.Column("ood_score", sa.Float()),
        sa.Column("conservative_score", sa.Float()),
        sa.Column("rank", sa.Integer()),
        sa.Column("evidence_tier", sa.String(length=32), nullable=False),
        sa.Column(
            "score_components",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("candidate_checksum", sa.String(length=64), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["selection_receipt_id"],
            ["agri.strategy_selection_receipt.id"],
        ),
        sa.ForeignKeyConstraint(["strategy_id"], ["agri.strategies.id"]),
        sa.UniqueConstraint(
            "selection_receipt_id",
            "strategy_id",
            name="uq_strategy_selection_candidate_strategy",
        ),
        sa.UniqueConstraint(
            "selection_receipt_id",
            "rank",
            name="uq_strategy_selection_candidate_rank",
        ),
        sa.CheckConstraint(
            "eligibility_state IN ('eligible', 'excluded', 'insufficient_evidence', 'no_effect')",
            name=op.f("ck_strategy_selection_candidate_eligibility"),
        ),
        sa.CheckConstraint(
            "(eligibility_state = 'eligible' AND exclusion_reason IS NULL) "
            "OR (eligibility_state <> 'eligible' AND exclusion_reason IS NOT NULL)",
            name=op.f("ck_strategy_selection_candidate_eligibility_reason"),
        ),
        sa.CheckConstraint(
            "(effect_lower_bound IS NULL OR expected_effect IS NULL "
            "OR effect_lower_bound <= expected_effect) "
            "AND (effect_upper_bound IS NULL OR expected_effect IS NULL "
            "OR expected_effect <= effect_upper_bound)",
            name=op.f("ck_strategy_selection_candidate_effect_bounds"),
        ),
        sa.CheckConstraint(
            "(overlap_score IS NULL OR overlap_score BETWEEN 0 AND 1) "
            "AND (coverage_fraction IS NULL OR coverage_fraction BETWEEN 0 AND 1) "
            "AND (effective_sample_size IS NULL OR effective_sample_size >= 0) "
            "AND (weighted_smd IS NULL OR weighted_smd >= 0) "
            "AND (model_disagreement IS NULL OR model_disagreement >= 0) "
            "AND (ood_score IS NULL OR ood_score >= 0) "
            "AND (rank IS NULL OR rank > 0)",
            name=op.f("ck_strategy_selection_candidate_metrics"),
        ),
        sa.CheckConstraint(
            "evidence_tier IN ('feasibility_candidate', 'effect_candidate')",
            name=op.f("ck_strategy_selection_candidate_evidence_tier"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(strategy_snapshot) = 'object' "
            "AND jsonb_typeof(score_components) = 'object'",
            name=op.f("ck_strategy_selection_candidate_json_contracts"),
        ),
        sa.CheckConstraint(
            "strategy_snapshot_checksum ~ '^[0-9a-f]{64}$' "
            "AND candidate_checksum ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_strategy_selection_candidate_checksums"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_strategy_selection_candidate_receipt_score",
        "strategy_selection_candidate",
        ["selection_receipt_id", sa.text("rank ASC")],
        schema=SCHEMA,
    )

    for function_file in (
        "functions/strategy_outcome_definition_checksum.sql",
        "functions/strategy_selection_policy_checksum.sql",
        "functions/strategy_label_release_checksum.sql",
        "functions/strategy_label_episode_checksum.sql",
        "functions/require_strategy_initial_state.sql",
        "functions/guard_strategy_child_insert.sql",
        "functions/guard_strategy_review_change.sql",
        "functions/guard_strategy_label_release_change.sql",
        "functions/finalize_strategy_label_release.sql",
        "functions/export_strategy_label_bundle.sql",
        "functions/strategy_label_bundle_checksum.sql",
        "functions/strategy_selection_candidate_checksum.sql",
        "functions/strategy_selection_receipt_checksum.sql",
        "functions/guard_strategy_selection_receipt_change.sql",
        "functions/finalize_strategy_selection_receipt.sql",
    ):
        op.execute(load_object_sql(function_file))

    op.execute(
        load_object_sql(
            "functions/validate_forecast_feature_snapshot.sql",
            or_replace=True,
        )
    )
    op.execute(
        load_object_sql(
            "functions/validate_forecast_training_run.sql",
            or_replace=True,
        )
    )

    for trigger_file in (
        "triggers/strategy_outcome_definition.sql",
        "triggers/strategy_label_release.sql",
        "triggers/strategy_label_episode.sql",
        "triggers/strategy_selection_policy.sql",
        "triggers/strategy_selection_receipt.sql",
        "triggers/strategy_selection_candidate.sql",
    ):
        op.execute(load_object_sql(trigger_file))

    op.execute(
        """
        REVOKE EXECUTE ON FUNCTION agri.strategy_outcome_definition_checksum(
            agri.strategy_outcome_definition
        ) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.strategy_selection_policy_checksum(
            agri.strategy_selection_policy
        ) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.strategy_label_release_checksum(uuid) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.strategy_label_episode_checksum(uuid) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.finalize_strategy_label_release(uuid, varchar) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.export_strategy_label_bundle(uuid) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.strategy_label_bundle_checksum(uuid) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.strategy_selection_candidate_checksum(uuid) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.strategy_selection_receipt_checksum(uuid) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.finalize_strategy_selection_receipt(uuid, varchar) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.guard_strategy_review_change() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.require_strategy_initial_state() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.guard_strategy_child_insert() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.guard_strategy_label_release_change() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.guard_strategy_selection_receipt_change() FROM PUBLIC;
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Strategy labels and selection receipts are forward-only evidence; "
        "restore a verified backup into a fresh database."
    )
