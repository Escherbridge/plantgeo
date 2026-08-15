-- Purpose: record one recommendation-model training run: what label release it read, what
--          artifact digest it produced, what it measured, and the review tier those labels
--          still carry.
-- Loaded by: agri_data_service.execution.recommendation_lane
-- Params: training_key (text), model_name (text), model_kind (text:
--         'species_fit'|'strategy_selection'), label_release_id (uuid),
--         label_review_tier (text), artifact_id (uuid, nullable), job_run_id (uuid, nullable),
--         job_output_id (uuid, nullable), feature_schema_version (text),
--         label_count/training_instance_count/source_count (int), artifact_checksum (text),
--         evaluation_metrics (jsonb as text), evaluation_checksum (text),
--         parameter_checksum (text), training_code_checksum (text),
--         started_at/completed_at (timestamptz)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- evaluation_only and publication_authorized are NOT parameters. The table's CHECKs pin them
-- to true and false respectively, so the row shape itself cannot express a publishable
-- recommendation model, and this statement cannot be the one that tries. Model B in
-- particular writes here and nowhere else: it never touches agri.strategy_selection_receipt,
-- agri.strategy_selection_candidate, agri.forecast_publication or any publication pointer.
INSERT INTO agri.recommendation_training_receipt (
    training_key,
    model_name,
    model_kind,
    label_release_id,
    label_review_tier,
    artifact_id,
    job_run_id,
    job_output_id,
    feature_schema_version,
    label_count,
    training_instance_count,
    source_count,
    artifact_checksum,
    evaluation_metrics,
    evaluation_checksum,
    parameter_checksum,
    training_code_checksum,
    started_at,
    completed_at
)
VALUES (
    CAST(:training_key AS varchar),
    CAST(:model_name AS varchar),
    CAST(:model_kind AS varchar),
    CAST(:label_release_id AS uuid),
    CAST(:label_review_tier AS varchar),
    CAST(:artifact_id AS uuid),
    CAST(:job_run_id AS uuid),
    CAST(:job_output_id AS uuid),
    CAST(:feature_schema_version AS varchar),
    CAST(:label_count AS integer),
    CAST(:training_instance_count AS integer),
    CAST(:source_count AS integer),
    CAST(:artifact_checksum AS varchar),
    CAST(:evaluation_metrics AS jsonb),
    CAST(:evaluation_checksum AS varchar),
    CAST(:parameter_checksum AS varchar),
    CAST(:training_code_checksum AS varchar),
    CAST(:started_at AS timestamptz),
    CAST(:completed_at AS timestamptz)
)
ON CONFLICT (training_key) DO NOTHING
RETURNING id
