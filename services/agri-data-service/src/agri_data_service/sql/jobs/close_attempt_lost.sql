-- close_attempt_lost
-- Purpose: let a worker that has just discovered it was fenced out close its OWN attempt as
--          'lost', so the row stops looking like live work.
-- Loaded by: agri_data_service.jobs.lease
-- Params: attempt_id (uuid) -- this worker's own attempt row.
--         failure_class (text) -- short machine-readable label, here always the fence-lost class.
--         error_summary (text) -- one operator-facing sentence explaining the supersession.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row when the attempt was still open and got closed, none when it had
-- already been closed by somebody else -- typically by close_superseded_attempts, which reaps the
-- same orphan from the other direction when the worker never comes back at all.
--
-- WHY IT EXISTS. Nothing in the schema reaps an abandoned attempt. Without this statement a
-- fenced-out worker's attempt row sits in 'running' for ever, and every incident query that counts
-- running attempts lies about how much work is in flight.
--
-- THIS IS THE ONE ATTEMPT-CLOSE THAT MUST NOT CARRY A FENCE, and the exception is the entire point.
-- It runs precisely at the moment the fence HAS ALREADY MOVED, so fencing it the way
-- close_attempt_succeeded, close_attempt_failed and close_attempt_deferred are fenced would match
-- zero rows and leave behind the dangling 'running' attempt it exists to reap. It is safe unfenced
-- for a reason that does not generalise to those three: 'lost' is the ONLY verdict a superseded
-- worker is ever entitled to reach about its own attempt, and addressing the row by attempt_id
-- touches that attempt and no other. Nothing here can reach another worker's state.
--
-- No metrics are written on this path, deliberately. A fenced-out attempt's counters describe work
-- whose durability now belongs to whoever took the shard, and recording them here would invite
-- reading them as this shard's progress.
--
-- How this query works, clause by clause:
--
--   UPDATE agri.job_attempt SET status = 'lost', finished_at = now()
--     'lost' means "this attempt was superseded and nobody is running it" -- not a success, and
--     not a failure the handler reported. Stamping finished_at takes it out of the open-attempt
--     population that liveness queries read.
--
--   WHERE id = attempt_id
--     The attempt's primary key: exactly one row, this worker's own.
--
--   AND status = 'running'
--     Makes the close one-way and safely repeatable. An attempt that already reached a verdict --
--     including one already reaped as 'lost' by the claim that superseded it -- is never
--     relabelled.
--
--   RETURNING id
--     RETURNING hands back a row per row actually updated, in the same round trip. The caller does
--     not branch on it; the write is best-effort tidying.
UPDATE agri.job_attempt
SET status = 'lost',
    finished_at = now(),
    failure_class = :failure_class,
    error_summary = :error_summary
WHERE id = :attempt_id AND status = 'running'
RETURNING id
