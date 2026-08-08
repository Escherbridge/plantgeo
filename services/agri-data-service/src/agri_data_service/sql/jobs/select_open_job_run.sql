-- select_open_job_run
-- Purpose: pick the one run a tick should drive -- the oldest run of this lane that is still open.
-- Loaded by: agri_data_service.jobs.worker
-- Params: job_definition_id (uuid) -- the lane whose runs are in scope.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: at most one run id. No rows means this lane has nothing open, which the tick
-- reports as its stop reason and still records a heartbeat for -- an idle healthy lane and a lane
-- whose cron never fired must not look the same in the ledger.
--
-- A lane can legitimately have SEVERAL open runs at once: lowering a lane's backfill floor mints a
-- new run, and the previous one may still have windows left. A tick drives exactly one of them,
-- which is why the reaper it runs first is scoped to the whole LANE rather than to this run -- see
-- reclaim_expired_leases.sql, where a shard stranded in a sibling run would otherwise be
-- unreclaimable by any tick at all.
--
-- How this query works, clause by clause:
--
--   WHERE job_definition_id = job_definition_id
--     Scope: one lane.
--
--   AND status IN ('queued', 'running')
--     "Open" means not yet terminal. A run that has succeeded, failed, gone partial or been
--     cancelled has settled every shard it is ever going to settle and has no work to drive.
--
--   ORDER BY scheduled_for, created_at
--     Oldest first, so a lane works through its backlog front to back rather than starving an early
--     run behind newer ones. created_at breaks ties between runs scheduled for the same moment, and
--     makes the order deterministic instead of leaving equal rows to arrive in any order.
--
--   LIMIT 1
--     One run per tick.
SELECT id
FROM agri.job_run
WHERE job_definition_id = :job_definition_id AND status IN ('queued', 'running')
ORDER BY scheduled_for, created_at
LIMIT 1
