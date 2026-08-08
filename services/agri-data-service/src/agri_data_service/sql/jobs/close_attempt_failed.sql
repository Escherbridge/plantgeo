-- close_attempt_failed
-- Purpose: record this worker's attempt as failed -- with its failure class, its summary AND its
--          metrics -- while the worker still demonstrably owns the shard.
-- Loaded by: agri_data_service.jobs.lease
-- Params: work_item_id (uuid) -- the shard whose ownership is being proved.
--         fencing_token (int) -- the token this worker was given at claim time.
--         lease_owner (text) -- this worker's id, as written into lease_owner by the claim.
--         attempt_id (uuid) -- the attempt row being closed.
--         failure_class (text) -- short machine-readable label, already redacted and clamped to
--         the column's width by the caller.
--         error_summary (text) -- one operator-facing sentence, likewise redacted and clamped.
--         metrics (text holding JSON) -- what the handler managed before it failed.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row when the attempt was closed, NO rows when the fence had already
-- moved.
--
-- METRICS TRAVEL WITH THE FAILURE, and that is a deliberate choice rather than symmetry for its
-- own sake. What a dead-lettered window actually managed -- chunks walked, records seen, records
-- written -- is exactly what an operator needs in order to tell "upstream had nothing" from "we
-- never reached upstream", and dropping it on the failure path leaves that question unanswerable
-- from the ledger alone. The column is NOT NULL DEFAULT with an empty object, so writing an empty
-- object is always legal.
--
-- It is fenced through the ITEM row, exactly as close_attempt_succeeded is, and for the same
-- reason: 'failed' is a terminal verdict on an attempt, so writing it while another worker owns
-- the shard records a judgement this worker had no authority to reach. See
-- close_attempt_succeeded.sql for the full argument about why the fence must be read off the item
-- row rather than the attempt row, and why the lock is taken rather than joined.
--
-- How this query works, clause by clause:
--
--   WITH fence AS (...)
--     A CTE ("common table expression") -- a named subquery written up front and used below like a
--     table. It exists to prove ownership, not to fetch data: it yields one row if this worker
--     still owns the shard and no rows otherwise, and an UPDATE joined to an empty CTE changes
--     nothing, so a lost fence becomes a silent no-op instead of a wrong write.
--
--   WHERE item.fencing_token = fencing_token AND item.lease_owner = lease_owner
--     THE FENCE. The fencing token is a counter on the item row bumped by every claim and never
--     reset; a superseded worker holds a stale copy and matches nothing. Note this compares the
--     ITEM's token -- comparing the attempt's own token would match its own row for ever and fence
--     nothing at all.
--
--   FOR UPDATE
--     Locks the matched item row until the transaction ends. Under READ COMMITTED isolation a
--     plain join could read the item as it looked before a competing claim committed; the lock
--     makes PostgreSQL wait for that claim and then re-check this WHERE against what it actually
--     wrote, at which point it matches zero. The statement_timeout bounds the wait.
--
--   UPDATE agri.job_attempt AS attempt ... FROM fence WHERE attempt.job_work_item_id = fence.id
--     Ties "this attempt may be closed" to "this worker still owns its shard".
--
--   AND attempt.status = 'running'
--     Makes the close one-way. An attempt that already reached a verdict is never relabelled.
--
--   metrics = CAST(metrics AS jsonb)
--     Stored as jsonb, PostgreSQL's parsed JSON type. The CAST pins the parameter's type, since a
--     bare bound parameter gives the planner no column context, and parsing means a malformed
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
SET status = 'failed',
    finished_at = now(),
    failure_class = :failure_class,
    error_summary = :error_summary,
    metrics = CAST(:metrics AS jsonb)
FROM fence
WHERE attempt.id = :attempt_id
  AND attempt.job_work_item_id = fence.id
  AND attempt.status = 'running'
RETURNING attempt.id
