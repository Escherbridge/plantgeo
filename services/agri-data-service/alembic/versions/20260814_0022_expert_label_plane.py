"""Add the expert recommendation label plane and its evaluation-only training receipts.

Revision ID: 20260814_0022
Revises: 20260814_0021

The literature label plane the ML recommendation track charters. It is deliberately
*separate* from the `20260725_0013` strategy-selection causal plane and carries **no**
foreign key into it: these are "what a trusted source recommends under these conditions"
labels, not treatment/control intervention outcomes, and `strategy_label_mapping.py`'s
custody preflight rightly refuses them. `strategy_label_episode` and
`strategy_label_release` stay empty and untouched.

Five tables:

- `expert_label_source` -- the work/edition/DOI identity a label cites.
- `expert_label_release` -- one harvest run, its document digest, its counts, and the
  review tier the whole release travels under.
- `expert_label` -- the label itself: kind, subject, CHECK-validated condition envelope,
  outcome, confidence, full citation lineage, citation-check verdict, review state.
- `expert_label_training_instance` -- the envelope mapped onto governed streams, one row
  per (label, cell, day), carrying the envelope terms our streams cannot express as an
  explicit gap rather than silently dropping them.
- `recommendation_training_receipt` -- the training receipt for Models A and B, pinned to
  a label release, an artifact digest and a job-ledger output.

Review states are `draft` -> `agent_reviewed` -> `approved`, plus `rejected`. `approved`
exists in the schema and is deliberately unreachable without an owner signature
reference: a CHECK and the review guard both demand one, and nothing in this service can
mint it. Training reads `agent_reviewed`, and every artifact, receipt and served response
carries `label_review_tier = 'agent_reviewed_pending_owner_signature'`.

`recommendation_training_receipt` carries `CHECK (evaluation_only)` and
`CHECK (NOT publication_authorized)` -- the row shape itself cannot express a publishable
recommendation model, so Model B has no representation in which it could become a
selection. It writes nothing to `strategy_selection_receipt` or any publication pointer.

On the one trigger: `guard_expert_label_review_change` is accident prevention on a new
plane, not a reinstatement of the enforcement layer `20260803_0018` retired (see the
function's own comment and `db/AGENTS.md`). A reviewed label is the evidence a trained
artifact cites by checksum; editing one in place would silently invalidate that artifact.
It fires `BEFORE INSERT OR DELETE OR UPDATE`: the INSERT branch restricts a directly
inserted row to `draft`/`rejected`, so `agent_reviewed`/`approved` are reachable only
through the guarded UPDATE transition, never by an INSERT that skips the state machine.
Paired with the two CHECKs on `expert_label` (`ck_expert_label_agent_review_not_refuted`
now also covers `approved`, and `ck_expert_label_approval_needs_owner_signature` now
demands a non-empty signature rather than merely a non-NULL one, matching the trigger's
own `coalesce(...) = ''` test), a refuted or unsigned label cannot reach `approved` by
any path -- guarded UPDATE or bare INSERT.
"""

from collections.abc import Sequence

from agri_data_service.db.sql_objects import load_object_sql
from alembic import op

