-- close_attempt_deferred
-- Purpose: close this worker's attempt as 'deferred' -- parked, not failed -- while the worker
--          still demonstrably owns the shard.
-- Loaded by: agri_data_service.jobs.lease
-- Params: work_item_id (uuid) -- the shard whose ownership is being proved.
--         fencing_token (int) -- the token this worker was given at claim time.
--         lease_owner (text) -- this worker's id, as written into lease_owner by the claim.
--         attempt_id (uuid) -- the attempt row being closed.
--         metrics (text holding JSON) -- what the handler managed, plus the park's own reason.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row when the attempt was closed, NO rows when the fence had already
-- moved.
--
-- A DEFERRAL IS NOT A FAILURE, and the columns reflect that. The reason a shard parked travels
-- inside the free-form metrics object rather than in error_summary, because error_summary is what
-- an operator scans when hunting real failures, and a weekly source politely saying "nothing new
-- yet" does not belong in that list.
--
-- It is fenced through the ITEM row, exactly as close_attempt_succeeded is. 'deferred' is terminal
-- FOR THE ATTEMPT even though it is not terminal for the shard, so a fenced-out worker parking
-- somebody else's shard would still be writing a verdict it had no authority to reach. See
-- close_attempt_succeeded.sql for the full argument about why the fence must be read off the item
-- row rather than the attempt row, and why the row is locked rather than merely joined.
--
-- How this query works, clause by clause:
--
--   WITH fence AS (...)
--     A CTE ("common table expression") -- a named subquery written up front and referenced below
--     like a table. It exists to prove ownership rather than to fetch data: one row if this worker
--     still owns the shard, no rows otherwise, and an UPDATE joined to an empty CTE changes
--     nothing, so a lost fence becomes a silent no-op instead of a wrong write.
--
--   WHERE item.fencing_token = fencing_token AND item.lease_owner = lease_owner
--     THE FENCE. The fencing token is a counter on the ITEM row, bumped by every claim and never
--     reset. Comparing the attempt's own copy of the token instead would be a tautology -- nothing
--     ever updates it -- so the item's column is the only one that can fence anything.
--
--   FOR UPDATE
--     Locks the matched item row for the rest of the transaction. Under READ COMMITTED isolation a
--     plain join could read the item as it looked before a competing claim committed; the lock
--     makes PostgreSQL wait for that claim and re-check this WHERE against what it wrote, at which
--     point it matches zero. The transaction-local statement_timeout bounds the wait.
--
--   UPDATE agri.job_attempt AS attempt ... FROM fence WHERE attempt.job_work_item_id = fence.id
--     Ties "this attempt may be parked" to "this worker still owns its shard".
--
--   AND attempt.status = 'running'
--     Makes the close one-way; an attempt that already reached a verdict is never relabelled.
--
--   metrics = CAST(metrics AS jsonb)
--     Stored as jsonb, PostgreSQL's parsed JSON type. The CAST pins the parameter's type, because
--     a bare bound parameter gives the planner no column context, and parsing means a malformed
--     payload is refused rather than stored.
--
--   RETURNING attempt.id
--     RETURNING hands back a row per row actually updated, in the same round trip, so the caller
--     learns whether the fence held without a follow-up query.
WITH fence AS (
    SELECT item.id
    FROM agri.job_work_item AS item
    WHERE item.id = :work_item_id
      AND item.fencing_token = :fencing_token
      AND item.lease_owner = :lease_owner
      AND item.status IN ('leased', 'running')
    FOR UPDATE
)
UPDATE agri.job_attempt AS attempt
SET status = 'deferred', finished_at = now(), metrics = CAST(:metrics AS jsonb)
FROM fence
WHERE attempt.id = :attempt_id
  AND attempt.job_work_item_id = fence.id
  AND attempt.status = 'running'
RETURNING attempt.id
