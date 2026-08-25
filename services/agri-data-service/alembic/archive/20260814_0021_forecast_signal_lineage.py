"""Add the evaluation-only derived-signal lineage plane and candidate-evaluation receipts.

Five additive relations, two immutable checksum functions, one recursive lineage audit and one
feature-snapshot integration gate. Nothing here touches the operational run, receipt, publication or
serving planes, and nothing here reinstates a retired `verify_*`/`guard_*`/`enforce_*` state machine:
the DAG is enforced **declaratively**, by composite foreign keys plus CHECK constraints, and the only
triggers added are the surviving `guard_forecast_immutable_rows` append-only rule.

Why declarative rather than a cycle-detecting trigger. Every edge carries its child's and parent's
own `lineage_depth`, tied to the real rows by a composite foreign key, and
`ck_forecast_signal_lineage_edge_depth_step` requires `child_depth = parent_depth + 1`. A cycle
`v1 -> v2 -> ... -> vk -> v1` would then require `depth(v1) = depth(v1) + k` with `k > 0`, which no
row can satisfy. `ck_forecast_signal_lineage_edge_parent_cutoff_earlier` forbids it a second time by
imposing a strict total order on origin cutoffs along any path. Constraints cannot be disabled by the
table owner the way the 2026-08-03 audit showed triggers can, and they are checked on every path
including `COPY`, so this is strictly stronger than the trigger the plan first imagined.

`agri.forecast_signal_lineage_audit` is the recursive-CTE traversal that *verifies* the property from
the stored rows rather than asserting it; the tests use it, and its Python twin
(`method/ml/seasonal_lineage_graph.py`) re-derives the same verdict from rows read back out.

Revision ID: 20260814_0021
Revises: 20260814_0020
"""

from collections.abc import Sequence

from agri_data_service.db.sql_objects import load_object_sql
from alembic import op

revision = "20260814_0021"
down_revision = "20260814_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HEX64 = "^[0-9a-f]{64}$"

_SIGNAL_DEFINITION = f"""
CREATE TABLE agri.forecast_signal_definition (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    signal_key character varying(150) NOT NULL,
    signal_version character varying(50) NOT NULL,
    unit character varying(50) NOT NULL,
    spatial_support_key character varying(100) NOT NULL,
    temporal_grain interval NOT NULL,
    recipe_key character varying(150) NOT NULL,
    recipe_checksum character varying(64) NOT NULL,
    parent_schema jsonb DEFAULT '[]'::jsonb NOT NULL,
    max_dependency_depth integer NOT NULL,
    evaluation_only boolean DEFAULT true NOT NULL,
    publication_authorized boolean DEFAULT false NOT NULL,
    definition_checksum character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_forecast_signal_definition_checksums
        CHECK (recipe_checksum::text ~ '{_HEX64}' AND definition_checksum::text ~ '{_HEX64}'),
    CONSTRAINT ck_forecast_signal_definition_depth_bound
        CHECK (max_dependency_depth >= 1 AND max_dependency_depth <= 8),
    CONSTRAINT ck_forecast_signal_definition_evaluation_only CHECK (evaluation_only),
    CONSTRAINT ck_forecast_signal_definition_never_published CHECK (NOT publication_authorized),
    CONSTRAINT ck_forecast_signal_definition_parent_schema_is_array
        CHECK (jsonb_typeof(parent_schema) = 'array'),
    CONSTRAINT ck_forecast_signal_definition_positive_grain CHECK (temporal_grain > interval '0')
);
ALTER TABLE ONLY agri.forecast_signal_definition
    ADD CONSTRAINT pk_forecast_signal_definition PRIMARY KEY (id);
ALTER TABLE ONLY agri.forecast_signal_definition
    ADD CONSTRAINT uq_forecast_signal_definition_identity UNIQUE (signal_key, signal_version);
ALTER TABLE ONLY agri.forecast_signal_definition
    ADD CONSTRAINT uq_forecast_signal_definition_depth_bound UNIQUE (id, max_dependency_depth);
"""

