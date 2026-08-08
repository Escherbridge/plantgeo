-- load_job_definition
-- Purpose: read the one enabled lane definition a slice is about to run, optionally pinned to a
--          version, so the tick executes what the ledger holds rather than what code assumed.
-- Loaded by: agri_data_service.jobs.worker
-- Params: name (text) -- the lane's name.
--         version (text, nullable) -- pin to one version, or NULL to take the newest.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: at most one definition row. No rows means either that no lane by that name
-- exists, or that it exists but is disabled, or that the pinned version is not there -- and the
-- caller turns all three into an explicit "no enabled job definition named X" error rather than
-- quietly doing nothing, because a tick that silently does nothing is indistinguishable from a
-- cron that never fired.
--
-- How this query works, clause by clause:
--
--   WHERE name = name
--     The lane being asked for.
--
--   AND enabled
--     enabled is a boolean column, so it stands alone as a condition -- no comparison needed. A
--     disabled lane is deliberately unloadable rather than loadable-and-skipped: switching the flag
--     off is how an operator stops a lane, and it must stop it at the earliest possible point.
--
--   AND (CAST(version AS text) IS NULL OR version = CAST(version AS text))
--     An OPTIONAL FILTER written inside the SQL rather than by assembling different statements in
--     Python: when the parameter is NULL the left side is true and the condition disappears, so one
--     statement serves both the pinned and unpinned cases. The CAST exists purely to pin the
--     parameter's type -- PostgreSQL cannot infer the type of a bare parameter that is compared
--     against NULL, and would refuse the statement.
--
--   ORDER BY version DESC
--     Picks the newest version of a name when the caller pinned none. Note that version is
--     free-text VARCHAR(100), so this is a STRING sort, not a numeric one -- "10" sorts before "2".
--     A lane that wants predictable ordering must version with a sortable scheme, such as a date or
--     zero-padded numbers.
--
--   LIMIT 1
--     One definition per slice. The ORDER BY above decides which.
SELECT id, name, version, handler, queue_name, concurrency_key,
       max_attempts, lease_seconds, time_budget_seconds, retry_policy, parameters
FROM agri.job_definition
WHERE name = :name
  AND enabled
  AND (CAST(:version AS text) IS NULL OR version = CAST(:version AS text))
ORDER BY version DESC
LIMIT 1
