-- refresh_job_run_rollup
-- Purpose: recompute a run's three counters and its status from its work items and write all of
--          them at once -- the authority that the cheap incremental bumps only approximate.
-- Loaded by: agri_data_service.jobs.worker
-- Params: job_run_id (uuid) -- the run being recomputed. It appears twice below, once to scope the
--         count and once to address the row being written.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: exactly one row -- the run's status and its three counters as they now stand.
-- No rows would mean the run disappeared mid-tick, which the caller raises on.
--
-- NOTHING IN THE DATABASE MAINTAINS THESE COUNTERS. There is no trigger, no generated column and no
-- materialised view behind them; this statement is the only thing that ever makes them true. The
-- per-shard increments elsewhere keep a dashboard roughly current between ticks, and this repairs
-- them at the end of every tick.
--
-- IT MUST BE ONE STATEMENT. Every counter is assigned an ABSOLUTE value recomputed from the work
-- items, and all three move together, because ck_job_run_work_item_counts_within_total is an
-- IMMEDIATE constraint -- checked the instant a statement runs rather than at commit -- so it must
-- never see a half-updated triple. For the same reason the terminal status and completed_at move
-- together, satisfying ck_job_run_terminal_run_has_completion_time.
--
-- How this query works, clause by clause:
--
--   FROM ( SELECT count(*) ... FROM agri.job_work_item WHERE job_run_id = job_run_id ) AS tally
--     A subquery in the FROM clause, used here as a one-row source of truth. It scans the run's
--     work items once and collapses them into a single row of totals, which the UPDATE above then
--     reads columns from. Because it has no GROUP BY, an aggregate query like this returns exactly
--     one row even when nothing matches -- with zeroes -- which is what keeps a run with no shards
--     yet from silently failing to update.
--
--   count(*) FILTER (WHERE status = 'succeeded')
--     FILTER restricts ONE aggregate to the rows matching its condition while the other aggregates
--     in the same SELECT still see every row. That is how the total and both breakdowns come out of
--     a single pass over the table instead of three separate queries. Rows failing the FILTER are
--     not counted; they are not dropped.
--
--   count(*) FILTER (WHERE status IN ('dead_letter', 'cancelled')) AS failed
--     "Failed" for a run's purposes means "settled without succeeding" -- a spent budget or an
--     operator cancellation. Note what is NOT here: 'retry_wait' and 'deferred' are still in play
--     and count as neither, which is exactly why succeeded + failed < total means "still running".
--
--   SET total_work_items = tally.total, succeeded_work_items = ..., failed_work_items = ...
--     Absolute assignment, not arithmetic on the stored value, so the result cannot drift no matter
--     what the incremental bumps did in between.
--
--   started_at = COALESCE(run.started_at, now())
--     COALESCE returns its first non-NULL argument, so a start time is stamped once and never
--     rewritten. "When this run first did anything", not "when it was last touched".
--
--   status = CASE ... END
--     A CASE expression is SQL's if/else, evaluated in order, first match wins:
--       tally.total = 0                                -> leave the status alone. A run whose shards
--                                                         have not been fanned out yet has decided
--                                                         nothing, and calling that 'succeeded'
--                                                         would close an empty run.
--       succeeded + failed < total                     -> 'running'. Something is still outstanding.
--       failed = 0                                     -> 'succeeded'. Everything settled, cleanly.
--       succeeded = 0                                  -> 'failed'. Everything settled, none well.
--       otherwise                                      -> 'partial'. A mixture, and worth naming
--                                                         separately so it is neither celebrated nor
--                                                         mourned wholesale.
--
--   completed_at = CASE WHEN total > 0 AND succeeded + failed = total
--                       THEN COALESCE(run.completed_at, now()) ELSE run.completed_at END
--     Stamps a completion time exactly when the run reaches a terminal status, preserves it once
--     stamped (COALESCE again), and otherwise leaves the column exactly as it was -- an ELSE that
--     re-assigns the existing value is how a conditional write is spelled inside a single UPDATE.
--
--   WHERE run.id = job_run_id
--     One run, addressed by primary key.
--
--   RETURNING run.status, run.total_work_items, run.succeeded_work_items, run.failed_work_items
--     RETURNING hands back the POST-update values in the same round trip, so the caller reports the
--     numbers the database actually holds rather than the ones it hoped it wrote.
UPDATE agri.job_run AS run
SET total_work_items = tally.total,
    succeeded_work_items = tally.succeeded,
    failed_work_items = tally.failed,
    started_at = COALESCE(run.started_at, now()),
    status = CASE
        WHEN tally.total = 0 THEN run.status
        WHEN tally.succeeded + tally.failed < tally.total THEN 'running'
        WHEN tally.failed = 0 THEN 'succeeded'
        WHEN tally.succeeded = 0 THEN 'failed'
        ELSE 'partial'
    END,
    completed_at = CASE
        WHEN tally.total > 0 AND tally.succeeded + tally.failed = tally.total
            THEN COALESCE(run.completed_at, now())
        ELSE run.completed_at
    END
FROM (
    SELECT count(*) AS total,
           count(*) FILTER (WHERE status = 'succeeded') AS succeeded,
           count(*) FILTER (WHERE status IN ('dead_letter', 'cancelled')) AS failed
    FROM agri.job_work_item
    WHERE job_run_id = :job_run_id
) AS tally
WHERE run.id = :job_run_id
RETURNING run.status, run.total_work_items, run.succeeded_work_items, run.failed_work_items