_DERIVED_SIGNAL_VALUE = f"""
CREATE TABLE agri.forecast_derived_signal_value (
    id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
    signal_definition_id uuid NOT NULL,
    max_dependency_depth integer NOT NULL,
    series_key character varying(255) NOT NULL,
    series_id uuid,
    lineage_depth integer NOT NULL,
    origin_cutoff_time timestamp with time zone NOT NULL,
    valid_time timestamp with time zone NOT NULL,
    availability_time timestamp with time zone NOT NULL,
    signal_value double precision,
    known_missing_inputs jsonb DEFAULT '[]'::jsonb NOT NULL,
    input_release_checksum character varying(64) NOT NULL,
    recipe_checksum character varying(64) NOT NULL,
    value_checksum character varying(64) NOT NULL,
    evaluation_only boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_forecast_derived_signal_value_available_after_origin
        CHECK (availability_time >= origin_cutoff_time),
    CONSTRAINT ck_forecast_derived_signal_value_checksums
        CHECK (input_release_checksum::text ~ '{_HEX64}'
               AND recipe_checksum::text ~ '{_HEX64}'
               AND value_checksum::text ~ '{_HEX64}'),
    CONSTRAINT ck_forecast_derived_signal_value_depth_bound
        CHECK (lineage_depth >= 0 AND lineage_depth <= max_dependency_depth),
    CONSTRAINT ck_forecast_derived_signal_value_evaluation_only CHECK (evaluation_only),
    CONSTRAINT ck_forecast_derived_signal_value_known_missing_is_array
        CHECK (jsonb_typeof(known_missing_inputs) = 'array'),
    CONSTRAINT ck_forecast_derived_signal_value_reproducible_checksum
        CHECK (value_checksum::text = agri.forecast_derived_signal_value_checksum(
            signal_definition_id,
            series_key,
            origin_cutoff_time,
            valid_time,
            availability_time,
            lineage_depth,
            signal_value,
            input_release_checksum,
            recipe_checksum
        )::text)
);
ALTER TABLE ONLY agri.forecast_derived_signal_value
    ADD CONSTRAINT pk_forecast_derived_signal_value PRIMARY KEY (id);
ALTER TABLE ONLY agri.forecast_derived_signal_value
    ADD CONSTRAINT uq_forecast_derived_signal_value_identity
    UNIQUE (signal_definition_id, series_key, valid_time, origin_cutoff_time);
ALTER TABLE ONLY agri.forecast_derived_signal_value
    ADD CONSTRAINT uq_forecast_derived_signal_value_lineage_facts
    UNIQUE (id, origin_cutoff_time, valid_time, availability_time, lineage_depth);
CREATE INDEX ix_forecast_derived_signal_value_series_valid
    ON agri.forecast_derived_signal_value USING btree (series_key, valid_time);
CREATE INDEX ix_forecast_derived_signal_value_availability
    ON agri.forecast_derived_signal_value USING btree (availability_time);
"""

_LINEAGE_EDGE = """
CREATE TABLE agri.forecast_signal_lineage_edge (
    id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
    child_value_id bigint NOT NULL,
    child_origin_cutoff_time timestamp with time zone NOT NULL,
    child_valid_time timestamp with time zone NOT NULL,
    child_availability_time timestamp with time zone NOT NULL,
    child_lineage_depth integer NOT NULL,
    parent_value_id bigint NOT NULL,
    parent_origin_cutoff_time timestamp with time zone NOT NULL,
    parent_valid_time timestamp with time zone NOT NULL,
    parent_availability_time timestamp with time zone NOT NULL,
    parent_lineage_depth integer NOT NULL,
    parent_role character varying(50) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_forecast_signal_lineage_edge_no_self_reference
        CHECK (child_value_id <> parent_value_id),
    CONSTRAINT ck_forecast_signal_lineage_edge_parent_cutoff_earlier
        CHECK (parent_origin_cutoff_time < child_origin_cutoff_time),
    CONSTRAINT ck_forecast_signal_lineage_edge_parent_valid_time_earlier
        CHECK (parent_valid_time < child_origin_cutoff_time),
    CONSTRAINT ck_forecast_lineage_edge_parent_available_at_child_origin
        CHECK (parent_availability_time <= child_origin_cutoff_time),
    -- Overlaps the constraint above but is not implied by it: a parent that becomes available after
    -- the child's origin yet before the child's own availability breaks only that one, while a
    -- parent later than the child's availability breaks only this one.
    CONSTRAINT ck_forecast_lineage_edge_child_not_available_before_parent
        CHECK (child_availability_time >= parent_availability_time),
    CONSTRAINT ck_forecast_signal_lineage_edge_depth_step
        CHECK (child_lineage_depth = parent_lineage_depth + 1),
    CONSTRAINT ck_forecast_signal_lineage_edge_parent_role
        CHECK (parent_role::text = ANY (ARRAY['feedback_parent'::character varying,
                                              'base_forecast'::character varying,
                                              'actual_linkage'::character varying]::text[]))
);
ALTER TABLE ONLY agri.forecast_signal_lineage_edge
    ADD CONSTRAINT pk_forecast_signal_lineage_edge PRIMARY KEY (id);
ALTER TABLE ONLY agri.forecast_signal_lineage_edge
    ADD CONSTRAINT uq_forecast_signal_lineage_edge_pair UNIQUE (child_value_id, parent_value_id);
CREATE INDEX ix_forecast_signal_lineage_edge_parent
    ON agri.forecast_signal_lineage_edge USING btree (parent_value_id);
"""

