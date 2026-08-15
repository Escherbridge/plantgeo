-- Purpose: resolve the one artifact a recommendation response is pinned to -- either the
--          caller's explicitly requested checksum or the newest receipt for that model kind --
--          together with the label release and review tier it was trained under.
-- Loaded by: agri_data_service.routes.recommendations
-- Params: model_kind (text: 'species_fit'|'strategy_selection'),
--         artifact_checksum (text, nullable: pin to this exact digest when supplied)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- Why the response is artifact-pinned rather than time-pinned: `date.today()` in a serving
-- path silently changes what is served between two identical requests. A checksum does not.
-- When the caller supplies one, this returns that artifact or nothing; when it does not, it
-- returns the newest receipt and the response echoes the digest it resolved to, so the caller
-- can pin the next request and get a byte-identical model.
--
-- Clause by clause:
--
--   artifact.content_bytes / convert_from(..., 'UTF8')
--     The model document is stored inline (agri.artifact re-verifies its sha256 and byte
--     length with CHECK constraints at write time), so serving reads the exact bytes the
--     digest covers rather than a pointer to something that may have changed.
--
--   ORDER BY receipt.completed_at DESC, receipt.training_key
--     Deterministic even when two receipts share a completion instant.
--
--   LIMIT 1
--     One artifact per response; a serving route never pages over models.
SELECT
    receipt.training_key,
    receipt.model_name,
    receipt.model_kind,
    receipt.feature_schema_version,
    receipt.label_review_tier,
    receipt.label_count,
    receipt.training_instance_count,
    receipt.source_count,
    receipt.artifact_checksum,
    receipt.evaluation_checksum,
    receipt.parameter_checksum,
    receipt.training_code_checksum,
    receipt.evaluation_metrics,
    receipt.completed_at,
    receipt.evaluation_only,
    receipt.publication_authorized,
    release.release_key AS label_release_key,
    release.review_tier AS label_release_review_tier,
    release.release_checksum AS label_release_checksum,
    release.harvest_document_checksum,
    convert_from(artifact.content_bytes, 'UTF8') AS model_document
FROM agri.recommendation_training_receipt AS receipt
JOIN agri.expert_label_release AS release
  ON release.id = receipt.label_release_id
JOIN agri.artifact AS artifact
  ON artifact.id = receipt.artifact_id
WHERE receipt.model_kind = CAST(:model_kind AS varchar)
  AND artifact.content_bytes IS NOT NULL
  AND (
        CAST(:artifact_checksum AS varchar) IS NULL
        OR receipt.artifact_checksum = CAST(:artifact_checksum AS varchar)
  )
ORDER BY receipt.completed_at DESC, receipt.training_key
LIMIT 1
