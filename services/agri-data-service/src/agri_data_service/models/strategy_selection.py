"""Governed intervention-label and strategy-selection models."""

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Interval,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from agri_data_service.db.base import Base, UUIDMixin


class StrategyOutcomeDefinition(Base, UUIDMixin):
    """Reviewed, immutable definition of one intervention outcome."""

    __tablename__ = "strategy_outcome_definition"

    definition_key: Mapped[str] = mapped_column(String(255), nullable=False)
    definition_version: Mapped[str] = mapped_column(String(100), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(150), nullable=False)
    metric_unit: Mapped[str] = mapped_column(String(64), nullable=False)
    benefit_direction: Mapped[str] = mapped_column(String(16), nullable=False)
    smallest_meaningful_effect: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_window: Mapped[timedelta] = mapped_column(Interval(), nullable=False)
    outcome_window: Mapped[timedelta] = mapped_column(Interval(), nullable=False)
    aggregation_method: Mapped[str] = mapped_column(String(100), nullable=False)
    transform_method: Mapped[str] = mapped_column(String(100), nullable=False)
    eligibility_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    definition_checksum: Mapped[str | None] = mapped_column(String(64))
    review_state: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'draft'"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "definition_key",
            "definition_version",
            name="uq_strategy_outcome_definition_version",
        ),
        CheckConstraint("benefit_direction IN ('increase', 'decrease')", name="benefit_direction"),
        CheckConstraint(
            "smallest_meaningful_effect >= 0 "
            "AND smallest_meaningful_effect::text NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="smallest_meaningful_effect",
        ),
        CheckConstraint(
            "baseline_window > INTERVAL '0' AND outcome_window > INTERVAL '0'",
            name="positive_windows",
        ),
        CheckConstraint("jsonb_typeof(eligibility_policy) = 'object'", name="eligibility_policy_object"),
        CheckConstraint(
            "definition_checksum IS NULL OR definition_checksum ~ '^[0-9a-f]{64}$'",
            name="checksum_sha256",
        ),
        CheckConstraint(
            "review_state IN ('draft', 'approved', 'rejected')",
            name="review_state",
        ),
    )


class StrategyLabelRelease(Base, UUIDMixin):
    """Checksum-bound cohort of treatment and explicit-control labels."""

    __tablename__ = "strategy_label_release"

    release_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    release_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.release_set.id"), nullable=False
    )
    outcome_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.strategy_outcome_definition.id"), nullable=False
    )
    as_of_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    strategy_taxonomy_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    strategy_taxonomy_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_schema: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    feature_schema_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_plan_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_code_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    spatial_block_scheme: Mapped[str] = mapped_column(String(120), nullable=False)
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    treated_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    control_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    strategy_count: Mapped[int] = mapped_column(Integer, nullable=False)
    spatial_block_count: Mapped[int] = mapped_column(Integer, nullable=False)
    validation_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'staging'"))
    receipt_checksum: Mapped[str | None] = mapped_column(String(64))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "strategy_taxonomy_checksum ~ '^[0-9a-f]{64}$' "
            "AND feature_schema_checksum ~ '^[0-9a-f]{64}$' "
            "AND extraction_plan_checksum ~ '^[0-9a-f]{64}$' "
            "AND extraction_code_checksum ~ '^[0-9a-f]{64}$'",
            name="input_checksums",
        ),
        CheckConstraint(
            "jsonb_typeof(strategy_taxonomy_snapshot) = 'array' "
            "AND jsonb_array_length(strategy_taxonomy_snapshot) > 0 "
            "AND jsonb_typeof(feature_schema) = 'array' "
            "AND jsonb_array_length(feature_schema) > 0 "
            "AND jsonb_typeof(validation_summary) = 'object'",
            name="json_contracts",
        ),
        CheckConstraint(
            "row_count >= 0 AND treated_count >= 0 AND control_count >= 0 "
            "AND row_count = treated_count + control_count "
            "AND strategy_count >= 0 AND spatial_block_count >= 0",
            name="counts",
        ),
        CheckConstraint("status IN ('staging', 'validated', 'rejected')", name="status"),
        Index("ix_strategy_label_release_outcome_asof", "outcome_definition_id", text("as_of_time DESC")),
    )


