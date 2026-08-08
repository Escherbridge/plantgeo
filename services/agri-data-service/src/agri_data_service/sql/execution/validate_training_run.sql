-- Purpose: ask the database to promote one gated training receipt to 'validated', which it
--          does only after re-deriving the whole lineage server-side.
-- Loaded by: agri_data_service.execution.covariate_wind_persist
-- Params: training_run_id (uuid), model_checksum (text), validation_checksum (text),
--         validation_metrics (jsonb as text)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- This is the governance gate for the whole ML receipt chain, and it is deliberately not
-- something the application can talk its way past. agri.validate_forecast_training_run
-- (db/agri/functions/validate_forecast_training_run.sql) locks the receipt and raises unless
-- ALL of the following hold: both checksums look like SHA-256 digests; the receipt is in a
-- state eligible for validation; the model is kind 'ml'; a metric-forecast receipt binds no
-- strategy label release; the feature snapshot is 'validated' and its input-release and
-- feature checksums equal the receipt's; the training job succeeded, has a completion time
-- and shares the snapshot's release set; the job output belongs to that job, is validated,
-- points at the model's artifact, has checksum equal to the model checksum passed here,
-- carries a matching 'validation_checksum' in its metadata and a row count of exactly 1; and
-- the model artifact's own digest equals the model checksum. Only then does it write the
-- validated status, the two checksums, the metrics and the timestamps.
--
-- How this query works, clause by clause:
--
--   (agri.validate_forecast_training_run(...)).status
--     The function returns a whole forecast_training_run row -- a composite value. Wrapping
--     the call in parentheses and appending .status selects one field out of that composite,
--     which is how PostgreSQL reaches into a row-typed expression. Only the status is needed
--     here; the caller already holds every other value it wrote.
--
--   CAST(... AS jsonb)
--     validation_metrics is sent as text. The driver has no jsonb type of its own, so without
--     the cast the function would be handed an untyped placeholder for a jsonb argument and
--     the call could not be resolved. Note that re-running this function with metrics that
--     DIFFER from an already-validated receipt's raises rather than overwriting -- which is
--     why the caller derives the metrics deterministically from pinned inputs.
SELECT (
    agri.validate_forecast_training_run(
        CAST(:training_run_id AS uuid),
        CAST(:model_checksum AS varchar),
        CAST(:validation_checksum AS varchar),
        CAST(:validation_metrics AS jsonb)
    )
).status AS status
