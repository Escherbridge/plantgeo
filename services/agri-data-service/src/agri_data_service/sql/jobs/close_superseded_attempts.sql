-- close_superseded_attempts
-- Purpose: mark as 'lost' every still-'running' attempt row on this shard that belongs to a
--          worker the claim just superseded, so nothing counts a dead worker as live work.
-- Loaded by: agri_data_service.jobs.lease
-- Params: work_item_id (uuid) -- the shard whose abandoned attempts are being reaped.
--         fencing_token (int) -- the token the CLAIM THAT JUST RAN was given. Everything
--         strictly below it belongs to a superseded worker.
--         failure_class (text) -- short machine-readable label for why the attempt ended.
--         error_summary (text) -- one operator-facing sentence, already redacted and clamped.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row per attempt it closed, usually none. On a shard's first claim there
-- is nothing to reap; on a claim that took over an expired lease there is normally exactly one.
--
-- WHY THIS EXISTS AT ALL. The expired-lease arm of claim_work_item takes a shard whose previous
-- owner never came back, and that owner's job_attempt row is still sitting in 'running'. NOTHING
-- ELSE EVER CLOSES IT. The tidy path, release_lost_attempt, runs on the losing worker -- which in
-- the crash case is the process that died. close_lost_attempts is bound only to the items the
-- reaper itself reclaimed in the same pass, and the reaper runs once per slice, at the top, so a
-- lease that expires after that point and is then taken by the claim leaks its attempt for ever.
-- There is no foreign key, CHECK or trigger tying job_attempt.status to job_work_item.status, so
-- nothing in the schema will clean it up either, and every liveness query that counts running
-- attempts would over-report live work monotonically, without bound. See jobs/AGENTS.md
-- "Nothing reaps an orphaned attempt".
--
-- WHY IT CARRIES NO FENCE, deliberately. Most writes in this package prove ownership by matching
-- the shard's current fencing token. This one cannot: it runs precisely at the moment the fence
-- has already moved, so a fenced version would match zero rows and leave behind the very row it
-- exists to reap. It is bounded a different way instead -- see the WHERE clause below.
--
-- How this query works, clause by clause:
--
--   UPDATE agri.job_attempt SET status = 'lost', finished_at = now(), ...
--     'lost' is the verdict for an attempt whose worker vanished: not a success, not a failure it
--     reported, simply an attempt nobody is running any more. Stamping finished_at is what takes
--     it out of the "open attempts" population.
--
--   WHERE job_work_item_id = work_item_id
--     Only attempts on the shard this claim just took. Other shards are none of this claim's
--     business.
--
--   AND status = 'running'
--     Only attempts still believed to be live. An attempt already closed as succeeded, failed,
--     deferred or lost has reached a terminal verdict and must never be relabelled.
--
--   AND fencing_token < fencing_token
--     The bound that replaces the fence, and the safety argument in one line. It addresses only
--     STRICTLY superseded attempts -- those stamped with a token older than the one this claim
--     just minted. The attempt this claim is about to open carries the new token, so no later
--     replay of this statement can ever close a live attempt: the live one is never strictly
--     below the current token.
--
--   RETURNING id
--     RETURNING hands back a row per attempt actually closed, in the same round trip, so the
--     caller can see what it reaped without a second query. Zero rows is the common case.
UPDATE agri.job_attempt
SET status = 'lost',
    finished_at = now(),
    failure_class = :failure_class,
    error_summary = :error_summary
WHERE job_work_item_id = :work_item_id
  AND status = 'running'
  AND fencing_token < :fencing_token
RETURNING id
