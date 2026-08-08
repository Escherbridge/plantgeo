-- Purpose: open the training receipt for one covariate wind fit, in the gated state the
--          schema requires, so the validator can promote it afterwards.
-- Loaded by: agri_data_service.execution.covariate_wind_persist
-- Params: training_key (text), model_id (uuid), job_run_id (uuid), job_output_id (uuid),
--         feature_snapshot_id (uuid), input_release_checksum (text),
--         feature_checksum (text), training_code_checksum (text),
--         started_at (timestamptz), completed_at (timestamptz)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- This is the row the whole ML receipt chain exists for, and until now nothing outside the
-- test suite has ever inserted one.
--
-- The row is inserted as 'gated' -- and it has no choice. A BEFORE INSERT trigger,
-- agri.require_initial_forecast_state, raises unless a forecast_training_run arrives as
-- 'gated'. It is moved to 'validated' only by agri.validate_forecast_training_run, which
-- re-derives every lineage check server-side: that the feature snapshot is validated and its
-- two checksums match this row's, that the training job succeeded and shares the snapshot's
-- release set, that the job output's checksum equals the model checksum and its metadata
-- carries the matching validation checksum, and that the model's artifact digest agrees. The
-- application cannot talk its way past any of that, which is the point.
--
-- strategy_label_release_id and strategy_label_checksum are deliberately left NULL. The
-- validator raises "metric-forecast training cannot bind a strategy label release" if either
-- is set on a metric-forecast model, and this model has no intervention labels behind it.
--
-- How this query works, clause by clause:
--
--   input_release_checksum / feature_checksum
--     Copied from the feature snapshot rather than recomputed. The validator compares this
--     row's pair against the snapshot's pair and raises on any difference, so they are a
--     restatement whose only job is to be checkable.
--
--   training_code_checksum column
--     Digest of the training module's own source, so a receipt names the code that produced
--     it. It is written once here; the validator does not inspect it, but nothing else records
--     which implementation ran.
--
--   ON CONFLICT (training_key) DO NOTHING
--     ON CONFLICT is PostgreSQL's "what to do when this row would violate a unique
--     constraint". The training key is derived from the pinned run identity -- cell, origins,
--     as-of instant and parameter digest -- so a durable lane re-claiming a shard whose work
--     already committed resolves to the receipt that exists instead of raising. Nothing is
--     inserted on that path, so RETURNING yields no row and the caller resolves the existing
--     id with a one-line lookup.
INSERT INTO agri.forecast_training_run (
    training_key,
    model_id,
    job_run_id,
    job_output_id,
    feature_snapshot_id,
    execution_mode,
    status,
    input_release_checksum,
    feature_checksum,
    training_code_checksum,
    started_at,
    completed_at
)
VALUES (
    CAST(:training_key AS varchar),
    CAST(:model_id AS uuid),
    CAST(:job_run_id AS uuid),
    CAST(:job_output_id AS uuid),
    CAST(:feature_snapshot_id AS uuid),
    'local',
    'gated',
    CAST(:input_release_checksum AS varchar),
    CAST(:feature_checksum AS varchar),
    CAST(:training_code_checksum AS varchar),
    CAST(:started_at AS timestamptz),
    CAST(:completed_at AS timestamptz)
)
ON CONFLICT (training_key) DO NOTHING
RETURNING id