_CANDIDATE_EVALUATION = f"""
CREATE TABLE agri.forecast_candidate_evaluation (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    evaluation_key character varying(200) NOT NULL,
    series_key character varying(255) NOT NULL,
    series_id uuid,
    signal_definition_id uuid,
    candidate_family character varying(100) NOT NULL,
    candidate_version character varying(50) NOT NULL,
    hyperparameters jsonb DEFAULT '{{}}'::jsonb NOT NULL,
    simulation_seed bigint NOT NULL,
    export_manifest_checksum character varying(64) NOT NULL,
    horizon_steps integer NOT NULL,
    development_origin_count integer NOT NULL,
    final_holdout_origin_count integer NOT NULL,
    metrics jsonb DEFAULT '{{}}'::jsonb NOT NULL,
    decision character varying(16) NOT NULL,
    decision_reason text NOT NULL,
    evaluation_only boolean DEFAULT true NOT NULL,
    publication_authorized boolean DEFAULT false NOT NULL,
    receipt_checksum character varying(64) NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_forecast_candidate_evaluation_checksums
        CHECK (export_manifest_checksum::text ~ '{_HEX64}' AND receipt_checksum::text ~ '{_HEX64}'),
    CONSTRAINT ck_forecast_candidate_evaluation_decision
        CHECK (decision::text = ANY (ARRAY['accept'::character varying,
                                           'reject'::character varying,
                                           'abstain'::character varying,
                                           'baseline'::character varying]::text[])),
    CONSTRAINT ck_forecast_candidate_evaluation_evaluation_only CHECK (evaluation_only),
    CONSTRAINT ck_forecast_candidate_evaluation_never_published CHECK (NOT publication_authorized),
    CONSTRAINT ck_forecast_candidate_evaluation_nonnegative_origins
        CHECK (development_origin_count >= 0 AND final_holdout_origin_count >= 0),
    CONSTRAINT ck_forecast_candidate_evaluation_positive_horizon CHECK (horizon_steps > 0),
    CONSTRAINT ck_forecast_candidate_evaluation_reproducible_receipt
        CHECK (receipt_checksum::text = agri.forecast_candidate_evaluation_receipt_checksum(
            evaluation_key,
            series_key,
            candidate_family,
            candidate_version,
            hyperparameters,
            simulation_seed,
            export_manifest_checksum,
            horizon_steps,
            development_origin_count,
            final_holdout_origin_count,
            decision
        )::text)
);
ALTER TABLE ONLY agri.forecast_candidate_evaluation
    ADD CONSTRAINT pk_forecast_candidate_evaluation PRIMARY KEY (id);
ALTER TABLE ONLY agri.forecast_candidate_evaluation
    ADD CONSTRAINT uq_forecast_candidate_evaluation_key UNIQUE (evaluation_key);
CREATE INDEX ix_forecast_candidate_evaluation_series
    ON agri.forecast_candidate_evaluation USING btree (series_key, candidate_family);
"""

_CANDIDATE_EVALUATION_ORIGIN = f"""
CREATE TABLE agri.forecast_candidate_evaluation_origin (
    id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
    evaluation_id uuid NOT NULL,
    origin_cutoff_time timestamp with time zone NOT NULL,
    fold_kind character varying(20) NOT NULL,
    scored_step_count integer NOT NULL,
    mean_absolute_error double precision NOT NULL,
    root_mean_squared_error double precision NOT NULL,
    bias double precision NOT NULL,
    interval_coverage_fraction double precision,
    skill_versus_persistence double precision,
    origin_checksum character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_forecast_candidate_evaluation_origin_checksum
        CHECK (origin_checksum::text ~ '{_HEX64}'),
    CONSTRAINT ck_forecast_candidate_evaluation_origin_coverage_fraction
        CHECK (interval_coverage_fraction IS NULL
               OR (interval_coverage_fraction >= 0.0 AND interval_coverage_fraction <= 1.0)),
    CONSTRAINT ck_forecast_candidate_evaluation_origin_fold_kind
        CHECK (fold_kind::text = ANY (ARRAY['development'::character varying,
                                            'final_holdout'::character varying]::text[])),
    CONSTRAINT ck_forecast_candidate_evaluation_origin_nonnegative_errors
        CHECK (mean_absolute_error >= 0.0 AND root_mean_squared_error >= 0.0),
    CONSTRAINT ck_forecast_candidate_evaluation_origin_positive_steps CHECK (scored_step_count > 0)
);
ALTER TABLE ONLY agri.forecast_candidate_evaluation_origin
    ADD CONSTRAINT pk_forecast_candidate_evaluation_origin PRIMARY KEY (id);
ALTER TABLE ONLY agri.forecast_candidate_evaluation_origin
    ADD CONSTRAINT uq_forecast_candidate_evaluation_origin_identity
    UNIQUE (evaluation_id, origin_cutoff_time);
"""

