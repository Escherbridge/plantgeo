-- select_run_final_attempt
-- Purpose: read the ONE attempt that decides a held checkpoint run's exit class when a `lane_hold`
--          incident opens (spec Sec 4.9.3, design Sec 3.2A: "its class comes from
--          select_run_final_attempt.sql"). `execution/lane_incidents.py` turns the row this returns
--          into an `ExitClass` and a `class_source` string; missing, an unrecognised
--          `metrics.exit_class`, or a `lost` status are all read there as `code`, never here.
-- Loaded by: agri_data_service.execution.lane_incidents
-- Params: job_run_id (uuid) -- the checkpoint run a `judge_failed_checkpoint` verdict just named as held.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: at most one row -- the run's FINAL attempt (across every work item the run has;
-- an executor's scheduled-command run normally has exactly one, `select_executor_run_work_items.sql`'s
-- own header says so) -- carrying its status, failure_class, error_summary and o5a's `classify_exit`
-- stamp, read here with `->>` as `exit_class` (GL-3 onward) rather than the whole `metrics` jsonb
-- blob, the same extract-don't-return-jsonb convention `select_open_incidents.sql` already uses. No
-- rows means the run never opened an attempt at all, which the caller reads the same way it reads a
-- NULL exit_class: as `code`, because a run judged failed with no attempt evidence is not evidence
-- of anything transient.
--
-- How this query works, clause by clause:
--
--   FROM agri.job_attempt AS attempt JOIN agri.job_work_item AS item ON item.id = attempt.job_work_item_id
--     Walks from attempts up to their owning work item so every attempt of every shard of the run is
--     in scope, not only one item's -- the run-final attempt is the newest across the WHOLE run.
--
--   WHERE item.job_run_id = CAST(:job_run_id AS uuid)
--     Scopes the read to exactly the one checkpoint run the caller is judging.
--
--   ORDER BY attempt.finished_at DESC NULLS LAST, attempt.attempt_number DESC
--     "Final" means the attempt that settled last. A held run's deciding attempt is always terminal
--     and therefore carries a finished_at, but NULLS LAST keeps a still-`running` row (which should
--     never exist for a settled run, and never wins the ordering if it somehow does) from sorting
--     ahead of a genuinely finished one; attempt_number DESC breaks a tie between two attempts that
--     finished in the same instant, always preferring the higher-numbered (later) retry.
--
--   LIMIT 1
--     One row: the run's own "what actually happened last" answer, never a history to walk.
SELECT attempt.status,
       attempt.failure_class,
       attempt.metrics ->> 'exit_class' AS exit_class,
       attempt.error_summary,
       attempt.finished_at
FROM agri.job_attempt AS attempt
JOIN agri.job_work_item AS item ON item.id = attempt.job_work_item_id
WHERE item.job_run_id = CAST(:job_run_id AS uuid)
ORDER BY attempt.finished_at DESC NULLS LAST, attempt.attempt_number DESC
LIMIT 1
