-- Purpose: daily dead-letter counts per lane, with a running total, so /ops/backfill can
--          show whether dead lettering is accelerating or has already stopped.
-- Loaded by: agri_data_service.routes.ops
-- Params: trend_days (int) -- how many days back the trend reaches.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row per (lane, day) on which at least one window was dead
-- lettered. Days with no dead letters produce no row at all -- there is nothing in the
-- ledger to count -- so the caller must treat a missing day as zero rather than as a gap.
--
-- How this query works, clause by clause:
--
--   WITH daily_dead_letters AS (...)
--     A CTE ("common table expression") -- a named subquery defined up front and used
--     below like a table. It does the counting; the outer query only adds the running
--     total. They are separate because a window function cannot be applied to an
--     aggregate that is being computed in the same SELECT.
--
--   WHERE item.status = 'dead_letter'
--     Dead letter is the terminal failure state: the window exhausted its attempts and
--     will not be retried until an operator requeues it. It is the only state worth
--     trending, because every other failure state is transient by design.
--
--   AND item.completed_at >= date_trunc('day', now() - make_interval(days => CAST(...)))
--     Interval arithmetic. now() is the current transaction timestamp;
--     make_interval(days => N) builds a span of N days; subtracting gives the far edge of
--     the window. date_trunc('day', ...) then rounds that edge down to midnight so the
--     oldest day in the result is a whole day rather than a partial one. make_interval is
--     used rather than the literal interval syntax because a bound parameter cannot
--     appear inside a quoted interval literal, and the CAST pins the parameter to integer
--     so the database never has to guess its type. completed_at is written at the moment
--     a window is dead lettered, which is what makes it the right axis here.
--
--   date_trunc('day', item.completed_at) AS day
--     Rounds each timestamp down to the midnight that starts its day, so many timestamps
--     across one day collapse to one identical value and can be grouped.
--
--   GROUP BY definition.name, date_trunc('day', item.completed_at)
--     Collapses the matching rows into one row per lane per day. count(*) then reports
--     how many rows fell into each of those buckets.
--
--   sum(daily.dead_lettered) OVER (PARTITION BY daily.definition_name
--                                  ORDER BY daily.day
--                                  ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
--     A window function. Unlike GROUP BY it does not collapse rows -- every input row is
--     still returned, with one extra computed column. PARTITION BY restarts the running
--     total for each lane, so one lane's dead letters never leak into another's. ORDER BY
--     fixes the order the running total accumulates in. The ROWS frame is the span being
--     summed: every row from the start of this lane's partition up to and including the
--     current one -- i.e. a cumulative total to date.
--
--   ORDER BY daily.definition_name, daily.day
--     A stable, total order so the chart is drawn left to right per lane and the rows do
--     not shuffle between refreshes.
WITH daily_dead_letters AS (
    SELECT
        definition.name AS definition_name,
        date_trunc('day', item.completed_at) AS day,
        count(*) AS dead_lettered
    FROM agri.job_work_item AS item
    JOIN agri.job_run AS run ON run.id = item.job_run_id
    JOIN agri.job_definition AS definition ON definition.id = run.job_definition_id
    WHERE item.status = 'dead_letter'
      AND item.completed_at >= date_trunc('day', now() - make_interval(days => CAST(:trend_days AS integer)))
    GROUP BY definition.name, date_trunc('day', item.completed_at)
)
SELECT
    daily.definition_name,
    daily.day,
    daily.dead_lettered,
    sum(daily.dead_lettered) OVER (
        PARTITION BY daily.definition_name
        ORDER BY daily.day
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS cumulative_dead_lettered
FROM daily_dead_letters AS daily
ORDER BY daily.definition_name, daily.day
