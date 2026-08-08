-- close_attempt_succeeded
-- Purpose: mark this worker's attempt on a shard as succeeded and store its metrics, but only
--          while the worker still demonstrably owns the shard.
-- Loaded by: agri_data_service.jobs.lease
-- Params: work_item_id (uuid) -- the shard whose ownership is being proved.
--         fencing_token (int) -- the token this worker was given at claim time.
--         lease_owner (text) -- this worker's id, as written into lease_owner by the claim.
--         attempt_id (uuid) -- the attempt row being closed.
--         metrics (text holding JSON) -- what the handler counted, as canonical JSON.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row when the attempt was closed, and NO rows when the fence had already
-- moved. Callers roll the transaction back on an empty answer; see jobs/AGENTS.md "The fence
-- guards the attempt too".
--
-- THE FENCE LIVES ON THE ITEM, NOT ON THE ATTEMPT. That is the single fact this statement's shape
-- exists to respect, and it is worth reading before changing any of the three close_attempt_*
-- writes, because the obvious cheaper spelling does not work:
--
--   Writing "AND attempt.fencing_token = fencing_token" would be a TAUTOLOGY. The open_attempt
--   INSERT stamps the attempt row with exactly the token the claim minted, and nothing ever
--   updates that column afterwards, so such a predicate matches its own row for ever and fences
--   nothing at all. The only column that MOVES when another worker takes the shard is
--   job_work_item.fencing_token, so that is the column every fenced write must compare against.
--
-- How this query works, clause by clause:
--
--   WITH fence AS (...)
--     A CTE ("common table expression") -- a named subquery written up front and referenced below
--     like a table. This one does not fetch data for its own sake; it exists to LOCK the shard's
--     row and prove, at this instant, that this worker still owns it. It yields one row if the
--     proof holds and no rows if it does not, and an UPDATE joined to an empty CTE changes
--     nothing. That is how a lost fence turns into a silent no-op rather than a wrong write.
--
--   WHERE item.id = work_item_id AND item.fencing_token = fencing_token
--     AND item.lease_owner = lease_owner AND item.status IN ('leased', 'running')
--     THE FENCE ITSELF. The fencing token is a counter on the item row bumped by every claim and
--     never reset, so a worker whose shard was taken away is holding a stale copy and matches
--     nothing. The owner check pins identity; the status check refuses to reopen a settled shard.
--
--   FOR UPDATE
--     Takes a write lock on the matched row and holds it to the end of the transaction, and it is
--     load-bearing rather than decorative. Under READ COMMITTED isolation each statement takes its
--     own snapshot, so a plain "UPDATE ... FROM job_work_item" could read the item as it looked
--     BEFORE a competing claim committed and close the attempt anyway. Locking the row makes
--     PostgreSQL block on the competing claim, then re-evaluate this WHERE against the row that
--     claim actually committed -- at which point it matches zero and the write correctly does
--     nothing. The item UPDATE that the caller issues one statement later wants this same lock
--     regardless, so this MOVES the wait rather than adding one, and the transaction-local
--     statement_timeout bounds how long the block can last.
--
--   UPDATE agri.job_attempt AS attempt ... FROM fence WHERE attempt.job_work_item_id = fence.id
--     Joins the attempt to the proof. The attempt is only updated if the fence CTE produced the
--     row it belongs to, which is what ties "this attempt may be closed" to "this worker still
--     owns its shard".
--
--   AND attempt.id = attempt_id AND attempt.status = 'running'
--     Addresses this worker's own open attempt and no other. The status test makes the close
--     one-way: an attempt that already reached a verdict is never relabelled.
--
--   metrics = CAST(metrics AS jsonb)
--     The handler's counters, stored as jsonb (PostgreSQL's parsed JSON type). The CAST pins the
--     parameter's type -- a bare bound parameter gives the planner no column context to infer
--     from -- and parses the text so a malformed payload is rejected rather than stored.
--
--   RETURNING attempt.id
--     RETURNING hands back a row per row actually updated, in the same round trip, so the caller
--     learns whether the fence held without a second query.
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
SET status = 'succeeded', finished_at = now(), metrics = CAST(:metrics AS jsonb)
FROM fence
WHERE attempt.id = :attempt_id
  AND attempt.job_work_item_id = fence.id
  AND attempt.status = 'running'
RETURNING attempt.id
