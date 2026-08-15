-- Purpose: read recorded forecast-iteration horizon values with their actuals -- one row per
--          (iteration, horizon step) -- for split-conformal recalibration of a finalized
--          iteration method's p10/p50/p90 bands. Optionally scoped to one series.
-- Loaded by: agri_data_service.execution.conformal_recalibration
-- Params: method (varchar, the forecast_iteration.method to recalibrate), series_id (uuid,
--         nullable -- NULL reads every series governed under this method), as_of_time
--         (timestamptz)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy's text() scans comments for colon-prefixed words too.
--
-- THIS FILE CARRIES TWO INDEPENDENT LEAKAGE GATES, both against the same `as_of_time`:
--
--   outcome.forecast_available_at <= :as_of_time
--     The iteration's own band (low/median/high) must already have been recorded. A margin
--     or a "before" coverage figure fit against a band nobody could have read yet would not
--     be evidence of anything.
--
--   outcome.actual_recorded_at <= :as_of_time
--     The actual this residual is scored against must also already have been recorded.
--     Without this gate a recalibration run today could silently use an actual that arrived
--     only yesterday, which is exactly the "recorded_at" leakage rule the pure calibration
--     module (agri_data_service.method.ml.conformal_calibration) re-checks a second time on
--     the Python side -- belt and braces, not redundant, because a caller could otherwise pass
--     a stale as_of_time into only one of the two layers.
--
-- outcome.actual_value IS NOT NULL
--     v_forecast_iteration_outcome LEFT JOINs the actual, so a horizon step whose actual has
--     not landed yet still appears with every actual_* column NULL. Excluding those here means
--     the caller never has to special-case a NULL residual downstream.
--
-- (CAST(:series_id AS uuid) IS NULL OR outcome.series_id = CAST(:series_id AS uuid))
--     A static, always-present predicate rather than an assembled WHERE clause: passing NULL
--     reads every series governed under the method (the shape a recalibration run wants, since
--     one series rarely carries enough held-out origins on its own), and passing a real id scopes
--     to exactly one series for a single-series report.
--
-- ORDER BY outcome.cutoff_time, outcome.series_id, outcome.horizon_step
--     A stable order: cutoff_time first, because that is the axis a caller splits into a
--     calibration fold and a held-out fold.
SELECT outcome.iteration_key,
       outcome.series_id,
       outcome.method,
       outcome.cutoff_time,
       outcome.valid_time,
       outcome.horizon_step,
       outcome.low_value,
       outcome.median_value,
       outcome.high_value,
       outcome.actual_value,
       outcome.forecast_available_at,
       outcome.actual_recorded_at
FROM agri.v_forecast_iteration_outcome AS outcome
WHERE outcome.method = CAST(:method AS varchar)
  AND (CAST(:series_id AS uuid) IS NULL OR outcome.series_id = CAST(:series_id AS uuid))
  AND outcome.actual_value IS NOT NULL
  AND outcome.forecast_available_at <= CAST(:as_of_time AS timestamptz)
  AND outcome.actual_recorded_at <= CAST(:as_of_time AS timestamptz)
ORDER BY outcome.cutoff_time, outcome.series_id, outcome.horizon_step
