-- Purpose: open the staged forecast run that the rolling-origin backtest metrics hang from.
-- Loaded by: agri_data_service.execution.covariate_wind_persist
-- Params: run_key (text), job_run_id (uuid), feature_snapshot_id (uuid), model_id (uuid),
--         training_run_id (uuid), quality_policy_id (uuid), issue_time (timestamptz),
--         valid_from (timestamptz), valid_to (timestamptz), horizon_steps (int),
--         input_release_checksum (text), feature_checksum (text), model_checksum (text),
--         parameter_checksum (text), quality_summary (jsonb as text)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- Why this row exists at all: agri.forecast_backtest_metric requires a forecast_run_id, so
-- there is no way to record a holdout score without one. It is NOT a forecast anyone may
-- read.
--
-- IT STAYS 'staged' FOREVER, DELIBERATELY. A BEFORE INSERT trigger,
-- agri.require_initial_forecast_state, requires the row to arrive as 'staged'; only
-- agri.validate_forecast_run promotes it, and this lane never calls that function. Nothing
-- downstream can therefore pick these rows up: the serving view is built from published
-- forecast receipts and forecast values, and this lane writes neither. That is the structural
-- guarantee that an evaluation-only backtest cannot leak onto a serving surface -- it does not
-- rest on a WHERE clause anyone could forget.
--
-- backtest_passed is left at its server default of false for the same reason: passing is a
-- verdict against a quality policy, and this lane records measurements, not verdicts.
--
-- How this query works, clause by clause:
--
--   forecast_method = 'ml' with training_run_id set
--     A CHECK constraint ties the two together: an 'ml' run must name a training run and a
--     'sql_linear' run must not. Naming the training run is what makes these metrics
--     attributable to a specific fitted model rather than to a method in the abstract.
--
--   issue_time / valid_from / valid_to
--     The rolling-origin span this run covers: issue_time is the newest origin scored,
--     valid_from the earliest origin, valid_to the last target day any origin reached. A CHECK
--     requires valid_to to be strictly after valid_from, which a single-origin run satisfies
--     because its horizon window still has width.
--
--   horizon_steps with step_interval = interval '1 day'
--     The lead times scored per origin, one day apart. A CHECK requires horizon_steps to be
--     positive.
--
--   quality_summary column
--     The aggregate across every origin and horizon, plus the per-horizon breakdown. It lives
--     here rather than as extra metric rows because agri.forecast_backtest_metric is unique on
--     (forecast_run_id, series_id, cutoff_time) -- one row per origin -- and an "aggregate"
--     row would need a cutoff_time no origin actually had. Inventing that instant to satisfy a
--     unique index would be fabricating provenance. See execution/AGENTS.md.
--
--   ON CONFLICT (run_key) DO NOTHING
--     ON CONFLICT is PostgreSQL's "what to do when this row would violate a unique
--     constraint". The run key is derived from the pinned run identity, so a re-claimed shard
--     resolves to the existing row rather than raising. Nothing is inserted on that path, so
--     RETURNING yields no row and the caller resolves the existing id with a one-line lookup.
INSERT INTO agri.forecast_run (
    run_key,
    job_run_id,
    feature_snapshot_id,
    model_id,
    training_run_id,
    quality_policy_id,
    forecast_method,
    issue_time,
    valid_from,
    valid_to,
    horizon_steps,
    step_interval,
    input_release_checksum,
    feature_checksum,
    model_checksum,
    parameter_checksum,
    status,
    quality_summary
)
VALUES (
    CAST(:run_key AS varchar),
    CAST(:job_run_id AS uuid),
    CAST(:feature_snapshot_id AS uuid),
    CAST(:model_id AS uuid),
    CAST(:training_run_id AS uuid),
    CAST(:quality_policy_id AS uuid),
    'ml',
    CAST(:issue_time AS timestamptz),
    CAST(:valid_from AS timestamptz),
    CAST(:valid_to AS timestamptz),
    CAST(:horizon_steps AS integer),
    interval '1 day',
    CAST(:input_release_checksum AS varchar),
    CAST(:feature_checksum AS varchar),
    CAST(:model_checksum AS varchar),
    CAST(:parameter_checksum AS varchar),
    'staged',
    CAST(:quality_summary AS jsonb)
)
ON CONFLICT (run_key) DO NOTHING
RETURNING id
