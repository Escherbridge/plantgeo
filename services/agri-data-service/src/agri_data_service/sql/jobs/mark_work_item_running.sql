-- mark_work_item_running
-- Purpose: move a shard this worker has just claimed from 'leased' to 'running', and push its
--          lease out, so a crash tells an operator whether the handler ever actually started.
-- Loaded by: agri_data_service.jobs.lease
-- Params: work_item_id (uuid) -- the shard being started.
--         fencing_token (int) -- the token this worker was given at claim time.
--         lease_owner (text) -- this worker's id, as written into lease_owner by the claim.
--         lease_seconds (double precision) -- how far into the future to push the lease.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row when this worker still owns the shard, and NO rows when it does not.
-- Zero rows is not an error condition to retry -- it is the fence telling this worker that
-- somebody else now owns the shard and it must stop touching it.
--
-- 'leased' and 'running' are deliberately distinct states. 'leased' means a worker took the shard
-- but had not yet entered the handler; 'running' means the handler is executing. When a container
-- dies, which of the two the row is left in is the difference between "we never got started" and
-- "we were part-way through", and that distinction is otherwise unrecoverable.
--
-- How this query works, clause by clause:
--
--   WHERE id = work_item_id AND fencing_token = fencing_token AND lease_owner = lease_owner
--     THE FENCE. These three columns together are the entire authority a worker has over a shard,
--     and every write this worker makes repeats them. The fencing token is a counter on the item
--     row that goes up by one on every claim and is never reset, so if another worker has claimed
--     this shard in the meantime the stored token has moved past the copy this worker is holding,
--     the predicate matches nothing, and the write silently does nothing instead of corrupting
--     the newer owner's work. The lease_owner check makes the same point about identity, and
--     matching the id is what makes it one shard rather than all of them.
--
--   AND status IN ('leased', 'running')
--     A shard that has already reached a terminal state -- succeeded, dead-lettered, cancelled --
--     must not be dragged back to 'running' by a straggler. Re-running this on an already-running
--     shard is harmless, which keeps the statement safely repeatable.
--
--   lease_expires_at = now() + make_interval(secs => lease_seconds)
--     Interval arithmetic: make_interval(secs => N) builds a span of N seconds, and adding it to
--     now() (the database's clock, never the worker's) gives the new expiry. make_interval is used
--     rather than the literal INTERVAL syntax because a bound parameter cannot be placed inside a
--     quoted interval literal.
--
--   heartbeat_at = now()
--     A liveness stamp for operators and dashboards. It is distinct from the lease: the lease
--     decides who may touch the row, the heartbeat only records when something last did.
--
--   RETURNING id
--     RETURNING hands back a row per row actually updated, in the same round trip. The caller does
--     not want the id -- it wants the count. One row means the fence held; zero means it moved.
UPDATE agri.job_work_item
SET status = 'running',
    lease_expires_at = now() + make_interval(secs => :lease_seconds),
    heartbeat_at = now()
WHERE id = :work_item_id
  AND fencing_token = :fencing_token
  AND lease_owner = :lease_owner
  AND status IN ('leased', 'running')
RETURNING id
