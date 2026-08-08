-- Purpose: the newest dead-lettered and retry-waiting windows, each with the error text
--          from the work item and from its most recent failed attempt, for the failure
--          table on /ops/backfill.
-- Loaded by: agri_data_service.routes.ops
-- Params: row_limit (int) -- how many rows to return, newest first.
--         error_summary_limit (int) -- how many characters of each error string to keep.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row per failing window. 'dead_letter' means the window ran out
-- of attempts and will never be retried without an operator requeuing it; 'retry_wait'
-- means it failed but has attempts left and is waiting for its backoff to elapse.
--
-- How this query works, clause by clause:
--
--   FROM agri.job_work_item AS item
--   JOIN agri.job_run AS run ON run.id = item.job_run_id
--   JOIN agri.job_definition AS definition ON definition.id = run.job_definition_id
--     Two ordinary joins, used only to put a human-readable lane name and run key next to
--     each window. A plain JOIN is right here (rather than LEFT JOIN) because both
--     parents are mandatory -- a work item cannot exist without its run, and a run cannot
--     exist without its definition.
--
--   LEFT JOIN LATERAL (SELECT ... ORDER BY attempt.attempt_number DESC LIMIT 1) ON TRUE
--     LATERAL lets the subquery on the right refer to the row on the left, so it runs
--     once per work item; a plain subquery in the FROM clause cannot see the row it is
--     being joined to. Used here to pull each window's single most recent *failed*
--     attempt, which is where the failure class and the error text that the runtime
--     actually recorded live. The work item only keeps the latest error, so the attempt
--     row is the more precise record of what went wrong and when. LEFT keeps windows that
--     have no failed attempt row at all (a lease reaped by the reaper leaves the item
--     marked failing without a closed attempt); ON TRUE means "no further join
--     condition", because the correlation already sits inside the subquery.
--
--   ORDER BY attempt.attempt_number DESC LIMIT 1
--     Inside the LATERAL: attempt numbers increase by one per try, so the highest one is
--     the newest. LIMIT 1 makes the subquery produce at most a single row, which is what
--     lets it be joined as if it were one extra set of columns.
--
--   left(item.last_error_summary, CAST(error_summary_limit AS integer))
--     Truncation. left(string, n) returns the first n characters. Applied so one
--     pathological stack trace cannot dominate the page. It returns NULL for a NULL
--     input, so a window with no recorded summary stays empty rather than becoming an
--     empty string. The CAST pins the bound parameter to integer so the database never
--     has to guess its type.
--
--   WHERE item.status IN ('dead_letter', 'retry_wait')
--     Only genuinely failing windows. 'deferred' is deliberately excluded: a deferral is
--     a voluntary park (the worker ran out of time budget and handed the window back)
--     and is not a failure, even though it also sets next_attempt_at.
--
--   ORDER BY COALESCE(item.completed_at, item.next_attempt_at, latest_failure.finished_at) DESC NULLS LAST
--     Newest first. COALESCE returns its first non-NULL argument, which is how one sort
--     key is built from three columns that are each populated on a different path:
--     dead-lettered windows get completed_at, retry-waiting windows get next_attempt_at,
--     and anything else falls back to when its last attempt closed. NULLS LAST keeps rows
--     with none of the three at the bottom instead of the top, which is where PostgreSQL
--     would otherwise place them under DESC.
--
--   LIMIT CAST(row_limit AS integer)
--     The page. Bound, never interpolated, and always present -- this table must not be
--     able to return the whole ledger.
SELECT
    definition.name AS definition_name,
    run.logical_run_key,
    item.shard_key,
    item.status AS item_status,
    item.attempt_count,
    item.max_attempts,
    item.last_error_class,
    left(item.last_error_summary, CAST(:error_summary_limit AS integer)) AS last_error_summary,
    item.completed_at,
    item.next_attempt_at,
    latest_failure.attempt_number AS attempt_number,
    latest_failure.worker_id AS attempt_worker_id,
    latest_failure.status AS attempt_status,
    latest_failure.finished_at AS attempt_finished_at,
    latest_failure.failure_class AS attempt_failure_class,
    left(latest_failure.error_summary, CAST(:error_summary_limit AS integer)) AS attempt_error_summary
FROM agri.job_work_item AS item
JOIN agri.job_run AS run ON run.id = item.job_run_id
JOIN agri.job_definition AS definition ON definition.id = run.job_definition_id
LEFT JOIN LATERAL (
    SELECT
        attempt.attempt_number,
        attempt.worker_id,
        attempt.status,
        attempt.finished_at,
        attempt.failure_class,
        attempt.error_summary
    FROM agri.job_attempt AS attempt
    WHERE attempt.job_work_item_id = item.id
      AND attempt.status IN ('failed', 'lost')
    ORDER BY attempt.attempt_number DESC
    LIMIT 1
) AS latest_failure ON TRUE
WHERE item.status IN ('dead_letter', 'retry_wait')
ORDER BY
    COALESCE(item.completed_at, item.next_attempt_at, latest_failure.finished_at) DESC NULLS LAST,
    item.shard_key
LIMIT CAST(:row_limit AS integer)
