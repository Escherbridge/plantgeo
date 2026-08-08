-- complete_work_item
-- Purpose: settle a shard as succeeded -- final progress, no lease, no next attempt -- while this
--          worker still owns it.
-- Loaded by: agri_data_service.jobs.lease
-- Params: work_item_id (uuid) -- the shard being settled.
--         fencing_token (int) -- the token this worker was given at claim time.
--         lease_owner (text) -- this worker's id, as written into lease_owner by the claim.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row when the shard was settled, NO rows when the fence had already
-- moved. This statement is the AUTHORITY on whether the completion happened -- the attempt close
-- that ran just before it carries an identical fence, so a fence loss matches zero rows in both,
-- and reading the verdict off the row whose status actually decides the shard keeps one answer
-- rather than two that must be argued equal.
--
-- The ordering around it is deliberate: attempt first, then this, then the run counter. A crash
-- between any two of them leaves the shard still leased and re-drivable, rather than leaving a
-- completed shard sitting behind an attempt that still claims to be running.
--
-- How this query works, clause by clause:
--
--   WHERE id = work_item_id AND fencing_token = fencing_token AND lease_owner = lease_owner
--     THE FENCE -- the three columns that together are this worker's whole authority over the
--     shard. The fencing token is a counter on this row bumped by every claim and never reset, so
--     a superseded worker holds a stale copy, matches nothing, and cannot mark somebody else's
--     in-flight work as finished.
--
--   AND status IN ('leased', 'running')
--     Only a shard actually in hand can be completed. A shard already settled some other way is
--     left exactly as it is.
--
--   SET status = 'succeeded', completed_at = now()
--     The terminal state and the moment it was reached. completed_at is written on every terminal
--     path, which is what makes it a sound axis for throughput reporting elsewhere.
--
--   SET progress_fraction = 1
--     Finished means finished. Unlike the checkpoint path this is not clamped upward, because
--     1 is already the ceiling.
--
--   SET lease_owner = NULL, lease_expires_at = NULL
--     The rental is given back. These two must move TOGETHER: ck_job_work_item_complete_lease_pair
--     requires the owner and the expiry to be both present or both absent, and clearing only one
--     of them would abort the statement.
--
--   SET next_attempt_at = NULL
--     Nothing further is scheduled; the shard is done.
--
--   RETURNING id
--     RETURNING hands back a row per row actually updated, in the same round trip. The caller
--     reads presence or absence of that row as success or fence-loss.
UPDATE agri.job_work_item
SET status = 'succeeded',
    completed_at = now(),
    progress_fraction = 1,
    lease_owner = NULL,
    lease_expires_at = NULL,
    next_attempt_at = NULL,
    heartbeat_at = now()
WHERE id = :work_item_id
  AND fencing_token = :fencing_token
  AND lease_owner = :lease_owner
  AND status IN ('leased', 'running')
RETURNING id
