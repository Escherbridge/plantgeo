-- advance_checkpoint_sequence
-- Purpose: reserve the next checkpoint number for this shard, raise its high-water progress mark,
--          and renew its lease -- all under the fence, in one statement.
-- Loaded by: agri_data_service.jobs.lease
-- Params: work_item_id (uuid) -- the shard about to record a checkpoint.
--         fencing_token (int) -- the token this worker was given at claim time.
--         lease_owner (text) -- this worker's id, as written into lease_owner by the claim.
--         progress_fraction (double precision) -- how far through the window this step reached,
--         between 0 and 1.
--         lease_seconds (double precision) -- how far past now() to push the lease.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row holding the newly reserved checkpoint sequence number, or NO rows if
-- the fence has moved -- in which case the caller abandons the shard and never writes the
-- checkpoint row that would have used the number.
--
-- WHY THE SEQUENCE COMES FROM THE ITEM ROW. The number is taken from a counter on the shard's own
-- row, under that row's lock, and NEVER by asking the checkpoint table for its largest existing
-- sequence. Two writers reading such an aggregate would derive the same N and one of them would die
-- on uq_job_checkpoint_item_sequence. Incrementing the item's counter serialises the two through
-- the row lock instead, so they get N and N+1. This UPDATE must therefore run BEFORE the checkpoint
-- INSERT, for exactly that reason -- the number has to be reserved before it is used.
--
-- That rejected alternative is described in words above and deliberately NOT written out as an
-- aggregate expression. This whole file is the statement text -- comments included -- and a unit
-- test asserts that spelling appears nowhere in this statement, precisely because its absence is
-- what proves the sequence is not derived that way. Paraphrase it; never paste it back.
--
-- How this query works, clause by clause:
--
--   SET checkpoint_sequence = checkpoint_sequence + 1
--     Reserves the next number. Reading the new value back through RETURNING is what makes the
--     reservation and the read a single atomic step.
--
--   SET progress_fraction = GREATEST(progress_fraction, CAST(progress_fraction AS double precision))
--     GREATEST returns the largest of its arguments, so the stored value can rise but never fall.
--     This matters because progress is a HIGH-WATER MARK on the item, not a running value: a
--     parked outcome may carry a resume cursor and still report a fraction of zero -- a deferral
--     or a budget yield states where it resumes, not how far it got -- and assigning that raw
--     would rewind the shard's progress every time it waits. The checkpoint row written next keeps
--     the raw per-step value; this column keeps the furthest point ever reached.
--     The CAST exists purely to pin the parameter's type. A bare bound parameter inside GREATEST
--     gives PostgreSQL no column context to infer from, so it is told explicitly.
--
--   WHERE id = work_item_id AND fencing_token = fencing_token AND lease_owner = lease_owner
--     THE FENCE. These three columns together are the whole of this worker's authority over the
--     shard. The fencing token is a counter bumped on every claim and never reset, so once another
--     worker has claimed this shard the stored token is past this worker's copy, the predicate
--     matches nothing, and no sequence is consumed for a checkpoint that must not be written.
--
--   AND status IN ('leased', 'running')
--     A settled shard takes no more checkpoints.
--
--   lease_expires_at = now() + make_interval(secs => lease_seconds)
--     Checkpointing is progress, so it also renews the rental. make_interval(secs => N) builds a
--     span of N seconds; adding it to now() (the database's clock, never the worker's) gives the
--     new expiry. make_interval is used rather than the literal INTERVAL syntax because a bound
--     parameter cannot appear inside a quoted interval literal.
--
--   RETURNING checkpoint_sequence
--     RETURNING hands back a column of the row actually updated, in the same round trip. This is
--     the reserved number the checkpoint INSERT will carry; no rows means the fence moved.
UPDATE agri.job_work_item
SET checkpoint_sequence = checkpoint_sequence + 1,
    progress_fraction = GREATEST(progress_fraction, CAST(:progress_fraction AS double precision)),
    heartbeat_at = now(),
    lease_expires_at = now() + make_interval(secs => :lease_seconds)
WHERE id = :work_item_id
  AND fencing_token = :fencing_token
  AND lease_owner = :lease_owner
  AND status IN ('leased', 'running')
RETURNING checkpoint_sequence
