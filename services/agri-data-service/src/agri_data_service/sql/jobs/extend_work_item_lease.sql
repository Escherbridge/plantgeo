-- extend_work_item_lease
-- Purpose: push a held shard's lease further into the future and stamp a heartbeat, so a worker
--          that is still making progress does not have its shard reclaimed out from under it.
-- Loaded by: agri_data_service.jobs.lease
-- Params: work_item_id (uuid) -- the shard whose lease is being renewed.
--         fencing_token (int) -- the token this worker was given at claim time.
--         lease_owner (text) -- this worker's id, as written into lease_owner by the claim.
--         lease_seconds (double precision) -- how far past now() the new expiry sits.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row while this worker still owns the shard, and NO rows once it does
-- not. Zero rows back means the fence moved -- another worker owns this shard now -- and this
-- one must stop working on it immediately. That is the whole point of the statement: it is a
-- liveness renewal and an ownership check in a single round trip.
--
-- This is the "heartbeat" a long-running handler calls periodically. A lease is a rental with an
-- expiry, and a handler that will take longer than one lease period must keep renewing or the
-- reaper will hand its window to somebody else while it is still working on it.
--
-- How this query works, clause by clause:
--
--   WHERE id = work_item_id AND fencing_token = fencing_token AND lease_owner = lease_owner
--     THE FENCE. These three columns together are the entire authority a worker has over a shard.
--     The fencing token is a counter on the item row, bumped on every claim and never reset, so
--     once another worker has claimed this shard the stored token is past the copy this worker
--     holds, the predicate matches nothing, and the renewal quietly fails rather than extending a
--     lease this worker no longer owns.
--
--   AND status IN ('leased', 'running')
--     Only a shard actually in hand can have its lease renewed. A settled shard has no lease left
--     to extend, and reviving one would produce a row that lies about who owns it.
--
--   SET lease_expires_at = now() + make_interval(secs => lease_seconds)
--     Interval arithmetic: make_interval(secs => N) builds a span of N seconds and adding it to
--     now() gives the new expiry. now() is the database's clock, never the worker's, because two
--     containers with skewed clocks must never disagree about whether a lease has lapsed.
--     make_interval is used rather than the literal INTERVAL syntax because a bound parameter
--     cannot appear inside a quoted interval literal.
--
--   lease_owner is deliberately NOT written
--     It already holds this worker's id -- the fence above proved it -- and leaving it untouched
--     is what keeps ck_job_work_item_complete_lease_pair satisfied, which requires the owner and
--     the expiry to be present or absent together.
--
--   RETURNING id
--     RETURNING hands back one row per row actually updated, in the same round trip. The caller
--     reads the presence or absence of that row as "fence held" or "fence lost"; the id itself is
--     not used.
UPDATE agri.job_work_item
SET heartbeat_at = now(),
    lease_expires_at = now() + make_interval(secs => :lease_seconds)
WHERE id = :work_item_id
  AND fencing_token = :fencing_token
  AND lease_owner = :lease_owner
  AND status IN ('leased', 'running')
RETURNING id
