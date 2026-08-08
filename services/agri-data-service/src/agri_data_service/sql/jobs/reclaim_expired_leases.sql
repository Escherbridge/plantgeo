-- reclaim_expired_leases
-- Purpose: find every shard whose lease has lapsed with nobody coming back for it, return it to
--          the queue (or dead-letter it if its budget is spent), and report what it did.
-- Loaded by: agri_data_service.jobs.lease
-- Params: job_run_id (uuid, nullable) -- narrow to one run; NULL means "do not narrow this way".
--         job_definition_id (uuid, nullable) -- narrow to every non-terminal run of one lane;
--         NULL means "do not narrow this way". The two scopes are independent and both optional.
--         backoff_seconds (double precision) -- how long a reclaimed shard waits before it is
--         claimable again.
--         failure_class (text) -- short machine-readable label recorded on each reclaimed shard.
--         error_summary (text) -- one operator-facing sentence, likewise recorded.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row per shard reclaimed -- its id, its run, and the state it landed in --
-- so the caller can count requeues against dead letters and close the matching dangling attempts.
-- No rows means there were no expired leases, which is the healthy case.
--
-- THE REAPER RUNS WITHOUT A FENCE, because there is no fence to hold: by definition every
-- candidate row's owner is gone. It also deliberately does NOT bump the shard's fencing counter.
-- That counter is raised only by the next successful claim, so a zombie's copy of it is already
-- permanently stale; raising it here would burn a token with no attempt row behind it and break the
-- fk_job_checkpoint_attempt_fence invariant.
--
-- Note that the counter is referred to in words throughout this file and never by its column name.
-- The whole file is the statement text -- comments included -- and a unit test asserts that column
-- name appears nowhere in this statement, because its total absence is exactly what proves the
-- reaper is unfenced. Paraphrase it; never paste it back.
--
-- IT RECLAIMS TO 'retry_wait' RATHER THAN 'queued', which looks like a detail and is not: a
-- 'queued' row still carrying a stale lease_owner and lease_expires_at passes every CHECK on the
-- table and produces a row that actively lies about who owns it. 'retry_wait' with the lease
-- cleared is the honest shape.
--
-- WHY BOTH SCOPES EXIST. job_run_id narrows to a single run and is what an operator or a test
-- drives directly. job_definition_id is what a slice uses, and it exists because a slice drives
-- exactly ONE run per tick -- the oldest open one -- while a lane mints a SECOND run every time its
-- floor is lowered (the floor is part of the run's logical key). Scoped only to the driven run, a
-- shard stranded behind a dead lease in a sibling run could never be reaped by any tick at all,
-- because every tick reaps only the run it drives. Only NON-TERMINAL sibling runs are in scope: a
-- terminal run reached its status by having every shard settled, so it holds no lease worth
-- reclaiming. ix_job_work_item_lease_expiry is what keeps the widened scan cheap.
--
-- How this query works, clause by clause:
--
--   WITH expired AS (...)
--     A CTE ("common table expression") -- a named subquery written up front and referenced below
--     like a table. This one selects and LOCKS the candidate shards. Selecting first and updating
--     second is what lets the update be told exactly which rows it got rather than hunting for
--     them a second time under different conditions.
--
--   WHERE item.status IN ('leased', 'running')
--     AND item.lease_expires_at IS NOT NULL AND item.lease_expires_at < now()
--     The definition of an abandoned lease: the shard still claims to be held, and the rental has
--     already lapsed on the DATABASE clock. The IS NOT NULL test is spelled out because a
--     comparison against NULL is UNKNOWN rather than false, and relying on that would be an
--     accident waiting to be reversed.
--
--   AND (CAST(job_run_id AS uuid) IS NULL OR item.job_run_id = CAST(job_run_id AS uuid))
--     An OPTIONAL FILTER expressed inside the SQL rather than by building different statements in
--     Python: when the parameter is NULL the left side is true and the whole condition passes, so
--     the filter disappears. The CAST exists purely to pin the parameter's type -- PostgreSQL
--     cannot infer the type of a bare parameter that is only ever compared against NULL, and would
--     refuse the statement outright.
--
--   AND (CAST(job_definition_id AS uuid) IS NULL OR item.job_run_id IN (SELECT run.id ...))
--     The same optional-filter shape at lane scope. The inner SELECT lists every run of that lane
--     whose status is still 'queued' or 'running', and IN keeps shards belonging to any of them.
--
--   FOR UPDATE SKIP LOCKED
--     FOR UPDATE takes a write lock on each selected row and holds it until the transaction ends,
--     so nothing can claim a shard between this statement choosing it and updating it. SKIP LOCKED
--     says: if some other transaction already holds a row, do not wait -- ignore it and move on.
--     That matters because two containers may run their reapers at the same instant; without SKIP
--     LOCKED one would block on the other and a tick would be spent waiting instead of working,
--     and the skipped rows simply get reclaimed on the next pass anyway.
--     There is deliberately no LIMIT: the reaper takes everything it finds in one pass.
--
--   reclaimed AS (UPDATE ... RETURNING ...)
--     A DATA-MODIFYING CTE -- PostgreSQL allows an UPDATE to be written as a named CTE, with its
--     RETURNING rows becoming that CTE's output. That is what makes reclaiming and reporting a
--     single statement and a single round trip, rather than an update followed by a second query
--     that would see a different snapshot.
--
--   status = CASE WHEN item.attempt_count >= item.max_attempts THEN 'dead_letter' ELSE 'retry_wait' END
--     A CASE expression is SQL's if/else, evaluated per row. A shard whose budget was already spent
--     has nothing left to retry with, so reclaiming it means dead-lettering it; anything else goes
--     back to waiting.
--
--   next_attempt_at = CASE ... THEN NULL ELSE now() + make_interval(secs => backoff_seconds) END
--     The same branch again, for the wake-up time. A dead letter is never claimed again, so it gets
--     none. make_interval(secs => N) builds a span of N seconds; adding it to now() gives the
--     moment the shard becomes claimable. make_interval is used rather than the literal INTERVAL
--     syntax because a bound parameter cannot appear inside a quoted interval literal.
--
--   completed_at = CASE ... THEN COALESCE(item.completed_at, now()) ELSE item.completed_at END
--     Dead-lettering is terminal, and a terminal shard must carry a completion time. COALESCE
--     returns its first non-NULL argument, so an existing timestamp is preserved and only a missing
--     one is filled in. A shard that is merely being requeued keeps whatever it had, which is
--     normally nothing.
--
--   lease_owner = NULL, lease_expires_at = NULL
--     The dead worker's rental is torn up. These two must move TOGETHER, because
--     ck_job_work_item_complete_lease_pair requires the owner and the expiry to be both present or
--     both absent; clearing one alone aborts the statement.
--
--   SELECT id, job_run_id, status FROM reclaimed
--     The statement's actual output: one row per shard the UPDATE touched. The caller counts the
--     'dead_letter' rows per run to bump the run counters, and feeds the ids to
--     close_lost_attempts so the matching dangling attempt rows are closed too.
WITH expired AS (
    SELECT item.id
    FROM agri.job_work_item AS item
    WHERE item.status IN ('leased', 'running')
      AND item.lease_expires_at IS NOT NULL
      AND item.lease_expires_at < now()
      AND (CAST(:job_run_id AS uuid) IS NULL OR item.job_run_id = CAST(:job_run_id AS uuid))
      AND (
            CAST(:job_definition_id AS uuid) IS NULL
            OR item.job_run_id IN (
                SELECT run.id
                FROM agri.job_run AS run
                WHERE run.job_definition_id = CAST(:job_definition_id AS uuid)
                  AND run.status IN ('queued', 'running')
            )
      )
    FOR UPDATE SKIP LOCKED
), reclaimed AS (
    UPDATE agri.job_work_item AS item
    SET status = CASE
            WHEN item.attempt_count >= item.max_attempts THEN 'dead_letter'
            ELSE 'retry_wait'
        END,
        next_attempt_at = CASE
            WHEN item.attempt_count >= item.max_attempts THEN NULL
            ELSE now() + make_interval(secs => :backoff_seconds)
        END,
        completed_at = CASE
            WHEN item.attempt_count >= item.max_attempts THEN COALESCE(item.completed_at, now())
            ELSE item.completed_at
        END,
        lease_owner = NULL,
        lease_expires_at = NULL,
        last_error_class = :failure_class,
        last_error_summary = :error_summary
    FROM expired
    WHERE item.id = expired.id
    RETURNING item.id, item.job_run_id, item.status
)
SELECT id, job_run_id, status FROM reclaimed
