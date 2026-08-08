-- increment_run_succeeded
-- Purpose: nudge the run's succeeded-shard counter up by one, clamped so it can never cross the
--          run's own totals and abort the completion that triggered it.
-- Loaded by: agri_data_service.jobs.lease
-- Params: job_run_id (uuid) -- the run whose counter is being bumped.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row holding the counter's new value. The counter is an APPROXIMATION
-- kept cheap for dashboards; the authoritative figures are recomputed from the work items
-- themselves by the rollup statement the slice runs when it finishes.
--
-- WHY IT IS CLAMPED RATHER THAN A BARE +1. ck_job_run_work_item_counts_within_total is an
-- IMMEDIATE constraint, meaning the database enforces it the instant the statement runs rather
-- than at commit. So a counter bump that pushed the succeeded total past total_work_items would
-- not merely be a wrong number -- it would ABORT the transaction that was completing a shard, and
-- lose a shard that genuinely succeeded. Clamping trades a possibly-stale counter for never
-- losing real work, and the closing rollup repairs the number regardless.
--
-- How this query works, clause by clause:
--
--   LEAST(succeeded_work_items + 1, total_work_items - failed_work_items)
--     LEAST returns the smallest of its arguments. The right-hand side is the largest value the
--     succeeded counter could legally hold given how many shards have already failed, so this is
--     "add one, but never past the ceiling".
--
--   GREATEST(succeeded_work_items, LEAST(...))
--     GREATEST returns the largest of its arguments, and wraps the clamp so the counter can never
--     go DOWN. Without it, a ceiling that has just dropped (because failures were recorded) would
--     make the LEAST smaller than the stored value and the counter would move backwards.
--     Together the two mean: never decrease, never cross the total.
--
--   WHERE id = job_run_id
--     One run, addressed by primary key. No fence is involved -- this touches only a run-level
--     counter, never a shard, so there is no ownership to prove.
--
--   RETURNING succeeded_work_items
--     RETURNING hands back the post-update value in the same round trip, so a caller that wants to
--     log the new figure needs no second query.
UPDATE agri.job_run
SET succeeded_work_items = GREATEST(
        succeeded_work_items,
        LEAST(succeeded_work_items + 1, total_work_items - failed_work_items)
    )
WHERE id = :job_run_id
RETURNING succeeded_work_items
