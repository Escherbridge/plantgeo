-- Purpose: score one reconciled forecast iteration -- how many values it holds, how many now carry
--          an actual, and the average error and interval coverage across them.
-- Loaded by: agri_data_service.cli
-- Params: iteration_id (uuid) -- the reconciled iteration to score.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: exactly one row, always -- an aggregate with no GROUP BY collapses whatever it
-- read into a single row even when it read nothing. For an iteration with no rows the counts come
-- back as 0 and both averages as NULL, which the caller renders as null rather than as a number.
--
-- How this query works, clause by clause:
--
--   FROM agri.v_forecast_iteration_outcome AS outcome
--     A view -- a stored query addressed like a table. It pairs each forecast value with the actual
--     observation reconciliation later matched to it, and derives the per-row comparisons this
--     statement averages. Reading the view rather than re-deriving those comparisons here is what
--     keeps the CLI's numbers and the serving layer's numbers the same numbers.
--
--   count(*)::integer AS forecast_value_count
--     Every row in the window, actual or not -- that is, how many forecast values the iteration
--     produced. count() returns bigint; the :: cast narrows it to a plain integer so the driver
--     hands Python an int.
--
--   count(outcome.actual_checksum)::integer AS actual_count
--     How many of those have been reconciled. count(column) -- unlike count(*) -- skips rows where
--     the column is NULL, so counting the actual's checksum counts exactly the rows that got one.
--     The gap between this and forecast_value_count is the un-reconciled remainder.
--
--   avg(outcome.absolute_error) AS mean_absolute_error
--     The mean of the per-row absolute errors. avg() ignores NULLs, so un-reconciled rows do not
--     drag the average toward zero; it is the mean over the rows that actually have an actual. It
--     returns NULL when there are no such rows.
--
--   avg(CASE WHEN outcome.interval_covered THEN 1.0 ELSE 0.0 END) AS interval_coverage
--     The share of rows whose true value fell inside the forecast's predicted interval. Averaging a
--     CASE that maps true to 1.0 and false to 0.0 is the standard way to turn a yes/no column into
--     a proportion. Note that a NULL interval_covered takes the ELSE branch and counts as 0.0
--     rather than being skipped -- an un-evaluated row is treated as not covered, which is the
--     conservative reading.
--
--   WHERE outcome.iteration_id = :iteration_id
--     Restricts the aggregate to the one iteration asked about. Bound, never interpolated.
SELECT
    count(*)::integer AS forecast_value_count,
    count(outcome.actual_checksum)::integer AS actual_count,
    avg(outcome.absolute_error) AS mean_absolute_error,
    avg(
        CASE WHEN outcome.interval_covered THEN 1.0 ELSE 0.0 END
    ) AS interval_coverage
FROM agri.v_forecast_iteration_outcome AS outcome
WHERE outcome.iteration_id = :iteration_id
