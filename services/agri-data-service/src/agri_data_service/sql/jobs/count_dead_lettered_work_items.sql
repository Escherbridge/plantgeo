-- count_dead_lettered_work_items
-- Purpose: census one lane's BURIED work -- how many of a job definition's work items are sitting in
--          'dead_letter' right now, across every run that definition has ever opened.
-- Loaded by: agri_data_service.execution.jobs_pulse_command
-- Params: definition_name (text) -- the agri.job_definition.name whose buried work is being counted.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: exactly ONE row, always, even for a lane with no runs and no items at all -- an
-- aggregate SELECT with no GROUP BY collapses an empty scan to a single row of zeroes and NULLs. The
-- caller depends on that: a lane it cannot count is a lane it must not call healthy, and "no rows"
-- would be indistinguishable from "no buried work".
--
-- WHY THIS EXISTS, and what it replaced. The pulse used to derive its standing-failure signal from
-- agri.job_run.status, which is wrong in both directions:
--
--   It cannot fire when it should. select_open_job_run.sql only ever selects a run whose status is
--   'queued' or 'running'. The tick AFTER a run rolls up to 'failed' or 'partial' therefore selects
--   no run at all, builds its summary with run_status left NULL, and reads as a clean idle tick --
--   so the run-status signal covered exactly the ONE tick on which the run went terminal, and that
--   tick was already loud because it had dead-lettered something.
--
--   It fires when it must not. refresh_job_run_rollup.sql counts 'cancelled' work items as `failed`,
--   and the run CASE has no 'cancelled' branch, so an OPERATOR CANCELLATION rolls a run up to
--   'partial' (or 'failed') -- both of which the old signal treated as a standing failure. Worse,
--   a lane holding one never-rotating run (jobs/matview_refresh.py) keeps every historical settled
--   item forever, so one old burial pins that run at 'partial' and would have redded the hourly cron
--   permanently, with no operator action able to clear it.
--
-- A dead-lettered WORK ITEM has neither defect. It is written by exactly one statement
-- (dead_letter_work_item.sql), it says "this unit of work is buried and nobody is retrying it", it
-- does not appear because an operator made a decision, and it IS operator-clearable: requeueing the
-- item or cancelling it deliberately moves it out of 'dead_letter' and the next tick goes green.
--
-- How this query works, clause by clause:
--
--   FROM agri.job_work_item AS item JOIN agri.job_run ... JOIN agri.job_definition ...
--     Work items hang off a run, and a run hangs off a definition; the two joins walk that chain so
--     the caller can ask by the lane NAME it already holds rather than by a run id it may not have.
--     A definition is versioned -- several agri.job_definition rows can share one name -- so joining
--     on the name deliberately covers every version's runs, exactly as the pulse's own pause check
--     (select_job_definition_pause_state.sql) treats all versions of a name as one lane.
--
--   WHERE item.status = 'dead_letter'
--     The one status this counts. 'cancelled' is excluded ON PURPOSE: it is an operator's own
--     decision and must not page anyone hourly for having been made. 'retry_wait' and 'deferred' are
--     still in play. The leading column of ix_job_work_item_claim is status, so this predicate is
--     index-supported rather than a sequential scan of the whole ledger.
--
--   count(*) AS dead_lettered_work_items
--     The signal itself: zero means nothing is buried in this lane.
--
--   min(item.shard_key) / max(item.completed_at)
--     Two pieces of evidence for the operator-facing detail line, so the tick's summary names WHICH
--     unit of work to look at and WHEN it was buried instead of only how many there are. min() over
--     shard_key is a stable, deterministic pick (shard keys sort as the archive windows they name),
--     not "a random one of them".
SELECT count(*) AS dead_lettered_work_items,
       min(item.shard_key) AS first_dead_lettered_shard_key,
       max(item.completed_at) AS last_dead_lettered_at
FROM agri.job_work_item AS item
JOIN agri.job_run AS run ON run.id = item.job_run_id
JOIN agri.job_definition AS definition ON definition.id = run.job_definition_id
WHERE definition.name = :definition_name
  AND item.status = 'dead_letter'
