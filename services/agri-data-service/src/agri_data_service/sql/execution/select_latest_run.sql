-- Purpose: find the most recent durable cadence run for one executor definition.
-- Loaded by: agri_data_service.execution.job_executor_service
-- Params: job_definition_id (uuid)
--
-- How this query works, clause by clause:
--
--   SELECT id, scheduled_for, status, work_claimable
--     Returns the identity, cadence bucket, lifecycle state, and whether this run can claim work now.
--     An open run waiting on retry/defer time or a live lease must not consume its work class's turn.
--
--   FROM latest_run
--     Selects the durable scheduler checkpoint first; work items participate only in the claimability
--     predicate below, while command and layer tables do not participate in scheduling.
--
--   WHERE job_definition_id = ...
--     Restricts candidates to the one exact executor definition supplied as a bound parameter.
--
--   ORDER BY scheduled_for DESC, created_at DESC
--     Puts the newest cadence bucket first. created_at is the deterministic tie-breaker if repaired or
--     legacy data contains more than one row with the same schedule time.
--
--   EXISTS (... FROM agri.job_work_item)
--     Mirrors claim_work_item's fresh eligibility, while expired leases remain scheduler-eligible even
--     after the final attempt so run_job_slice's definition-scoped reaper can dead-letter and roll them up.
WITH latest_run AS (
    SELECT id, scheduled_for, status
    FROM agri.job_run
    WHERE job_definition_id = :job_definition_id
    ORDER BY scheduled_for DESC, created_at DESC
    LIMIT 1
)
SELECT run.id,
       run.scheduled_for,
       run.status,
       EXISTS (
           SELECT 1
           FROM agri.job_work_item AS item
           WHERE item.job_run_id = run.id
             AND item.available_at <= now()
             AND (
                   (
                       item.status IN ('queued', 'retry_wait', 'deferred')
                       AND item.attempt_count < item.max_attempts
                       AND (item.next_attempt_at IS NULL OR item.next_attempt_at <= now())
                   )
                   OR (
                       item.status IN ('leased', 'running')
                       AND item.lease_expires_at IS NOT NULL
                       AND item.lease_expires_at <= now()
                   )
             )
       ) AS work_claimable
FROM latest_run AS run
