-- Purpose: the citations a served recommendation must carry -- every agent-reviewed label
--          backing each candidate subject in the pinned label release, with its DOI.
-- Loaded by: agri_data_service.routes.recommendations
-- Params: release_key (text), label_kind (text), row_limit (int)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- This is also the coverage answer. A subject absent from this result has no agent-reviewed
-- label in the pinned release, so the route answers `insufficient_labels` for it rather than
-- scoring it from nothing -- an uncited recommendation is the one output shape this surface
-- must never produce.
SELECT
    label.subject_normalized,
    label.subject,
    label.outcome,
    label.confidence,
    label.confidence_weight,
    label.harvest_slice,
    label.review_state,
    label.label_key,
    label.label_checksum,
    label.condition_envelope,
    source.doi,
    source.title,
    source.publication_year,
    source.journal_or_publisher
FROM agri.expert_label_release AS release
JOIN agri.expert_label AS label
  ON label.release_id = release.id
JOIN agri.expert_label_source AS source
  ON source.id = label.source_id
WHERE release.release_key = CAST(:release_key AS varchar)
  AND label.label_kind = CAST(:label_kind AS varchar)
  AND label.review_state IN ('agent_reviewed', 'approved')
ORDER BY label.subject_normalized, label.label_key
LIMIT CAST(:row_limit AS integer)
