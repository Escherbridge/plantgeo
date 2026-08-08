-- close_lost_attempts
-- Purpose: close, in one statement, every still-'running' attempt belonging to the shards the
--          reaper has just reclaimed, so no dead worker's attempt is left looking live.
-- Loaded by: agri_data_service.jobs.lease
-- Params: work_item_ids (text holding an array of uuids) -- the shards the reaper reclaimed in
--         this same pass, passed as one array rather than one statement per shard.
--         failure_class (text) -- short machine-readable label recorded on each closed attempt.
--         error_summary (text) -- one operator-facing sentence, likewise recorded.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row per attempt closed, so the reaper can report how many dangling
-- attempts it tidied up alongside the shards themselves.
--
-- This is the bulk counterpart to close_attempt_lost. That one runs on a worker closing its own
-- attempt after being fenced out; this one runs on the reaper, which is dealing with workers that
-- will never run any code again. Neither carries a fence, for the same reason: there is no live
-- ownership left to prove.
--
-- ONE STATEMENT FOR ANY NUMBER OF SHARDS, on purpose. A per-shard UPDATE would be one network
-- round trip each against the Railway proxy over the single connection a slice opens, so a reaper
-- pass that found two hundred stranded windows would spend two hundred serial round trips on
-- bookkeeping.
--
-- How this query works, clause by clause:
--
--   UPDATE agri.job_attempt SET status = 'lost', finished_at = now()
--     'lost' is the verdict for an attempt whose worker vanished -- neither a success nor a failure
--     it reported. Stamping finished_at removes it from the open-attempt population that every
--     liveness query reads.
--
--   WHERE status = 'running'
--     Only attempts still believed live. Anything that already reached a verdict is left untouched,
--     which also makes the statement safe to run twice.
--
--   AND job_work_item_id = ANY(CAST(work_item_ids AS uuid[]))
--     ANY(array) is SQL's "is this value one of these", the set-shaped form of IN, and it accepts a
--     single bound parameter holding a whole array -- which is exactly what makes the one-statement
--     form possible, since IN would need one placeholder per element and therefore a statement
--     whose text changed with the batch size.
--     The CAST to uuid[] pins the parameter's type. The caller sends the ids as strings, and
--     without the cast PostgreSQL has no column context from which to infer that this parameter is
--     an array of uuids at all.
--
--   RETURNING id
--     RETURNING hands back a row per attempt actually closed, in the same round trip. The reaper
--     counts these rows for its summary; no second query is needed.
UPDATE agri.job_attempt
SET status = 'lost',
    finished_at = now(),
    failure_class = :failure_class,
    error_summary = :error_summary
WHERE status = 'running'
  AND job_work_item_id = ANY(CAST(:work_item_ids AS uuid[]))
RETURNING id
