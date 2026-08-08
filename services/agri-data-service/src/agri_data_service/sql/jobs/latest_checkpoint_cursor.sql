-- latest_checkpoint_cursor
-- Purpose: read back the newest saved cursor for one shard, so a fresh claim resumes where the
--          previous attempt left off instead of restarting the window.
-- Loaded by: agri_data_service.jobs.lease
-- Params: work_item_id (uuid) -- the shard whose progress is being recovered.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: at most one row holding a single JSONB value -- the resume point the last
-- checkpoint recorded. No rows means this shard has never checkpointed, so the handler starts the
-- window from the beginning.
--
-- A "checkpoint" is a durable note a worker writes mid-window saying "everything up to here is
-- committed; resume from this point". It is what makes a half-finished window survive a killed
-- container: the next claim reads this row and skips the work already done.
--
-- How this query works, clause by clause:
--
--   SELECT checkpoint.cursor FROM agri.job_checkpoint AS checkpoint
--     The checkpoint table is append-only -- one row per saved step, never updated -- so reading
--     the latest state means picking the newest row rather than reading a mutable column.
--
--   WHERE checkpoint.job_work_item_id = work_item_id
--     Narrows to one shard's checkpoint history.
--
--   ORDER BY checkpoint.sequence DESC
--     sequence is a per-shard counter that goes up by one on every checkpoint, so sorting it
--     descending puts the newest first. It is used rather than a timestamp because it is exact:
--     two checkpoints written in the same clock tick still have distinct sequences.
--
--   LIMIT 1
--     Only the newest matters. Everything older describes ground already covered.
SELECT checkpoint.cursor
FROM agri.job_checkpoint AS checkpoint
WHERE checkpoint.job_work_item_id = :work_item_id
ORDER BY checkpoint.sequence DESC
LIMIT 1
