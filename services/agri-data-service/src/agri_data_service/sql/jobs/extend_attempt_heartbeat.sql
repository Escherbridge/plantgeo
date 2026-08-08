-- extend_attempt_heartbeat
-- Purpose: stamp "this attempt is still alive" on the attempt row, immediately after the shard's
--          own lease has been successfully renewed.
-- Loaded by: agri_data_service.jobs.lease
-- Params: attempt_id (uuid) -- the attempt row this worker opened for the shard it holds.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row when the attempt was still open and got its stamp, none when it was
-- not. The caller ignores the answer, and that is correct: the shard's lease renewal has already
-- decided whether this worker may keep going. This write is only for the operator-facing picture
-- of which attempts are live.
--
-- It carries no fence, and needs none. Fencing exists to stop a superseded worker writing over
-- a live shard; here the only column touched is a timestamp on THIS worker's OWN attempt row,
-- addressed by its primary key. There is nobody else's state to corrupt.
--
-- How this query works, clause by clause:
--
--   WHERE id = attempt_id
--     The attempt's primary key -- exactly one row, this worker's own.
--
--   AND status = 'running'
--     An attempt that has already reached a verdict (succeeded, failed, deferred, lost) is
--     finished, and stamping a fresh heartbeat on it would make a closed attempt look live to
--     every liveness query. This is what keeps the statement harmless if it arrives late.
--
--   RETURNING id
--     RETURNING hands back a row per row actually updated, in the same round trip, so the write
--     needs no follow-up query to be observable. Nothing downstream reads it here.
UPDATE agri.job_attempt
SET heartbeat_at = now()
WHERE id = :attempt_id AND status = 'running'
RETURNING id