_FOREIGN_KEYS = """
ALTER TABLE ONLY agri.forecast_derived_signal_value
    ADD CONSTRAINT fk_forecast_derived_signal_value_definition
    FOREIGN KEY (signal_definition_id, max_dependency_depth)
    REFERENCES agri.forecast_signal_definition(id, max_dependency_depth);
ALTER TABLE ONLY agri.forecast_derived_signal_value
    ADD CONSTRAINT fk_forecast_derived_signal_value_series
    FOREIGN KEY (series_id) REFERENCES agri.forecast_series(id);
ALTER TABLE ONLY agri.forecast_signal_lineage_edge
    ADD CONSTRAINT fk_forecast_signal_lineage_edge_child
    FOREIGN KEY (child_value_id, child_origin_cutoff_time, child_valid_time,
                 child_availability_time, child_lineage_depth)
    REFERENCES agri.forecast_derived_signal_value(id, origin_cutoff_time, valid_time,
                                                  availability_time, lineage_depth);
ALTER TABLE ONLY agri.forecast_signal_lineage_edge
    ADD CONSTRAINT fk_forecast_signal_lineage_edge_parent
    FOREIGN KEY (parent_value_id, parent_origin_cutoff_time, parent_valid_time,
                 parent_availability_time, parent_lineage_depth)
    REFERENCES agri.forecast_derived_signal_value(id, origin_cutoff_time, valid_time,
                                                  availability_time, lineage_depth);
ALTER TABLE ONLY agri.forecast_candidate_evaluation
    ADD CONSTRAINT fk_forecast_candidate_evaluation_series
    FOREIGN KEY (series_id) REFERENCES agri.forecast_series(id);
ALTER TABLE ONLY agri.forecast_candidate_evaluation
    ADD CONSTRAINT fk_forecast_candidate_evaluation_definition
    FOREIGN KEY (signal_definition_id) REFERENCES agri.forecast_signal_definition(id);
ALTER TABLE ONLY agri.forecast_candidate_evaluation_origin
    ADD CONSTRAINT fk_forecast_candidate_evaluation_origin_evaluation
    FOREIGN KEY (evaluation_id) REFERENCES agri.forecast_candidate_evaluation(id);
"""

_IMMUTABILITY_TRIGGERS = (
    "forecast_signal_definition",
    "forecast_derived_signal_value",
    "forecast_signal_lineage_edge",
    "forecast_candidate_evaluation",
    "forecast_candidate_evaluation_origin",
)


def upgrade() -> None:
    op.execute(load_object_sql("functions/forecast_derived_signal_value_checksum.sql"))
    op.execute(load_object_sql("functions/forecast_candidate_evaluation_receipt_checksum.sql"))

    op.execute(_SIGNAL_DEFINITION)
    op.execute(_DERIVED_SIGNAL_VALUE)
    op.execute(_LINEAGE_EDGE)
    op.execute(_CANDIDATE_EVALUATION)
    op.execute(_CANDIDATE_EVALUATION_ORIGIN)
    op.execute(_FOREIGN_KEYS)

    op.execute(load_object_sql("functions/forecast_signal_lineage_audit.sql"))
    op.execute(load_object_sql("functions/forecast_derived_signal_snapshot_eligible.sql"))

    for table in _IMMUTABILITY_TRIGGERS:
        op.execute(load_object_sql(f"triggers/{table}.sql"))

    # Object hardening, not role management: the 2026-08-08 ruling retired the role family, and
    # `REVOKE ... FROM PUBLIC` on a new callable stays mandatory regardless.
    op.execute("REVOKE EXECUTE ON FUNCTION agri.forecast_signal_lineage_audit(bigint) FROM PUBLIC;")
    op.execute("REVOKE EXECUTE ON FUNCTION agri.forecast_derived_signal_snapshot_eligible(uuid) FROM PUBLIC;")


def downgrade() -> None:
    """No downgrade: derived-signal values and candidate receipts are append-only evidence.

    Reversing this revision would delete evaluation evidence rather than restore a prior state.
    Roll back by restoring a verified backup into a fresh database, as every data-bearing revision
    in this tree does.
    """
    raise NotImplementedError("20260814_0021 is forward-only; restore a verified backup instead")
