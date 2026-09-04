-- select_executor_run_work_items
-- Purpose: list one executor checkpoint run's work items, each with its final attempt, so a
--          supersession receipt names the exact dead letter it leaves standing and why it died.
-- Loaded by: agri_data_service.execution.job_run_supersession
-- Params: job_run_id (uuid) -- the checkpoint run whose shards are being described.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row per work item of the run (an executor run normally has exactly one, the
-- scheduled command shard), with the item's status and attempt counters and, when any attempt was
-- ever opened, the final attempt's number, failure class, error summary and finish time. An item
-- that never opened an attempt still returns, with those four columns NULL.
--
-- How this query works, clause by clause:
--
--   FROM agri.job_work_item AS item WHERE item.job_run_id = job_run_id
--     Every shard of the one run, addressed by its run id.
--
--   LEFT JOIN LATERAL ( ... ORDER BY final_attempt.attempt_number DESC LIMIT 1 ) ON true
--     LATERAL lets the subquery refer to the item row it is joined to, so for each item it fetches
--     that item's single highest-numbered attempt. LEFT keeps items that have no attempt at all
--     rather than dropping them; ON true means the lateral row is the only join condition.
--
--   ORDER BY item.shard_key, item.id
--     A stable order so two reads of the same run print the same receipt.
SELECT item.id AS work_item_id,
       item.shard_key,
       item.status,
       item.attempt_count,
       item.max_attempts,
       item.completed_at,
       final_attempt.attempt_number,
       final_attempt.failure_class,
       final_attempt.error_summary,
       final_attempt.finished_at
FROM agri.job_work_item AS item
LEFT JOIN LATERAL (
    SELECT attempt.attempt_number, attempt.failure_class, attempt.error_summary, attempt.finished_at
    FROM agri.job_attempt AS attempt
    WHERE attempt.job_work_item_id = item.id
    ORDER BY attempt.attempt_number DESC
    LIMIT 1
) AS final_attempt ON true
WHERE item.job_run_id = CAST(:job_run_id AS uuid)
ORDER BY item.shard_key, item.id