revision = "20260814_0022"
down_revision = "20260814_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_EXPERT_LABEL_SOURCE = r"""
CREATE TABLE agri.expert_label_source (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    source_key character varying(255) NOT NULL,
    doi character varying(255),
    source_url character varying(2000),
    title character varying(1000) NOT NULL,
    publication_year integer NOT NULL,
    journal_or_publisher character varying(500) NOT NULL,
    edition_or_version character varying(255),
    license_posture character varying(120) NOT NULL,
    source_checksum character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_expert_label_source_checksum CHECK (source_checksum ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_expert_label_source_locator CHECK (doi IS NOT NULL OR source_url IS NOT NULL),
    CONSTRAINT ck_expert_label_source_year CHECK (publication_year >= 1800 AND publication_year <= 2100)
);

ALTER TABLE ONLY agri.expert_label_source
    ADD CONSTRAINT pk_expert_label_source PRIMARY KEY (id);

ALTER TABLE ONLY agri.expert_label_source
    ADD CONSTRAINT uq_expert_label_source_key UNIQUE (source_key);

CREATE INDEX ix_expert_label_source_doi ON agri.expert_label_source USING btree (doi) WHERE (doi IS NOT NULL);
"""

_EXPERT_LABEL_RELEASE = r"""
CREATE TABLE agri.expert_label_release (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    release_key character varying(255) NOT NULL,
    harvest_document_uri character varying(2000) NOT NULL,
    harvest_document_checksum character varying(64) NOT NULL,
    harvested_at timestamp with time zone NOT NULL,
    label_count integer NOT NULL,
    draft_count integer NOT NULL,
    agent_reviewed_count integer NOT NULL,
    approved_count integer DEFAULT 0 NOT NULL,
    rejected_count integer NOT NULL,
    slice_summary jsonb NOT NULL,
    review_tier character varying(64) DEFAULT 'agent_reviewed_pending_owner_signature'::character varying NOT NULL,
    owner_signature_reference character varying(500),
    owner_signed_at timestamp with time zone,
    release_checksum character varying(64) NOT NULL,
    loader_code_checksum character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_expert_label_release_checksums CHECK (
        harvest_document_checksum ~ '^[0-9a-f]{64}$'
        AND release_checksum ~ '^[0-9a-f]{64}$'
        AND loader_code_checksum ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_expert_label_release_counts CHECK (
        label_count >= 0
        AND draft_count >= 0
        AND agent_reviewed_count >= 0
        AND approved_count >= 0
        AND rejected_count >= 0
        AND label_count = draft_count + agent_reviewed_count + approved_count + rejected_count
    ),
    CONSTRAINT ck_expert_label_release_owner_signature CHECK (
        review_tier <> 'owner_signed'
        OR (owner_signature_reference IS NOT NULL AND owner_signed_at IS NOT NULL)
    ),
    CONSTRAINT ck_expert_label_release_slice_summary CHECK (jsonb_typeof(slice_summary) = 'object'),
    CONSTRAINT ck_expert_label_release_tier CHECK (
        (review_tier)::text = ANY ((ARRAY[
            'draft'::character varying,
            'agent_reviewed_pending_owner_signature'::character varying,
            'owner_signed'::character varying
        ])::text[])
    )
);

ALTER TABLE ONLY agri.expert_label_release
    ADD CONSTRAINT pk_expert_label_release PRIMARY KEY (id);

ALTER TABLE ONLY agri.expert_label_release
    ADD CONSTRAINT uq_expert_label_release_key UNIQUE (release_key);
"""

_EXPERT_LABEL = r"""
CREATE TABLE agri.expert_label (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    label_key character varying(255) NOT NULL,
    release_id uuid NOT NULL,
    source_id uuid NOT NULL,
    label_kind character varying(32) NOT NULL,
    subject character varying(255) NOT NULL,
    subject_normalized character varying(255) NOT NULL,
    outcome character varying(24) NOT NULL,
    condition_envelope jsonb NOT NULL,
    envelope_checksum character varying(64) NOT NULL,
    rationale text NOT NULL,
    supporting_quote text,
    confidence character varying(16) NOT NULL,
    confidence_weight double precision NOT NULL,
    harvest_slice character varying(120) NOT NULL,
    citation_check_refuted boolean NOT NULL,
    citation_check_doi_resolves boolean NOT NULL,
    citation_check_reason text NOT NULL,
    review_state character varying(24) DEFAULT 'draft'::character varying NOT NULL,
    review_note text,
    reviewed_by character varying(255),
    reviewed_at timestamp with time zone,
    owner_signature_reference character varying(500),
    label_checksum character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_expert_label_agent_review_not_refuted CHECK (
        ((review_state)::text NOT IN ('agent_reviewed', 'approved')) OR (citation_check_refuted = false)
    ),
    CONSTRAINT ck_expert_label_approval_needs_owner_signature CHECK (
        ((review_state)::text <> 'approved') OR (coalesce(owner_signature_reference, '') <> '')
    ),
    CONSTRAINT ck_expert_label_checksums CHECK (
        label_checksum ~ '^[0-9a-f]{64}$' AND envelope_checksum ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_expert_label_confidence CHECK (
        (confidence)::text = ANY ((ARRAY[
            'high'::character varying, 'medium'::character varying, 'low'::character varying
        ])::text[])
        AND confidence_weight >= 0.0 AND confidence_weight <= 1.0
    ),
    CONSTRAINT ck_expert_label_envelope CHECK (agri.expert_label_envelope_valid(condition_envelope)),
    CONSTRAINT ck_expert_label_kind CHECK (
        (label_kind)::text = ANY ((ARRAY[
            'species_fit'::character varying,
            'strategy_outcome'::character varying
        ])::text[])
    ),
    CONSTRAINT ck_expert_label_outcome_matches_kind CHECK (
        ((label_kind)::text = 'species_fit'::text
            AND (outcome)::text = ANY ((ARRAY[
                'fit'::character varying, 'marginal'::character varying, 'unfit'::character varying
            ])::text[]))
        OR ((label_kind)::text = 'strategy_outcome'::text
            AND (outcome)::text = ANY ((ARRAY[
                'effective'::character varying, 'mixed'::character varying, 'ineffective'::character varying
            ])::text[]))
    ),
    CONSTRAINT ck_expert_label_quote_bounded CHECK (
        supporting_quote IS NULL
        OR (char_length(supporting_quote) >= 1 AND char_length(supporting_quote) <= 1000)
    ),
    CONSTRAINT ck_expert_label_review_evidence CHECK (
        (review_state)::text = 'draft'::text OR (reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL)
    ),
    CONSTRAINT ck_expert_label_review_state CHECK (
        (review_state)::text = ANY ((ARRAY[
            'draft'::character varying,
            'agent_reviewed'::character varying,
            'approved'::character varying,
            'rejected'::character varying
        ])::text[])
    )
);

ALTER TABLE ONLY agri.expert_label
    ADD CONSTRAINT pk_expert_label PRIMARY KEY (id);

ALTER TABLE ONLY agri.expert_label
    ADD CONSTRAINT uq_expert_label_key UNIQUE (label_key);

CREATE INDEX ix_expert_label_release_state ON agri.expert_label USING btree (release_id, review_state);

CREATE INDEX ix_expert_label_trainable ON agri.expert_label USING btree (review_state, label_kind, subject_normalized);
"""

_EXPERT_LABEL_TRAINING_INSTANCE = r"""
CREATE TABLE agri.expert_label_training_instance (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    label_id uuid NOT NULL,
    release_id uuid NOT NULL,
    spatial_cell_id uuid NOT NULL,
    observed_date date NOT NULL,
    as_of_time timestamp with time zone NOT NULL,
    feature_schema_version character varying(64) NOT NULL,
    feature_values jsonb NOT NULL,
    feature_checksum character varying(64) NOT NULL,
    envelope_match jsonb NOT NULL,
    unexpressible_terms jsonb NOT NULL,
    match_state character varying(24) NOT NULL,
    instance_checksum character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_expert_label_training_instance_checksums CHECK (
        feature_checksum ~ '^[0-9a-f]{64}$' AND instance_checksum ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_expert_label_training_instance_json CHECK (
        jsonb_typeof(feature_values) = 'array'
        AND jsonb_typeof(envelope_match) = 'object'
        AND jsonb_typeof(unexpressible_terms) = 'array'
    ),
    CONSTRAINT ck_expert_label_training_instance_state CHECK (
        (match_state)::text = ANY ((ARRAY[
            'matched'::character varying,
            'excluded'::character varying,
            'unexpressible'::character varying
        ])::text[])
    )
);

ALTER TABLE ONLY agri.expert_label_training_instance
    ADD CONSTRAINT pk_expert_label_training_instance PRIMARY KEY (id);

ALTER TABLE ONLY agri.expert_label_training_instance
    ADD CONSTRAINT uq_expert_label_training_instance_identity
        UNIQUE (label_id, spatial_cell_id, observed_date, feature_schema_version);

CREATE INDEX ix_expert_label_training_instance_release_state
    ON agri.expert_label_training_instance USING btree (release_id, match_state, observed_date);
"""

_RECOMMENDATION_TRAINING_RECEIPT = r"""
CREATE TABLE agri.recommendation_training_receipt (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    training_key character varying(255) NOT NULL,
    model_name character varying(120) NOT NULL,
    model_kind character varying(32) NOT NULL,
    label_release_id uuid NOT NULL,
    label_review_tier character varying(64) NOT NULL,
    artifact_id uuid,
    job_run_id uuid,
    job_output_id uuid,
    feature_schema_version character varying(64) NOT NULL,
    label_count integer NOT NULL,
    training_instance_count integer NOT NULL,
    source_count integer NOT NULL,
    artifact_checksum character varying(64) NOT NULL,
    evaluation_metrics jsonb NOT NULL,
    evaluation_checksum character varying(64) NOT NULL,
    parameter_checksum character varying(64) NOT NULL,
    training_code_checksum character varying(64) NOT NULL,
    evaluation_only boolean DEFAULT true NOT NULL,
    publication_authorized boolean DEFAULT false NOT NULL,
    started_at timestamp with time zone NOT NULL,
    completed_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_recommendation_training_receipt_checksums CHECK (
        artifact_checksum ~ '^[0-9a-f]{64}$'
        AND evaluation_checksum ~ '^[0-9a-f]{64}$'
        AND parameter_checksum ~ '^[0-9a-f]{64}$'
        AND training_code_checksum ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_recommendation_training_receipt_counts CHECK (
        label_count >= 0 AND training_instance_count >= 0 AND source_count >= 0
    ),
    CONSTRAINT ck_recommendation_training_receipt_evaluation_only CHECK (evaluation_only),
    CONSTRAINT ck_recommendation_training_receipt_kind CHECK (
        (model_kind)::text = ANY ((ARRAY[
            'species_fit'::character varying,
            'strategy_selection'::character varying
        ])::text[])
    ),
    CONSTRAINT ck_recommendation_training_receipt_metrics CHECK (jsonb_typeof(evaluation_metrics) = 'object'),
    CONSTRAINT ck_recommendation_training_receipt_ordered CHECK (completed_at >= started_at),
    CONSTRAINT ck_recommendation_training_receipt_review_tier CHECK (
        (label_review_tier)::text = ANY ((ARRAY[
            'draft'::character varying,
            'agent_reviewed_pending_owner_signature'::character varying,
            'owner_signed'::character varying
        ])::text[])
    ),
    CONSTRAINT ck_recommendation_training_receipt_unpublishable CHECK (NOT publication_authorized)
);

ALTER TABLE ONLY agri.recommendation_training_receipt
    ADD CONSTRAINT pk_recommendation_training_receipt PRIMARY KEY (id);

ALTER TABLE ONLY agri.recommendation_training_receipt
    ADD CONSTRAINT uq_recommendation_training_receipt_key UNIQUE (training_key);

CREATE INDEX ix_recommendation_training_receipt_model
    ON agri.recommendation_training_receipt USING btree (model_kind, completed_at DESC);
"""

_FOREIGN_KEYS = r"""
ALTER TABLE ONLY agri.expert_label
    ADD CONSTRAINT fk_expert_label_release FOREIGN KEY (release_id)
        REFERENCES agri.expert_label_release(id);

ALTER TABLE ONLY agri.expert_label
    ADD CONSTRAINT fk_expert_label_source FOREIGN KEY (source_id)
        REFERENCES agri.expert_label_source(id);

ALTER TABLE ONLY agri.expert_label_training_instance
    ADD CONSTRAINT fk_expert_label_training_instance_label FOREIGN KEY (label_id)
        REFERENCES agri.expert_label(id);

ALTER TABLE ONLY agri.expert_label_training_instance
    ADD CONSTRAINT fk_expert_label_training_instance_release FOREIGN KEY (release_id)
        REFERENCES agri.expert_label_release(id);

ALTER TABLE ONLY agri.expert_label_training_instance
    ADD CONSTRAINT fk_expert_label_training_instance_spatial_cell FOREIGN KEY (spatial_cell_id)
        REFERENCES agri.spatial_cell(id);

ALTER TABLE ONLY agri.recommendation_training_receipt
    ADD CONSTRAINT fk_recommendation_training_receipt_artifact FOREIGN KEY (artifact_id)
        REFERENCES agri.artifact(id);

ALTER TABLE ONLY agri.recommendation_training_receipt
    ADD CONSTRAINT fk_recommendation_training_receipt_job_output FOREIGN KEY (job_output_id)
        REFERENCES agri.job_output(id);

ALTER TABLE ONLY agri.recommendation_training_receipt
    ADD CONSTRAINT fk_recommendation_training_receipt_job_run FOREIGN KEY (job_run_id)
        REFERENCES agri.job_run(id);

ALTER TABLE ONLY agri.recommendation_training_receipt
    ADD CONSTRAINT fk_recommendation_training_receipt_release FOREIGN KEY (label_release_id)
        REFERENCES agri.expert_label_release(id);
"""

_TRIGGER = r"""
CREATE TRIGGER expert_label_review_guard
    BEFORE INSERT OR DELETE OR UPDATE ON agri.expert_label
    FOR EACH ROW EXECUTE FUNCTION agri.guard_expert_label_review_change();
"""


def upgrade() -> None:
    op.execute(load_object_sql("functions/expert_label_envelope_valid.sql"))
    op.execute(_EXPERT_LABEL_SOURCE)
    op.execute(_EXPERT_LABEL_RELEASE)
    op.execute(_EXPERT_LABEL)
    op.execute(_EXPERT_LABEL_TRAINING_INSTANCE)
    op.execute(_RECOMMENDATION_TRAINING_RECEIPT)
    op.execute(_FOREIGN_KEYS)
    op.execute(load_object_sql("functions/expert_label_release_summary.sql"))
    op.execute(load_object_sql("functions/guard_expert_label_review_change.sql"))
    op.execute(_TRIGGER)
    op.execute(
        r"""
        REVOKE EXECUTE ON FUNCTION agri.expert_label_envelope_valid(jsonb) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.expert_label_release_summary(varchar) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.guard_expert_label_review_change() FROM PUBLIC;
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Harvested literature labels, their citation-check verdicts and the receipts that "
        "cite them are review evidence; restore a verified backup into a fresh database."
    )
