-- Purpose: record the feature matrix this training run was fitted on, bound by the covariate
--          manifest checksum, so the model's feature lineage is checkable after the fact.
-- Loaded by: agri_data_service.execution.covariate_wind_persist
-- Params: snapshot_key (text), job_run_id (uuid), release_set_id (uuid),
--         input_release_checksum (text), feature_recipe_version (text),
--         feature_code_checksum (text), feature_checksum (text),
--         training_window_start (timestamptz), training_window_end (timestamptz),
--         row_count (bigint)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- What binds what, because these four checksums are easy to confuse:
--   - input_release_checksum is the governed release set's manifest checksum, read from
--     agri.release_set. It answers "which governed inputs existed".
--   - feature_checksum is agri.covariate_vector_manifest's manifest_checksum for exactly the
--     cell, window, as-of instant and schema version this run read. It answers "which feature
--     vectors came out". This is the value that binds the feature lineage to the 0016
--     covariate plane, and it is computed by the database, not by the caller.
--   - feature_code_checksum digests the pinned feature schema itself (the feature index and
--     name of every column, in order). It answers "which recipe produced them".
--   - feature_recipe_version is the human-readable name of that same schema version.
--
-- The row is inserted in state 'draft' -- and it has no choice. A BEFORE INSERT trigger,
-- agri.require_initial_forecast_state, raises unless a forecast_feature_snapshot arrives as
-- 'draft'. The caller moves it to 'validated' afterwards by calling
-- agri.validate_forecast_feature_snapshot, which re-checks the release set, the job lineage
-- and the artifact lineage server-side before it will promote the row. Writing 'validated'
-- here would be the row certifying itself.
--
-- How this query works, clause by clause:
--
--   job_output_id is deliberately omitted, so it stays NULL
--     agri.validate_forecast_feature_snapshot only checks artifact-output lineage when this
--     column is set. It is left unset because the feature matrix here is not materialized as
--     a separate stored artifact -- it is recomputed from the covariate functions, and the
--     manifest checksum is what proves it was the same matrix. Claiming an output artifact
--     that does not exist would be worse than declaring none.
--
--   row_count column
--     The number of feature-complete days that actually entered the fit, not the number of
--     days in the requested window. A CHECK constraint requires it to be positive, so a run
--     whose exclusions emptied the matrix fails here rather than recording an empty training set.
--
--   ON CONFLICT (snapshot_key) DO NOTHING
--     ON CONFLICT is PostgreSQL's "what to do when this row would violate a unique
--     constraint". The snapshot key is derived from the pinned run identity, so re-running the
--     same shard is a no-op rather than a duplicate. Nothing is inserted on that path, so
--     RETURNING yields no row and the caller resolves the existing id with a one-line lookup.
INSERT INTO agri.forecast_feature_snapshot (
    snapshot_key,
    job_run_id,
    release_set_id,
    input_release_checksum,
    feature_recipe_version,
    feature_code_checksum,
    feature_checksum,
    training_window_start,
    training_window_end,
    row_count,
    status
)
VALUES (
    CAST(:snapshot_key AS varchar),
    CAST(:job_run_id AS uuid),
    CAST(:release_set_id AS uuid),
    CAST(:input_release_checksum AS varchar),
    CAST(:feature_recipe_version AS varchar),
    CAST(:feature_code_checksum AS varchar),
    CAST(:feature_checksum AS varchar),
    CAST(:training_window_start AS timestamptz),
    CAST(:training_window_end AS timestamptz),
    CAST(:row_count AS bigint),
    'draft'
)
ON CONFLICT (snapshot_key) DO NOTHING
RETURNING id
