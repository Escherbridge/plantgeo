"""Add governed intervention labels and strategy-selection receipts.

Revision ID: 20260725_0013
Revises: 20260725_0012

Five function bodies are embedded here rather than loaded from ``db/agri/``
because ``20260803_0018`` drops those objects, and the declarative tree is
regenerated from a dump of *head* — so the canonical files no longer exist and
``load_object_sql`` could not replay this revision. Each embedded body is the
text ``load_object_sql`` returned immediately before the move, so the applied
DDL is unchanged; the objects that survive at head keep loading from the tree.
A migration whose object a later revision drops must carry its own DDL. See
``db/AGENTS.md``.
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


_GUARD_STRATEGY_CHILD_INSERT = r"""
CREATE FUNCTION agri.guard_strategy_child_insert() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            parent_status varchar;
        BEGIN
            IF TG_TABLE_NAME = 'strategy_label_episode' THEN
                SELECT status INTO parent_status
                  FROM agri.strategy_label_release
                 WHERE id = NEW.label_release_id
                 FOR UPDATE;
                IF parent_status IS DISTINCT FROM 'staging' THEN
                    RAISE EXCEPTION 'strategy label episodes require a staging label release';
                END IF;
            ELSIF TG_TABLE_NAME = 'strategy_selection_candidate' THEN
                SELECT status INTO parent_status
                  FROM agri.strategy_selection_receipt
                 WHERE id = NEW.selection_receipt_id
                 FOR UPDATE;
                IF parent_status IS DISTINCT FROM 'staging' THEN
                    RAISE EXCEPTION 'strategy selection candidates require a staging receipt';
                END IF;
            ELSE
                RAISE EXCEPTION 'unsupported strategy child-insert trigger table: %', TG_TABLE_NAME;
            END IF;
            RETURN NEW;
        END
    $$;
"""

_GUARD_STRATEGY_LABEL_RELEASE_CHANGE = r"""
CREATE FUNCTION agri.guard_strategy_label_release_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $_$
        BEGIN
            IF TG_OP = 'DELETE'
               OR OLD.status <> 'staging'
               OR NEW.status <> 'validated'
               OR ROW(
                    NEW.id,
                    NEW.release_key,
                    NEW.release_set_id,
                    NEW.outcome_definition_id,
                    NEW.as_of_time,
                    NEW.strategy_taxonomy_snapshot,
                    NEW.strategy_taxonomy_checksum,
                    NEW.feature_schema,
                    NEW.feature_schema_checksum,
                    NEW.extraction_plan_checksum,
                    NEW.extraction_code_checksum,
                    NEW.spatial_block_scheme,
                    NEW.row_count,
                    NEW.treated_count,
                    NEW.control_count,
                    NEW.strategy_count,
                    NEW.spatial_block_count,
                    NEW.validation_summary,
                    NEW.created_at
               ) IS DISTINCT FROM ROW(
                    OLD.id,
                    OLD.release_key,
                    OLD.release_set_id,
                    OLD.outcome_definition_id,
                    OLD.as_of_time,
                    OLD.strategy_taxonomy_snapshot,
                    OLD.strategy_taxonomy_checksum,
                    OLD.feature_schema,
                    OLD.feature_schema_checksum,
                    OLD.extraction_plan_checksum,
                    OLD.extraction_code_checksum,
                    OLD.spatial_block_scheme,
                    OLD.row_count,
                    OLD.treated_count,
                    OLD.control_count,
                    OLD.strategy_count,
                    OLD.spatial_block_count,
                    OLD.validation_summary,
                    OLD.created_at
               )
               OR NEW.receipt_checksum !~ '^[0-9a-f]{64}$'
               OR NEW.validated_at IS NULL THEN
                RAISE EXCEPTION 'only verified staging-to-validated label release transition is allowed';
            END IF;
            RETURN NEW;
        END
    $_$;
