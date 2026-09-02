-- Purpose: register one missing executor definition without overwriting an existing pause switch.
-- Loaded by: agri_data_service.execution.job_executor_service
-- Params: name/version/handler/queue_name/schedule/schedule_timezone/concurrency_key (text, nullable
--         where the job contract permits), enabled (boolean),
--         max_attempts/lease_seconds/time_budget_seconds (integer), retry_policy/parameters (JSON text)
--
-- How this query works, clause by clause:
--
--   INSERT INTO agri.job_definition (...)
--     Names every persisted part of the scheduler contract. enabled is deliberately supplied below
--     only for a new row; an existing row must retain the operator's stored value.
--
--   VALUES (..., enabled, ..., CAST(... AS jsonb), ...)
--     Binds every value rather than interpolating command or environment data. The two explicit casts
--     turn canonical JSON strings into the table's jsonb columns. enabled is true only for a lane's
--     first definition. Every later version starts disabled and requires an explicit lane-wide resume,
--     so a pause racing registration can cause only an extra safe pause, never silent reactivation.
--
--   ON CONFLICT (name, version) DO NOTHING
--     PostgreSQL's conflict clause converts a concurrent or repeated registration of the same exact
--     definition into a no-op. In particular, it does not run an UPDATE that could silently unpause it.
--
--   RETURNING id
--     Returns the new identity when this statement inserted a row. A no-op returns no row, after which
--     the caller performs the same exact state read used before insertion.
INSERT INTO agri.job_definition (
    name, version, handler, queue_name, schedule, schedule_timezone, enabled,
    concurrency_key, max_attempts, lease_seconds, time_budget_seconds, retry_policy, parameters
)
VALUES (
    :name, :version, :handler, :queue_name, :schedule, :schedule_timezone, :enabled,
    :concurrency_key, :max_attempts, :lease_seconds, :time_budget_seconds,
    CAST(:retry_policy AS jsonb), CAST(:parameters AS jsonb)
)
ON CONFLICT (name, version) DO NOTHING
RETURNING id
