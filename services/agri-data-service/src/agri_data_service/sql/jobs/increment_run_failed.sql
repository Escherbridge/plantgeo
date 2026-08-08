-- increment_run_failed
-- Purpose: raise the run's failed-shard counter by a given amount, clamped so it can never cross
--          the run's own totals and abort the transaction that is recording the failure.
-- Loaded by: agri_data_service.jobs.lease
-- Params: job_run_id (uuid) -- the run whose counter is being bumped.
--         increment (int) -- how many shards to add. One on the ordinary dead-letter path; the
--         reaper passes the number of shards it dead-lettered in a single pass.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row holding the counter's new value. Like its succeeded-side twin, the
-- counter is an APPROXIMATION kept cheap for dashboards; the authoritative numbers are recomputed
-- from the work items themselves by the rollup the slice runs when it finishes.
--
-- WHY IT IS CLAMPED RATHER THAN A BARE ADDITION. ck_job_run_work_item_counts_within_total is an
-- IMMEDIATE constraint -- checked the instant the statement runs, not at commit -- so a bump that
-- pushed the failed total past what the run can hold would ABORT the transaction recording the
-- failure, rather than merely producing a wrong number. Clamping trades a possibly-stale counter
-- for never losing a recorded outcome.
--
-- How this query works, clause by clause:
--
--   LEAST(failed_work_items + increment, total_work_items - succeeded_work_items)
--     LEAST returns the smallest of its arguments. The right-hand side is the largest value the
--     failed counter could legally hold given how many shards have already succeeded, so this
--     reads "add the increment, but never past the ceiling".
--
--   GREATEST(failed_work_items, LEAST(...))
--     GREATEST returns the largest of its arguments and stops the counter ever moving DOWN, which
--     it otherwise could when the ceiling drops because successes were recorded in between.
--     Together the two mean: never decrease, never cross the total.
--
--   WHERE id = job_run_id
--     One run, addressed by primary key. No fence is involved -- this touches a run-level counter
--     and never a shard, so there is no ownership to prove.
--
--   RETURNING failed_work_items
--     RETURNING hands back the post-update value in the same round trip, so a caller that wants to
--     log the new figure needs no second query.
UPDATE agri.job_run
SET failed_work_items = GREATEST(
        failed_work_items,
        LEAST(failed_work_items + :increment, total_work_items - succeeded_work_items)
    )
WHERE id = :job_run_id
RETURNING failed_work_items
