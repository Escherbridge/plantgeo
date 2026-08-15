-- Purpose: read the agent-reviewed labels of one release, with their citation lineage, as the
--          only rows a model is allowed to train on.
-- Loaded by: agri_data_service.execution.recommendation_lane
-- Params: release_key (text), label_kind (text: 'species_fit'|'strategy_outcome'),
--         row_limit (int)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- The review_state predicate is the trainability gate and is written here rather than left to
-- the caller: a draft label has not been citation-checked into evidence and a refuted one
-- never will be, so neither may reach a fit. 'approved' is included because an
-- owner-countersigned label is strictly stronger evidence than an agent-reviewed one -- but
-- no row can hold that state without an owner signature reference, so today this predicate
-- returns exactly the agent-reviewed set.
--
-- citation_check_refuted = false is redundant with ck_expert_label_agent_review_not_refuted and
-- the guard_expert_label_review_change trigger under the schema as currently constrained -- both
-- already refuse a refuted row 'agent_reviewed'/'approved' status, by INSERT or UPDATE. It is kept
-- here anyway as defense in depth: this is the one query a model actually trains from, and it must
-- not depend solely on a constraint elsewhere never being loosened or bypassed.
SELECT
    label.id AS label_id,
    label.label_key,
    label.label_kind,
    label.subject,
    label.subject_normalized,
    label.outcome,
    label.condition_envelope,
    label.envelope_checksum,
    label.confidence,
    label.confidence_weight,
    label.harvest_slice,
    label.review_state,
    label.label_checksum,
    source.id AS source_id,
    source.source_key,
    source.doi,
    source.title,
    source.publication_year,
    source.journal_or_publisher,
    release.id AS release_id,
    release.release_key,
    release.review_tier
FROM agri.expert_label_release AS release
JOIN agri.expert_label AS label
  ON label.release_id = release.id
JOIN agri.expert_label_source AS source
  ON source.id = label.source_id
WHERE release.release_key = CAST(:release_key AS varchar)
  AND label.label_kind = CAST(:label_kind AS varchar)
  AND label.review_state IN ('agent_reviewed', 'approved')
  AND label.citation_check_refuted = false
ORDER BY label.label_key
LIMIT CAST(:row_limit AS integer)
