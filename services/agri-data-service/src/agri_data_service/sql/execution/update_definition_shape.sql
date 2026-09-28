-- Purpose: bring one stored executor definition's shape up to its code-owned spec, never touching its pause switch.
-- Loaded by: agri_data_service.execution.job_executor_service (_sync_definition_shape)
-- Params: name/version (text), schedule/schedule_timezone (text), parameters (JSON text),
--         runtime_shape (boolean), max_attempts/lease_seconds/time_budget_seconds (integer),
--         retry_policy (JSON text)
--
-- Why it exists: insert_definition.sql is ON CONFLICT DO NOTHING, so a spec change that keeps the same
-- name and version (the soil cadence moving from hourly to six-hourly, a config lane's CA13 cut-over)
-- would otherwise never reach the stored row. The admin surface reads schedule from that row.
--
-- How this query works, clause by clause:
--
--   UPDATE agri.job_definition SET schedule = ..., schedule_timezone = ..., parameters = ...
--     The descriptive columns. The executor schedules from its own spec (cadence or cron), never from
--     these, so rewriting them changes what /admin/jobs shows and nothing about when a lane runs.
--
--   max_attempts = CASE WHEN :runtime_shape THEN :max_attempts ELSE max_attempts END (and the same for
--   lease_seconds, time_budget_seconds, retry_policy)
--     The runtime columns: the worker caps a turn with them. They are rewritten only when the caller
--     says so (a config-path lane, whose TOML owns them); a legacy lane keeps its stored limits, because
--     changing those has always meant a new definition version.
--
--   updated_at = now()
--     Records when the row last changed, as every other writer of this table does.
--
--   WHERE name = :name AND version = :version
--     Exactly one definition row: the unique (name, version) pair.
--
--   AND (schedule IS DISTINCT FROM ... OR ...)
--     Writes only when something actually differs, so a repeated call is a no-op that takes no row lock.
--     IS DISTINCT FROM treats NULL as a comparable value, so a NULL schedule is still brought in line.
--
--   enabled is never named: an operator's pause survives every shape update.
--
--   RETURNING id
--     A row back means the definition changed; no row means it already matched.
UPDATE agri.job_definition
SET
    schedule = :schedule,
    schedule_timezone = :schedule_timezone,
    parameters = CAST(:parameters AS jsonb),
    max_attempts = CASE WHEN :runtime_shape THEN :max_attempts ELSE max_attempts END,
    lease_seconds = CASE WHEN :runtime_shape THEN :lease_seconds ELSE lease_seconds END,
    time_budget_seconds = CASE WHEN :runtime_shape THEN :time_budget_seconds ELSE time_budget_seconds END,
    retry_policy = CASE WHEN :runtime_shape THEN CAST(:retry_policy AS jsonb) ELSE retry_policy END,
    updated_at = now()
WHERE name = :name
  AND version = :version
  AND (
      schedule IS DISTINCT FROM :schedule
      OR schedule_timezone IS DISTINCT FROM :schedule_timezone
      OR parameters IS DISTINCT FROM CAST(:parameters AS jsonb)
      OR (
          :runtime_shape
          AND (
              max_attempts IS DISTINCT FROM :max_attempts
              OR lease_seconds IS DISTINCT FROM :lease_seconds
              OR time_budget_seconds IS DISTINCT FROM :time_budget_seconds
              OR retry_policy IS DISTINCT FROM CAST(:retry_policy AS jsonb)
          )
      )
  )
RETURNING id