class StrategyLabelEpisode(Base, UUIDMixin):
    """Immutable treatment or control episode with baseline and outcome evidence."""

    __tablename__ = "strategy_label_episode"

    episode_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    label_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.strategy_label_release.id"), nullable=False
    )
    analysis_subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.analysis_subject.id"), nullable=False
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agri.strategies.id"))
    arm_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    cohort_key: Mapped[str] = mapped_column(String(255), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    intervention_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    intervention_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    baseline_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    baseline_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    outcome_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    outcome_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    baseline_evidence_input_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.intervention_evidence_input.id"), nullable=False
    )
    outcome_evidence_input_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.intervention_evidence_input.id"), nullable=False
    )
    target_value: Mapped[float] = mapped_column(Float, nullable=False)
    target_unit: Mapped[str] = mapped_column(String(64), nullable=False)
    assignment_mechanism: Mapped[str] = mapped_column(String(64), nullable=False)
    known_assignment_probability: Mapped[float | None] = mapped_column(Float)
    spatial_block_key: Mapped[str] = mapped_column(String(255), nullable=False)
    covariate_snapshot: Mapped[list[float]] = mapped_column(JSONB, nullable=False)
    covariate_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    covariates_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    episode_checksum: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "(arm_kind = 'treatment' AND strategy_id IS NOT NULL) OR (arm_kind = 'control' AND strategy_id IS NULL)",
            name="arm_strategy",
        ),
        CheckConstraint(
            "baseline_start < baseline_end AND baseline_end <= assigned_at "
            "AND assigned_at <= intervention_start "
            "AND intervention_start < intervention_end "
            "AND intervention_end <= outcome_start AND outcome_start < outcome_end "
            "AND outcome_end <= data_available_at "
            "AND covariates_available_at <= assigned_at",
            name="ordered_windows",
        ),
        CheckConstraint(
            "target_value::text NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="finite_target",
        ),
        CheckConstraint(
            "known_assignment_probability IS NULL "
            "OR (known_assignment_probability > 0 AND known_assignment_probability < 1)",
            name="assignment_probability",
        ),
        CheckConstraint(
            "btrim(cohort_key) <> '' AND jsonb_typeof(covariate_snapshot) = 'array' "
            "AND jsonb_array_length(covariate_snapshot) > 0 "
            "AND covariate_checksum ~ '^[0-9a-f]{64}$'",
            name="covariate_contract",
        ),
        CheckConstraint("episode_checksum ~ '^[0-9a-f]{64}$'", name="checksum_sha256"),
        Index("ix_strategy_label_episode_release_arm", "label_release_id", "arm_kind", "strategy_id"),
        Index("ix_strategy_label_episode_release_block", "label_release_id", "spatial_block_key"),
    )


