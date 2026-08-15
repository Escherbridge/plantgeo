-- Purpose: record one label harvest as a release: the document it came from, its counts by
--          review state, and the review tier the whole release travels under.
-- Loaded by: agri_data_service.execution.recommendation_lane
-- Params: release_key (text), harvest_document_uri (text),
--         harvest_document_checksum (text: sha256 of the harvest file bytes),
--         harvested_at (timestamptz), label_count/draft_count/agent_reviewed_count/
--         approved_count/rejected_count (int), slice_summary (jsonb as text),
--         review_tier (text), release_checksum (text), loader_code_checksum (text)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- approved_count is bound rather than defaulted so the CHECK that the four state counts sum
-- to label_count is exercised by the caller's own arithmetic. It is 0 for every release this
-- service can produce: approval requires the owner's signature and nothing here mints one.
INSERT INTO agri.expert_label_release (
    release_key,
    harvest_document_uri,
    harvest_document_checksum,
    harvested_at,
    label_count,
    draft_count,
    agent_reviewed_count,
    approved_count,
    rejected_count,
    slice_summary,
    review_tier,
    release_checksum,
    loader_code_checksum
)
VALUES (
    CAST(:release_key AS varchar),
    CAST(:harvest_document_uri AS varchar),
    CAST(:harvest_document_checksum AS varchar),
    CAST(:harvested_at AS timestamptz),
    CAST(:label_count AS integer),
    CAST(:draft_count AS integer),
    CAST(:agent_reviewed_count AS integer),
    CAST(:approved_count AS integer),
    CAST(:rejected_count AS integer),
    CAST(:slice_summary AS jsonb),
    CAST(:review_tier AS varchar),
    CAST(:release_checksum AS varchar),
    CAST(:loader_code_checksum AS varchar)
)
ON CONFLICT (release_key) DO NOTHING
RETURNING id
