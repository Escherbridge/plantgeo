-- Purpose: find the most recent durable cadence run for one executor definition.
-- Loaded by: agri_data_service.execution.job_executor_service
-- Params: job_definition_id (uuid)
--
-- How this query works, clause by clause:
--
--   SELECT id, scheduled_for, status
--     Returns the identity, cadence bucket, and lifecycle state needed to resume an open run, refuse
--     a terminal failed run, or decide whether a newer bucket is due.
--
--   FROM agri.job_run
--     Reads only durable scheduler runs; command or layer tables do not participate in scheduling.
--
--   WHERE job_definition_id = ...
--     Restricts candidates to the one exact executor definition supplied as a bound parameter.
--
--   ORDER BY scheduled_for DESC, created_at DESC
--     Puts the newest cadence bucket first. created_at is the deterministic tie-breaker if repaired or
--     legacy data contains more than one row with the same schedule time.
--
--   LIMIT 1
--     Returns the single latest run the scheduler uses as its durable due/resume checkpoint.
SELECT id, scheduled_for, status
FROM agri.job_run
WHERE job_definition_id = :job_definition_id
ORDER BY scheduled_for DESC, created_at DESC
LIMIT 1
