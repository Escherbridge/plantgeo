-- Purpose: load one harvested literature label as `draft`, with its full citation lineage
--          and the adversarial citation-check verdict that decides whether it may advance.
-- Loaded by: agri_data_service.execution.recommendation_lane
-- Params: label_key (text), release_id (uuid), source_id (uuid),
--         label_kind (text: 'species_fit'|'strategy_outcome'), subject (text),
--         subject_normalized (text), outcome (text), condition_envelope (jsonb as text),
--         envelope_checksum (text), rationale (text), supporting_quote (text, nullable),
--         confidence (text: 'high'|'medium'|'low'), confidence_weight (float),
--         harvest_slice (text), citation_check_refuted (bool),
--         citation_check_doi_resolves (bool), citation_check_reason (text),
--         label_checksum (text)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- review_state is not a parameter. Every label enters `draft` and nothing may load one in any
-- other state; advancing to `agent_reviewed` is a separate statement that carries a reviewer
-- and a timestamp, and `approved` is unreachable without an owner signature reference.
-- Writing the state here would let a loader bug mint a trainable label in one step.
INSERT INTO agri.expert_label (
    label_key,
    release_id,
    source_id,
    label_kind,
    subject,
    subject_normalized,
    outcome,
    condition_envelope,
    envelope_checksum,
    rationale,
    supporting_quote,
    confidence,
    confidence_weight,
    harvest_slice,
    citation_check_refuted,
    citation_check_doi_resolves,
    citation_check_reason,
    review_state,
    label_checksum
)
VALUES (
    CAST(:label_key AS varchar),
    CAST(:release_id AS uuid),
    CAST(:source_id AS uuid),
    CAST(:label_kind AS varchar),
    CAST(:subject AS varchar),
    CAST(:subject_normalized AS varchar),
    CAST(:outcome AS varchar),
    CAST(:condition_envelope AS jsonb),
    CAST(:envelope_checksum AS varchar),
    CAST(:rationale AS text),
    CAST(:supporting_quote AS text),
    CAST(:confidence AS varchar),
    CAST(:confidence_weight AS double precision),
    CAST(:harvest_slice AS varchar),
    CAST(:citation_check_refuted AS boolean),
    CAST(:citation_check_doi_resolves AS boolean),
    CAST(:citation_check_reason AS text),
    'draft',
    CAST(:label_checksum AS varchar)
)
ON CONFLICT (label_key) DO NOTHING
RETURNING id
