-- dead_letter_work_item
-- Purpose: settle a shard whose attempt budget is spent into the terminal 'dead_letter' state,
--          carrying the failure that ended it, while this worker still owns it.
-- Loaded by: agri_data_service.jobs.lease
-- Params: work_item_id (uuid) -- the shard being given up on.
--         fencing_token (int) -- the token this worker was given at claim time.
--         lease_owner (text) -- this worker's id, as written into lease_owner by the claim.
--         failure_class (text) -- short machine-readable label, redacted and clamped by the caller
--         because the column is VARCHAR(255) and an over-long value would abort the write.
--         error_summary (text) -- one operator-facing sentence, likewise redacted and clamped.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row when the shard was dead-lettered, NO rows when the fence had already
-- moved.
--
-- A dead letter is a LOUD ending, never a quiet one. Exhaustion could in principle be recorded as
-- some kind of "done", but then a completeness report that walks a lane's shard keys would count
-- the window as finished. Leaving it visibly unfinished is what makes such a report say the window
-- is MISSING, which is the true statement and the one that gets it looked at.
--
-- How this query works, clause by clause:
--
--   WHERE id = work_item_id AND fencing_token = fencing_token AND lease_owner = lease_owner
--     THE FENCE -- the three columns that together are this worker's whole authority over the
--     shard. The fencing token is a counter bumped by every claim and never reset, so a superseded
--     worker holds a stale copy, matches nothing, and cannot condemn a shard another worker is
--     currently making progress on.
--
--   AND status IN ('leased', 'running')
--     Only a shard actually in hand can be settled.
--
--   SET status = 'dead_letter', completed_at = now()
--     The terminal state and the moment it was reached. completed_at is written on both terminal
--     paths -- succeeded and dead-lettered -- which is what makes it a sound axis for throughput
--     and recency reporting elsewhere.
--
--   SET next_attempt_at = NULL
--     Nothing more is scheduled. A dead-lettered shard is never claimed again without an operator
--     deliberately reopening it.
--
--   SET lease_owner = NULL, lease_expires_at = NULL
--     The rental ends. These two must move TOGETHER, because
--     ck_job_work_item_complete_lease_pair requires the owner and the expiry to be both present or
--     both absent; clearing one alone aborts the statement.
--
--   SET last_error_class = failure_class, last_error_summary = error_summary
--     The failure that ended it, kept on the shard row so an operator listing dead letters reads
--     the reason without joining to the attempt history.
--
--   RETURNING id
--     RETURNING hands back a row per row actually updated, in the same round trip. The caller reads
--     presence or absence of that row as "settled" or "fence lost".
UPDATE agri.job_work_item
SET status = 'dead_letter',
    completed_at = now(),
    next_attempt_at = NULL,
    lease_owner = NULL,
    lease_expires_at = NULL,
    heartbeat_at = now(),
    last_error_class = :failure_class,
    last_error_summary = :error_summary
WHERE id = :work_item_id
  AND fencing_token = :fencing_token
  AND lease_owner = :lease_owner
  AND status IN ('leased', 'running')
RETURNING id
