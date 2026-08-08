-- Purpose: record one rolling origin's holdout scores -- the point and interval error the
--          model made when it forecast forward from that single issue day.
-- Loaded by: agri_data_service.execution.covariate_wind_persist
-- Params: forecast_run_id (uuid), job_output_id (uuid), series_id (uuid),
--         cutoff_time (timestamptz), training_point_count (int),
--         backtest_point_count (int), mae (float), rmse (float), naive_rmse (float,
--         nullable), skill_score (float, nullable), bias (float), mape (float, nullable),
--         coverage_fraction (float), metrics_checksum (text)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- ONE ROW PER ROLLING ORIGIN, and that grain is the schema's, not a preference: the table
-- carries UNIQUE (forecast_run_id, series_id, cutoff_time), so cutoff_time is the axis a
-- metric row is identified along. cutoff_time here is the origin's own UTC midnight -- the
-- last instant whose observations the model fitted at that origin was allowed to see. The
-- horizon axis is carried by the forecast run's horizon_steps, and the per-horizon breakdown
-- by its quality_summary, because two horizons scored from one origin share a cutoff and
-- could not both be stored here.
--
-- How this query works, clause by clause:
--
--   training_point_count / backtest_point_count
--     How many rows fitted the model at this origin, and how many (prediction, actual) pairs
--     were scored from it. A CHECK requires at least 3 training points and at least 1
--     backtest point. Read backtest_point_count with the caveat in execution/AGENTS.md: the
--     pairs from one origin are consecutive daily values of an autocorrelated variable, so the
--     effective sample size is far below the count.
--
--   naive_rmse / skill_score
--     The persistence baseline -- "tomorrow equals the last value observed at the origin" --
--     and the model's skill against it, computed as one minus the ratio of the two RMSEs.
--     Both are nullable and are sent as NULL when the baseline RMSE is zero, because a skill
--     score against a perfect baseline is a division by zero, not a score of one.
--
--   coverage_fraction column
--     The share of actuals that fell inside the calibrated p10-p90 band. A CHECK holds it in
--     [0, 1]. It is an empirical hit rate on one origin's residual band, not a calibrated
--     confidence level.
--
--   metrics_checksum column
--     A digest over exactly the numbers in this row, so a metric cannot be edited without the
--     edit being visible. A CHECK requires it to look like a SHA-256 hex digest.
--
--   passed column
--     Left at its server default of false. Passing is a verdict against a quality policy, and
--     this lane measures rather than judges; only the reviewed publication path may promote it.
--
--   ON CONFLICT (forecast_run_id, series_id, cutoff_time) DO NOTHING
--     ON CONFLICT is PostgreSQL's "what to do when this row would violate a unique
--     constraint". A durable lane re-claiming a shard whose work already committed re-derives
--     the same origin and the same run, so this becomes a no-op rather than raising. DO
--     NOTHING rather than DO UPDATE because a recorded measurement is evidence: a second run
--     that disagreed with the first must be visible as a conflict, not silently overwrite it.
INSERT INTO agri.forecast_backtest_metric (
    forecast_run_id,
    job_output_id,
    series_id,
    cutoff_time,
    training_point_count,
    backtest_point_count,
    mae,
    rmse,
    naive_rmse,
    skill_score,
    bias,
    mape,
    coverage_fraction,
    metrics_checksum
)
VALUES (
    CAST(:forecast_run_id AS uuid),
    CAST(:job_output_id AS uuid),
    CAST(:series_id AS uuid),
    CAST(:cutoff_time AS timestamptz),
    CAST(:training_point_count AS integer),
    CAST(:backtest_point_count AS integer),
    CAST(:mae AS double precision),
    CAST(:rmse AS double precision),
    CAST(:naive_rmse AS double precision),
    CAST(:skill_score AS double precision),
    CAST(:bias AS double precision),
    CAST(:mape AS double precision),
    CAST(:coverage_fraction AS double precision),
    CAST(:metrics_checksum AS varchar)
)
ON CONFLICT (forecast_run_id, series_id, cutoff_time) DO NOTHING
RETURNING id