"""

# The next three bodies are also embedded, with OR REPLACE, in 20260801_0014.
_FINALIZE_STRATEGY_LABEL_RELEASE = r"""
CREATE FUNCTION agri.finalize_strategy_label_release(p_label_release_id uuid, p_expected_checksum character varying) RETURNS agri.strategy_label_release
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $_$
        DECLARE
            label agri.strategy_label_release;
            pinned_release agri.release_set;
            outcome agri.strategy_outcome_definition;
            actual_row_count bigint;
            actual_treated_count bigint;
            actual_control_count bigint;
            actual_strategy_count integer;
            actual_spatial_block_count integer;
            distinct_subject_count bigint;
            invalid_episode_count bigint;
            invalid_cohort_count bigint;
            missing_taxonomy_count bigint;
            computed_checksum varchar;
        BEGIN
            IF p_expected_checksum IS NULL OR p_expected_checksum !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'strategy label release checksum must be SHA-256';
            END IF;

            SELECT * INTO label
              FROM agri.strategy_label_release
             WHERE id = p_label_release_id
             FOR UPDATE;
            IF NOT FOUND OR label.status NOT IN ('staging', 'validated') THEN
                RAISE EXCEPTION 'strategy label release is missing or not eligible for finalization';
            END IF;

            SELECT * INTO pinned_release
              FROM agri.release_set
             WHERE id = label.release_set_id;
            SELECT * INTO outcome
              FROM agri.strategy_outcome_definition
             WHERE id = label.outcome_definition_id;
            IF pinned_release.state NOT IN ('validated', 'published')
               OR pinned_release.validated_at IS NULL
               OR pinned_release.manifest_checksum IS NULL THEN
                RAISE EXCEPTION 'strategy label release requires a validated pinned release set';
            END IF;
            IF outcome.review_state <> 'approved' THEN
                RAISE EXCEPTION 'strategy label release requires an approved outcome definition';
            END IF;
            IF outcome.definition_checksum IS DISTINCT FROM
                agri.strategy_outcome_definition_checksum(outcome) THEN
                RAISE EXCEPTION 'strategy label release outcome definition checksum mismatch';
            END IF;
            IF label.strategy_taxonomy_checksum <> encode(
                digest(label.strategy_taxonomy_snapshot::text, 'sha256'),
                'hex'
            ) THEN
                RAISE EXCEPTION 'strategy label release taxonomy snapshot checksum mismatch';
            END IF;
            IF label.feature_schema_checksum <> encode(
                digest(label.feature_schema::text, 'sha256'),
                'hex'
            )
               OR jsonb_typeof(label.feature_schema) <> 'array'
               OR jsonb_array_length(label.feature_schema) = 0
               OR EXISTS (
                    SELECT 1
                      FROM jsonb_array_elements(label.feature_schema) AS feature(value)
                     WHERE jsonb_typeof(feature.value) <> 'string'
                        OR btrim(feature.value #>> '{}') = ''
               )
               OR (
                    SELECT count(*) <> count(DISTINCT feature.value #>> '{}')
                      FROM jsonb_array_elements(label.feature_schema) AS feature(value)
               ) THEN
                RAISE EXCEPTION 'strategy label release feature schema is invalid or checksum-mismatched';
            END IF;

            SELECT
                count(*),
                count(*) FILTER (WHERE episode.arm_kind = 'treatment'),
                count(*) FILTER (WHERE episode.arm_kind = 'control'),
                count(DISTINCT episode.strategy_id) FILTER (WHERE episode.strategy_id IS NOT NULL),
                count(DISTINCT episode.spatial_block_key),
                count(DISTINCT episode.analysis_subject_id),
                count(*) FILTER (
                    WHERE baseline.analysis_subject_id <> episode.analysis_subject_id
                       OR observed.analysis_subject_id <> episode.analysis_subject_id
                       OR baseline.release_set_id <> label.release_set_id
                       OR observed.release_set_id <> label.release_set_id
                       OR baseline.evidence_kind <> 'observed_fact'
                       OR observed.evidence_kind <> 'observed_fact'
                       OR baseline.metric_name <> outcome.metric_name
                       OR observed.metric_name <> outcome.metric_name
                       OR baseline.numeric_value IS NULL
                       OR observed.numeric_value IS NULL
                       OR baseline.value_unit IS DISTINCT FROM outcome.metric_unit
                       OR observed.value_unit IS DISTINCT FROM outcome.metric_unit
                       OR episode.target_unit <> outcome.metric_unit
                       OR episode.covariate_checksum <> encode(
                            digest(episode.covariate_snapshot::text, 'sha256'),
                            'hex'
                       )
                       OR jsonb_typeof(episode.covariate_snapshot) <> 'array'
                       OR jsonb_array_length(episode.covariate_snapshot) <>
                            jsonb_array_length(label.feature_schema)
                       OR EXISTS (
                            SELECT 1
                              FROM jsonb_array_elements(episode.covariate_snapshot) AS covariate(value)
                             WHERE jsonb_typeof(covariate.value) <> 'number'
                       )
                       OR episode.covariates_available_at > episode.assigned_at
                       OR baseline.data_available_at > label.as_of_time
                       OR observed.data_available_at > label.as_of_time
                       OR episode.data_available_at > label.as_of_time
                       OR episode.outcome_end > episode.data_available_at
                       OR baseline.observed_from IS NULL
                       OR baseline.observed_to IS NULL
                       OR observed.observed_from IS NULL
                       OR observed.observed_to IS NULL
                       OR baseline.observed_from > episode.baseline_start
                       OR baseline.observed_to < episode.baseline_end
                       OR observed.observed_from > episode.outcome_start
                       OR observed.observed_to < episode.outcome_end
                       OR episode.episode_checksum IS DISTINCT FROM
                            agri.strategy_label_episode_checksum(episode.id)
                       OR episode.baseline_end - episode.baseline_start <> outcome.baseline_window
                       OR episode.outcome_end - episode.outcome_start <> outcome.outcome_window
                       OR episode.target_value IS DISTINCT FROM CASE outcome.benefit_direction
                            WHEN 'increase' THEN observed.numeric_value - baseline.numeric_value
                            WHEN 'decrease' THEN baseline.numeric_value - observed.numeric_value
                       END
                )
              INTO
                actual_row_count,
                actual_treated_count,
                actual_control_count,
                actual_strategy_count,
                actual_spatial_block_count,
                distinct_subject_count,
                invalid_episode_count
              FROM agri.strategy_label_episode AS episode
              INNER JOIN agri.intervention_evidence_input AS baseline
                ON baseline.id = episode.baseline_evidence_input_id
              INNER JOIN agri.intervention_evidence_input AS observed
                ON observed.id = episode.outcome_evidence_input_id
             WHERE episode.label_release_id = label.id;

            SELECT count(*) INTO missing_taxonomy_count
              FROM agri.strategy_label_episode AS episode
             WHERE episode.label_release_id = label.id
               AND episode.arm_kind = 'treatment'
               AND NOT EXISTS (
                    SELECT 1
                      FROM jsonb_array_elements(label.strategy_taxonomy_snapshot) AS item
                     WHERE item ->> 'strategy_id' = episode.strategy_id::text
               );

            SELECT count(*) INTO invalid_cohort_count
              FROM (
                    SELECT episode.cohort_key
                      FROM agri.strategy_label_episode AS episode
                     WHERE episode.label_release_id = label.id
                     GROUP BY episode.cohort_key
                    HAVING count(DISTINCT episode.assigned_at) <> 1
              ) AS invalid_cohort;

            IF actual_row_count <> label.row_count
               OR actual_treated_count <> label.treated_count
               OR actual_control_count <> label.control_count
               OR actual_strategy_count <> label.strategy_count
               OR actual_spatial_block_count <> label.spatial_block_count
               OR distinct_subject_count <> label.row_count THEN
                RAISE EXCEPTION 'strategy label release declared counts do not match persisted episodes';
            END IF;
            IF invalid_episode_count > 0 THEN
                RAISE EXCEPTION 'strategy label release contains lineage, feature, availability, window, metric, or unit mismatch';
            END IF;
            IF missing_taxonomy_count > 0 THEN
                RAISE EXCEPTION 'strategy label release treatment is absent from the pinned taxonomy snapshot';
            END IF;
            IF invalid_cohort_count > 0 THEN
                RAISE EXCEPTION 'strategy label release cohort maps to multiple assignment times';
            END IF;

            computed_checksum := agri.strategy_label_release_checksum(label.id);
            IF computed_checksum IS DISTINCT FROM p_expected_checksum THEN
                RAISE EXCEPTION 'strategy label release checksum mismatch';
            END IF;

            IF label.status = 'staging' THEN
                UPDATE agri.strategy_label_release
                   SET status = 'validated',
                       receipt_checksum = computed_checksum,
                       validated_at = now()
                 WHERE id = label.id
                 RETURNING * INTO label;
            ELSIF label.receipt_checksum IS DISTINCT FROM computed_checksum THEN
                RAISE EXCEPTION 'validated strategy label release no longer matches its receipt';
            END IF;
            RETURN label;
        END
    $_$;
"""  # noqa: E501

_GUARD_STRATEGY_SELECTION_RECEIPT_CHANGE = r"""
CREATE FUNCTION agri.guard_strategy_selection_receipt_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $_$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'only verified staging-to-finalized strategy selection transition is allowed';
            END IF;
            IF OLD.status = 'finalized' THEN
                -- The only write a finalized receipt accepts is a one-way audit flag; the
                -- receipt, its checksum, and its lineage stay byte-identical.
                IF NEW.status <> 'finalized'
                   OR OLD.audit_state <> 'clear'
                   OR NEW.audit_state <> 'cutoff_violation'
                   OR NEW.audit_reason IS NULL
                   OR NEW.audit_flagged_at IS NULL
                   OR ROW(
                        NEW.id,
                        NEW.selection_key,
                        NEW.analysis_subject_id,
                        NEW.forecast_receipt_id,
                        NEW.forecast_iteration_id,
                        NEW.feature_snapshot_id,
                        NEW.training_run_id,
                        NEW.selection_policy_id,
                        NEW.issue_time,
                        NEW.applicability_start,
                        NEW.applicability_end,
                        NEW.data_cutoff,
                        NEW.execution_mode,
                        NEW.claim_tier,
                        NEW.decision_state,
                        NEW.abstention_reason,
                        NEW.candidate_count,
                        NEW.receipt_checksum,
                        NEW.finalized_at,
                        NEW.created_at
                   ) IS DISTINCT FROM ROW(
                        OLD.id,
                        OLD.selection_key,
                        OLD.analysis_subject_id,
                        OLD.forecast_receipt_id,
                        OLD.forecast_iteration_id,
                        OLD.feature_snapshot_id,
                        OLD.training_run_id,
                        OLD.selection_policy_id,
                        OLD.issue_time,
                        OLD.applicability_start,
                        OLD.applicability_end,
                        OLD.data_cutoff,
                        OLD.execution_mode,
                        OLD.claim_tier,
                        OLD.decision_state,
                        OLD.abstention_reason,
                        OLD.candidate_count,
                        OLD.receipt_checksum,
                        OLD.finalized_at,
                        OLD.created_at
                   ) THEN
                    RAISE EXCEPTION
                        'a finalized strategy selection accepts only a one-way cutoff_violation audit flag';
                END IF;
                RETURN NEW;
            END IF;
            IF OLD.status <> 'staging'
               OR NEW.status <> 'finalized'
               OR ROW(
                    NEW.id,
                    NEW.selection_key,
                    NEW.analysis_subject_id,
                    NEW.forecast_receipt_id,
                    NEW.forecast_iteration_id,
                    NEW.feature_snapshot_id,
                    NEW.training_run_id,
                    NEW.selection_policy_id,
                    NEW.issue_time,
                    NEW.applicability_start,
                    NEW.applicability_end,
                    NEW.data_cutoff,
                    NEW.execution_mode,
                    NEW.claim_tier,
                    NEW.decision_state,
                    NEW.abstention_reason,
                    NEW.candidate_count,
                    NEW.audit_state,
                    NEW.audit_reason,
                    NEW.audit_flagged_at,
                    NEW.created_at
               ) IS DISTINCT FROM ROW(
                    OLD.id,
                    OLD.selection_key,
                    OLD.analysis_subject_id,
                    OLD.forecast_receipt_id,
                    OLD.forecast_iteration_id,
                    OLD.feature_snapshot_id,
                    OLD.training_run_id,
                    OLD.selection_policy_id,
                    OLD.issue_time,
                    OLD.applicability_start,
                    OLD.applicability_end,
                    OLD.data_cutoff,
                    OLD.execution_mode,
                    OLD.claim_tier,
                    OLD.decision_state,
                    OLD.abstention_reason,
                    OLD.candidate_count,
                    OLD.audit_state,
                    OLD.audit_reason,
                    OLD.audit_flagged_at,
                    OLD.created_at
               )
               OR NEW.receipt_checksum !~ '^[0-9a-f]{64}$'
               OR NEW.finalized_at IS NULL THEN
                RAISE EXCEPTION 'only verified staging-to-finalized strategy selection transition is allowed';
            END IF;
            RETURN NEW;
        END
    $_$;
"""

_FINALIZE_STRATEGY_SELECTION_RECEIPT = r"""
CREATE FUNCTION agri.finalize_strategy_selection_receipt(p_selection_receipt_id uuid, p_expected_checksum character varying) RETURNS agri.strategy_selection_receipt
    LANGUAGE plpgsql
    SET "TimeZone" TO 'UTC'
    SET "DateStyle" TO 'ISO, MDY'
    SET "IntervalStyle" TO 'postgres'
    SET extra_float_digits TO '1'
    SET search_path TO 'public', 'pg_catalog'
    AS $_$
        DECLARE
            receipt agri.strategy_selection_receipt;
            training agri.forecast_training_run;
            model agri.forecast_model;
            feature agri.forecast_feature_snapshot;
            label agri.strategy_label_release;
            policy agri.strategy_selection_policy;
            actual_candidate_count integer;
            ranked_candidate_count integer;
            minimum_rank integer;
            maximum_rank integer;
            invalid_candidate_count integer;
            computed_checksum varchar;
        BEGIN
            IF p_expected_checksum IS NULL OR p_expected_checksum !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'strategy selection receipt checksum must be SHA-256';
            END IF;

            SELECT * INTO receipt
              FROM agri.strategy_selection_receipt
             WHERE id = p_selection_receipt_id
             FOR UPDATE;
            IF NOT FOUND OR receipt.status NOT IN ('staging', 'finalized') THEN
                RAISE EXCEPTION 'strategy selection receipt is missing or not eligible for finalization';
            END IF;
            IF receipt.audit_state <> 'clear' THEN
                RAISE EXCEPTION
                    'strategy selection receipt is flagged % and cannot be finalized', receipt.audit_state;
            END IF;

            SELECT * INTO training
              FROM agri.forecast_training_run
             WHERE id = receipt.training_run_id;
            SELECT * INTO model
              FROM agri.forecast_model
             WHERE id = training.model_id;
            SELECT * INTO feature
              FROM agri.forecast_feature_snapshot
             WHERE id = receipt.feature_snapshot_id;
            SELECT * INTO label
              FROM agri.strategy_label_release
             WHERE id = training.strategy_label_release_id;
            SELECT * INTO policy
              FROM agri.strategy_selection_policy
             WHERE id = receipt.selection_policy_id;

            IF training.status <> 'validated'
               OR model.model_purpose <> 'strategy_selection'
               OR training.feature_snapshot_id <> receipt.feature_snapshot_id
               OR training.strategy_label_release_id IS NULL
               OR training.strategy_label_checksum IS DISTINCT FROM label.receipt_checksum
               OR feature.status <> 'validated'
               OR feature.job_output_id IS NULL
               OR label.status <> 'validated'
               OR policy.review_state <> 'approved' THEN
                RAISE EXCEPTION 'strategy selection lineage is not validated and policy-approved';
            END IF;
            IF policy.policy_checksum IS DISTINCT FROM
                agri.strategy_selection_policy_checksum(policy) THEN
                RAISE EXCEPTION 'strategy selection policy checksum mismatch';
            END IF;
            IF label.as_of_time > receipt.data_cutoff
               OR feature.training_window_end > receipt.data_cutoff THEN
                RAISE EXCEPTION 'strategy selection inputs became available after the declared cutoff';
            END IF;

            IF receipt.execution_mode = 'evaluation_only' THEN
                IF NOT EXISTS (
                    SELECT 1
                      FROM agri.forecast_iteration AS iteration
                     WHERE iteration.id = receipt.forecast_iteration_id
                       AND iteration.status = 'finalized'
                       AND iteration.as_of_time = receipt.issue_time
                ) THEN
                    RAISE EXCEPTION 'evaluation-only strategy selection requires its finalized forecast iteration';
                END IF;
                IF agri.strategy_selection_cutoff_violation(receipt.id) THEN
                    RAISE EXCEPTION
                        'strategy selection forecast iteration cutoff is later than the declared data cutoff';
                END IF;
            ELSIF NOT EXISTS (
                SELECT 1
                  FROM agri.forecast_receipt AS forecast
                  INNER JOIN agri.forecast_publication_item AS item
                    ON item.forecast_receipt_id = forecast.id
                  INNER JOIN agri.forecast_publication AS publication
                    ON publication.id = item.publication_id
                 WHERE forecast.id = receipt.forecast_receipt_id
                   AND forecast.status = 'finalized'
                   AND forecast.issue_time = receipt.issue_time
                   AND publication.state = 'published'
            ) THEN
                RAISE EXCEPTION 'publishable strategy selection requires a published finalized forecast receipt';
            END IF;

            IF NOT agri.strategy_selection_quality_evidence(receipt.id) THEN
                IF receipt.execution_mode = 'evaluation_only' THEN
                    RAISE EXCEPTION
                        'strategy selection requires a finalized quality-passed hindcast for its backing series';
                ELSE
                    RAISE EXCEPTION
                        'strategy selection requires a finalized quality-passed hindcast for its backing series and model';
                END IF;
            END IF;

            SELECT
                count(*),
                count(*) FILTER (WHERE candidate.rank IS NOT NULL),
                min(candidate.rank),
                max(candidate.rank),
                count(*) FILTER (
                    WHERE candidate.strategy_snapshot ->> 'strategy_id' IS DISTINCT FROM candidate.strategy_id::text
                       OR candidate.strategy_snapshot_checksum <> encode(
                            digest(candidate.strategy_snapshot::text, 'sha256'),
                            'hex'
                       )
                       OR candidate.candidate_checksum IS DISTINCT FROM
                            agri.strategy_selection_candidate_checksum(candidate.id)
                       OR (candidate.rank IS NOT NULL AND candidate.eligibility_state <> 'eligible')
                )
              INTO
                actual_candidate_count,
                ranked_candidate_count,
                minimum_rank,
                maximum_rank,
                invalid_candidate_count
              FROM agri.strategy_selection_candidate AS candidate
             WHERE candidate.selection_receipt_id = receipt.id;

            IF actual_candidate_count <> receipt.candidate_count THEN
                RAISE EXCEPTION 'strategy selection candidate count mismatch';
            END IF;
            IF invalid_candidate_count > 0 THEN
                RAISE EXCEPTION 'strategy selection candidate snapshot, checksum, eligibility, or rank is invalid';
            END IF;
            IF receipt.decision_state = 'ranked'
               AND (
                    ranked_candidate_count < 1
                    OR minimum_rank <> 1
                    OR maximum_rank <> ranked_candidate_count
               ) THEN
                RAISE EXCEPTION 'ranked strategy selection requires contiguous eligible ranks';
            END IF;
            IF receipt.decision_state = 'abstained' AND ranked_candidate_count <> 0 THEN
                RAISE EXCEPTION 'abstained strategy selection cannot retain ranked candidates';
            END IF;
            IF receipt.claim_tier = 'feasibility_candidate'
               AND EXISTS (
                    SELECT 1
                      FROM agri.strategy_selection_candidate
                     WHERE selection_receipt_id = receipt.id
                       AND evidence_tier <> 'feasibility_candidate'
               ) THEN
                RAISE EXCEPTION 'feasibility receipt cannot contain effect-tier candidates';
            END IF;

            IF receipt.claim_tier = 'effect_candidate' THEN
                -- A later revision must add cluster-bootstrap, placebo,
                -- negative-control, and best-vs-second lower-bound gates.
                RAISE EXCEPTION
                    'effect_candidate finalization is disabled in strategy_selection_v1';
            END IF;

            computed_checksum := agri.strategy_selection_receipt_checksum(receipt.id);
            IF computed_checksum IS DISTINCT FROM p_expected_checksum THEN
                RAISE EXCEPTION 'strategy selection receipt checksum mismatch';
            END IF;

            IF receipt.status = 'staging' THEN
                UPDATE agri.strategy_selection_receipt
                   SET status = 'finalized',
                       receipt_checksum = computed_checksum,
                       finalized_at = now()
                 WHERE id = receipt.id
                 RETURNING * INTO receipt;
            ELSIF receipt.receipt_checksum IS DISTINCT FROM computed_checksum THEN
                RAISE EXCEPTION 'finalized strategy selection no longer matches its receipt';
            END IF;
            RETURN receipt;
        END
    $_$;
"""  # noqa: E501


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
            "(arm_kind = 'treatment' AND strategy_id IS NOT NULL) OR (arm_kind = 'control' AND strategy_id IS NULL)",
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
            "status <> 'finalized' OR (receipt_checksum ~ '^[0-9a-f]{64}$' AND finalized_at IS NOT NULL)",
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
            "jsonb_typeof(strategy_snapshot) = 'object' AND jsonb_typeof(score_components) = 'object'",
            name=op.f("ck_strategy_selection_candidate_json_contracts"),
        ),
        sa.CheckConstraint(
            "strategy_snapshot_checksum ~ '^[0-9a-f]{64}$' AND candidate_checksum ~ '^[0-9a-f]{64}$'",
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

    for function_sql in (
        load_object_sql("functions/strategy_outcome_definition_checksum.sql"),
        load_object_sql("functions/strategy_selection_policy_checksum.sql"),
        load_object_sql("functions/strategy_label_release_checksum.sql"),
        load_object_sql("functions/strategy_label_episode_checksum.sql"),
        load_object_sql("functions/require_strategy_initial_state.sql"),
        _GUARD_STRATEGY_CHILD_INSERT,
        load_object_sql("functions/guard_strategy_review_change.sql"),
        _GUARD_STRATEGY_LABEL_RELEASE_CHANGE,
        _FINALIZE_STRATEGY_LABEL_RELEASE,
        load_object_sql("functions/export_strategy_label_bundle.sql"),
        load_object_sql("functions/strategy_label_bundle_checksum.sql"),
        load_object_sql("functions/strategy_selection_candidate_checksum.sql"),
        load_object_sql("functions/strategy_selection_receipt_checksum.sql"),
        _GUARD_STRATEGY_SELECTION_RECEIPT_CHANGE,
        _FINALIZE_STRATEGY_SELECTION_RECEIPT,
    ):
        op.execute(function_sql)

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