class StrategySelectionPolicy(Base, UUIDMixin):
    """Reviewed thresholds that govern ranking and mandatory abstention."""

    __tablename__ = "strategy_selection_policy"

    policy_key: Mapped[str] = mapped_column(String(255), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(100), nullable=False)
    min_treated_per_strategy: Mapped[int] = mapped_column(Integer, nullable=False)
    min_control_count: Mapped[int] = mapped_column(Integer, nullable=False)
    min_spatial_blocks: Mapped[int] = mapped_column(Integer, nullable=False)
    min_effective_sample_size: Mapped[float] = mapped_column(Float, nullable=False)
    min_overlap_score: Mapped[float] = mapped_column(Float, nullable=False)
    max_weighted_smd: Mapped[float] = mapped_column(Float, nullable=False)
    min_coverage_fraction: Mapped[float] = mapped_column(Float, nullable=False)
    max_data_age: Mapped[timedelta] = mapped_column(Interval(), nullable=False)
    min_conservative_value_gain: Mapped[float] = mapped_column(Float, nullable=False)
    max_model_disagreement: Mapped[float] = mapped_column(Float, nullable=False)
    max_ood_score: Mapped[float] = mapped_column(Float, nullable=False)
    score_weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    allow_effect_claims: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    policy_checksum: Mapped[str | None] = mapped_column(String(64))
    review_state: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'draft'"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("policy_key", "policy_version", name="uq_strategy_selection_policy_version"),
        CheckConstraint(
            "min_treated_per_strategy > 0 AND min_control_count > 0 AND min_spatial_blocks > 0 "
            "AND min_effective_sample_size > 0",
            name="positive_support",
        ),
        CheckConstraint(
            "min_overlap_score BETWEEN 0 AND 1 AND min_coverage_fraction BETWEEN 0 AND 1 "
            "AND max_weighted_smd >= 0 AND max_model_disagreement >= 0 AND max_ood_score >= 0 "
            "AND max_data_age > INTERVAL '0'",
            name="valid_thresholds",
        ),
        CheckConstraint("jsonb_typeof(score_weights) = 'object'", name="score_weights_object"),
        CheckConstraint(
            "policy_checksum IS NULL OR policy_checksum ~ '^[0-9a-f]{64}$'",
            name="checksum_sha256",
        ),
        CheckConstraint("review_state IN ('draft', 'approved', 'rejected')", name="review_state"),
    )


class StrategySelectionReceipt(Base, UUIDMixin):
    """Checksum-bound ranked or abstained strategy-selection decision."""

    __tablename__ = "strategy_selection_receipt"

    selection_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    analysis_subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.analysis_subject.id"), nullable=False
    )
    forecast_receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_receipt.id")
    )
    forecast_iteration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_iteration.id")
    )
    feature_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_feature_snapshot.id"), nullable=False
    )
    training_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_training_run.id"), nullable=False
    )
    selection_policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.strategy_selection_policy.id"), nullable=False
    )
    issue_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    applicability_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    applicability_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'evaluation_only'"))
    claim_tier: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'feasibility_candidate'"))
    decision_state: Mapped[str] = mapped_column(String(24), nullable=False)
    abstention_reason: Mapped[str | None] = mapped_column(Text)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    receipt_checksum: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'staging'"))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    audit_state: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'clear'"))
    audit_reason: Mapped[str | None] = mapped_column(Text)
    audit_flagged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "(audit_state = 'clear' AND audit_reason IS NULL AND audit_flagged_at IS NULL) "
            "OR (audit_state = 'cutoff_violation' AND status = 'finalized' "
            "AND audit_reason IS NOT NULL AND audit_flagged_at IS NOT NULL)",
            name="audit_state",
        ),
        CheckConstraint(
            "(execution_mode = 'evaluation_only' AND forecast_iteration_id IS NOT NULL "
            "AND forecast_receipt_id IS NULL) OR "
            "(execution_mode = 'publishable' AND forecast_receipt_id IS NOT NULL "
            "AND forecast_iteration_id IS NULL)",
            name="forecast_source",
        ),
        CheckConstraint(
            "claim_tier IN ('feasibility_candidate', 'effect_candidate') "
            "AND (claim_tier <> 'effect_candidate' OR execution_mode = 'publishable')",
            name="claim_tier",
        ),
        CheckConstraint(
            "decision_state IN ('ranked', 'abstained') "
            "AND ((decision_state = 'ranked' AND abstention_reason IS NULL AND candidate_count > 0) "
            "OR (decision_state = 'abstained' AND abstention_reason IS NOT NULL AND candidate_count >= 0))",
            name="decision",
        ),
        CheckConstraint(
            "data_cutoff <= issue_time AND applicability_start >= issue_time "
            "AND applicability_end > applicability_start",
            name="ordered_times",
        ),
        CheckConstraint("status IN ('staging', 'finalized')", name="status"),
        Index("ix_strategy_selection_receipt_subject_issue", "analysis_subject_id", text("issue_time DESC")),
    )


