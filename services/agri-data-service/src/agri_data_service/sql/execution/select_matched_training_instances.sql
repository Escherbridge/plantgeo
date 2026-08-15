-- Purpose: read back the matched training instances of one release and label kind, joined to
--          the label and its citation source, as the exact rows a fit consumes.
-- Loaded by: agri_data_service.execution.recommendation_lane
-- Params: release_key (text), label_kind (text), feature_schema_version (text), row_limit (int)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- Two predicates carry the governance: match_state = 'matched' (an excluded or unexpressible
-- day is recorded evidence, not training signal) and review_state IN ('agent_reviewed',
-- 'approved') (a draft or refuted label is never trainable). The DOI travels with every row so
-- the fit can group by source for leave-one-source-out cross-validation and so every scored
-- recommendation can cite what it was learned from.
SELECT
    instance.id AS instance_id,
    instance.observed_date,
    instance.feature_values,
    instance.feature_checksum,
    instance.envelope_match,
    instance.unexpressible_terms,
    label.id AS label_id,
    label.label_key,
    label.subject,
    label.subject_normalized,
    label.outcome,
    label.condition_envelope,
    label.confidence,
    label.confidence_weight,
    label.harvest_slice,
    label.review_state,
    label.label_checksum,
    source.doi,
    source.title,
    source.publication_year,
    source.journal_or_publisher,
    source.source_key
FROM agri.expert_label_release AS release
JOIN agri.expert_label AS label
  ON label.release_id = release.id
JOIN agri.expert_label_source AS source
  ON source.id = label.source_id
JOIN agri.expert_label_training_instance AS instance
  ON instance.label_id = label.id
 AND instance.feature_schema_version = CAST(:feature_schema_version AS varchar)
 AND instance.match_state = 'matched'
WHERE release.release_key = CAST(:release_key AS varchar)
  AND label.label_kind = CAST(:label_kind AS varchar)
  AND label.review_state IN ('agent_reviewed', 'approved')
ORDER BY label.label_key, instance.observed_date
LIMIT CAST(:row_limit AS integer)
