-- Purpose: read the reconciled forecast-versus-actual pairs for a set of iterations, so holdout error
--          and interval coverage can be measured.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: iteration_ids (uuid[]) -- the finalized iterations to report on.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: one row per horizon step that has both a forecast and a real observation to
-- compare it against -- the series, the cutoff the forecast was made from, how many days ahead the
-- step was, the day it is about, the predicted low/median/high band, the observed value, and whether
-- the band contained that observation. Steps whose day has not yet been observed are excluded
-- entirely, so every returned row is a scorable pair.
--
-- How this query works, clause by clause:
--
--   FROM agri.v_forecast_iteration_outcome AS outcome
--     A view, not a table: a stored query that presents each forecast value already joined to its
--     reconciled actual, with the coverage test evaluated. Reading the view rather than re-deriving
--     the join here means every consumer scores forecasts by the same definition, and that definition
--     is versioned with the schema. In particular interval_covered -- whether the observation fell
--     between the low and high bounds -- is computed once in the view rather than being restated,
--     and possibly restated differently, by each caller.
--
--   WHERE outcome.iteration_id = ANY(CAST(iteration_ids AS uuid[]))
--     ANY means "equals any element of this array" -- the set-membership form of an equality test,
--     which is how one statement covers a whole run's worth of iterations rather than one. The cast
--     exists purely to pin the parameter's type: a bare bind parameter carries no type of its own and
--     the database will not guess which kind of array it was handed, so naming uuid[] settles it.
--
--   AND outcome.actual_value IS NOT NULL
--     Keeps only the steps that have something to be scored against. A forecast for a day that has
--     not happened yet, or whose observation has not been governed into a release, has no actual, and
--     an empty value is not a zero -- treating it as one would drag every error metric toward the
--     bounds. IS NOT NULL is the correct test because an empty value is never equal or unequal to
--     anything, so an ordinary comparison would silently drop these rows in a way that is easy to
--     misread.
--
--   ORDER BY outcome.series_id, outcome.cutoff_time, outcome.valid_time
--     A stable, total order: grouped by series, then by the run each pair came from, then
--     chronologically within it. The caller aggregates these rows into error metrics, and a fixed
--     order makes those metrics reproducible and any two runs directly comparable.
SELECT
    outcome.series_id,
    outcome.cutoff_time,
    outcome.horizon_step,
    outcome.valid_time,
    outcome.low_value,
    outcome.median_value,
    outcome.high_value,
    outcome.actual_value,
    outcome.interval_covered
FROM agri.v_forecast_iteration_outcome AS outcome
WHERE outcome.iteration_id = ANY(CAST(:iteration_ids AS uuid[]))
  AND outcome.actual_value IS NOT NULL
ORDER BY outcome.series_id, outcome.cutoff_time, outcome.valid_time