class StrategySelectionCandidate(Base, UUIDMixin):
    """One auditable strategy score owned by a selection receipt."""

    __tablename__ = "strategy_selection_candidate"

    selection_receipt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.strategy_selection_receipt.id"), nullable=False
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agri.strategies.id"), nullable=False)
    strategy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    strategy_snapshot_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    eligibility_state: Mapped[str] = mapped_column(String(32), nullable=False)
    exclusion_reason: Mapped[str | None] = mapped_column(Text)
    predicted_control_outcome: Mapped[float | None] = mapped_column(Float)
    predicted_strategy_outcome: Mapped[float | None] = mapped_column(Float)
    expected_effect: Mapped[float | None] = mapped_column(Float)
    effect_lower_bound: Mapped[float | None] = mapped_column(Float)
    effect_upper_bound: Mapped[float | None] = mapped_column(Float)
    overlap_score: Mapped[float | None] = mapped_column(Float)
    effective_sample_size: Mapped[float | None] = mapped_column(Float)
    weighted_smd: Mapped[float | None] = mapped_column(Float)
    coverage_fraction: Mapped[float | None] = mapped_column(Float)
    model_disagreement: Mapped[float | None] = mapped_column(Float)
    ood_score: Mapped[float | None] = mapped_column(Float)
    conservative_score: Mapped[float | None] = mapped_column(Float)
    rank: Mapped[int | None] = mapped_column(Integer)
    evidence_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    score_components: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    candidate_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "selection_receipt_id",
            "strategy_id",
            name="uq_strategy_selection_candidate_strategy",
        ),
        UniqueConstraint(
            "selection_receipt_id",
            "rank",
            name="uq_strategy_selection_candidate_rank",
        ),
        CheckConstraint(
            "eligibility_state IN ('eligible', 'excluded', 'insufficient_evidence', 'no_effect')",
            name="eligibility",
        ),
        CheckConstraint(
            "(eligibility_state = 'eligible' AND exclusion_reason IS NULL) "
            "OR (eligibility_state <> 'eligible' AND exclusion_reason IS NOT NULL)",
            name="eligibility_reason",
        ),
        CheckConstraint(
            "(effect_lower_bound IS NULL OR expected_effect IS NULL OR effect_lower_bound <= expected_effect) "
            "AND (effect_upper_bound IS NULL OR expected_effect IS NULL OR expected_effect <= effect_upper_bound)",
            name="effect_bounds",
        ),
        CheckConstraint(
            "(overlap_score IS NULL OR overlap_score BETWEEN 0 AND 1) "
            "AND (coverage_fraction IS NULL OR coverage_fraction BETWEEN 0 AND 1) "
            "AND (effective_sample_size IS NULL OR effective_sample_size >= 0) "
            "AND (weighted_smd IS NULL OR weighted_smd >= 0) "
            "AND (model_disagreement IS NULL OR model_disagreement >= 0) "
            "AND (ood_score IS NULL OR ood_score >= 0) "
            "AND (rank IS NULL OR rank > 0)",
            name="metrics",
        ),
        CheckConstraint(
            "evidence_tier IN ('feasibility_candidate', 'effect_candidate')",
            name="evidence_tier",
        ),
        CheckConstraint(
            "jsonb_typeof(strategy_snapshot) = 'object' AND jsonb_typeof(score_components) = 'object'",
            name="json_contracts",
        ),
        CheckConstraint(
            "strategy_snapshot_checksum ~ '^[0-9a-f]{64}$' AND candidate_checksum ~ '^[0-9a-f]{64}$'",
            name="checksums",
        ),
        Index("ix_strategy_selection_candidate_receipt_score", "selection_receipt_id", text("rank ASC")),
    )
