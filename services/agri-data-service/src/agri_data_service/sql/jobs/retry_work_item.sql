-- retry_work_item
-- Purpose: park a failed shard in a timed backoff so a later tick picks it up again, recording
--          why it failed, while this worker still owns it.
-- Loaded by: agri_data_service.jobs.lease
-- Params: work_item_id (uuid) -- the shard being scheduled for another try.
--         fencing_token (int) -- the token this worker was given at claim time.
--         lease_owner (text) -- this worker's id, as written into lease_owner by the claim.
--         backoff_seconds (double precision) -- how long to wait before the shard is claimable
--         again, computed from the lane's retry policy and this attempt's number.
--         failure_class (text) -- short machine-readable label, redacted and clamped by the caller
--         because the column is VARCHAR(255) and an over-long value would abort the write.
--         error_summary (text) -- one operator-facing sentence, likewise redacted and clamped.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row when the shard was parked for retry, NO rows when the fence had
-- already moved and this worker had no authority to schedule anything.
--
-- This is the non-terminal half of the failure path. Its counterpart, dead_letter_work_item, runs
-- instead when the shard's attempt budget is spent. The difference is only in what is set: this
-- statement leaves the shard claimable again later, that one does not.
--
-- How this query works, clause by clause:
--
--   WHERE id = work_item_id AND fencing_token = fencing_token AND lease_owner = lease_owner
--     THE FENCE -- the three columns that together are this worker's whole authority over the
--     shard. The fencing token is a counter bumped by every claim and never reset, so a worker
--     that has been superseded holds a stale copy, matches nothing, and cannot reschedule a shard
--     somebody else is now actively working.
--
--   AND status IN ('leased', 'running')
--     Only a shard actually in hand can be sent back to the queue.
--
--   SET status = 'retry_wait'
--     A distinct state from 'queued' on purpose: it means "failed, waiting out a backoff", which
--     is what makes a lane's failures visible in a status report rather than blending into the
--     backlog.
--
--   SET next_attempt_at = now() + make_interval(secs => backoff_seconds)
--     The moment the shard becomes claimable again. make_interval(secs => N) builds a span of N
--     seconds; adding it to now() -- the DATABASE clock, never the worker's -- gives the wake-up
--     time. make_interval is used rather than the literal INTERVAL syntax because a bound
--     parameter cannot appear inside a quoted interval literal. claim_work_item's fresh arm reads
--     exactly this column to decide whether the wait is over.
--
--   SET lease_owner = NULL, lease_expires_at = NULL
--     The rental ends here. These two must move TOGETHER, because
--     ck_job_work_item_complete_lease_pair requires the owner and the expiry to be both present or
--     both absent; clearing one alone aborts the statement.
--
--   SET last_error_class = failure_class, last_error_summary = error_summary
--     The most recent failure, kept on the shard row so an operator listing stuck windows sees why
--     each one is stuck without joining to the attempt history.
--
--   RETURNING id
--     RETURNING hands back a row per row actually updated, in the same round trip. The caller reads
--     presence or absence of that row as "scheduled" or "fence lost".
UPDATE agri.job_work_item
SET status = 'retry_wait',
    next_attempt_at = now() + make_interval(secs => :backoff_seconds),
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
