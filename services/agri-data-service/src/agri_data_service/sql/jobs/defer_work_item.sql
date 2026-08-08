-- defer_work_item
-- Purpose: park a shard until a stated moment WITHOUT spending its retry budget -- and bound that
--          generosity, so a shard that can only ever park eventually stops being protected.
-- Loaded by: agri_data_service.jobs.lease
-- Params: work_item_id (uuid) -- the shard being parked.
--         fencing_token (int) -- the token this worker was given at claim time.
--         lease_owner (text) -- this worker's id, as written into lease_owner by the claim.
--         resume_at (timestamptz, nullable) -- when the shard becomes claimable again; NULL means
--         immediately, which is what a budget yield or a container shutdown passes.
--         max_consecutive_parks (int) -- how many parks in a row may be forgiven before the
--         protection stops. Supplied by the caller from MAX_CONSECUTIVE_PARKS in jobs/lease.py.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row when the shard was parked, NO rows when the fence had already moved.
--
-- WHY RAISING max_attempts IS THE LOAD-BEARING PART. The claim that led to this park already spent
-- one try from the shard's budget by incrementing attempt_count. But a deferral means upstream had
-- nothing to give -- it is not a failure and the shard did not misbehave -- so charging the failure
-- budget for it would dead-letter a WEEKLY source polled hourly after about five hours of
-- perfectly correct waiting. Raising the ceiling by one gives that try back. It is legal
-- (ck_job_work_item_positive_work_item_max_attempts and
-- ck_job_work_item_attempt_count_within_limit are both still satisfied) and it keeps attempt
-- numbers densely unique, which decrementing attempt_count instead would not. See jobs/AGENTS.md
-- "A deferral must not spend the retry budget".
--
-- THE CASE IS WHAT BOUNDS THAT PROTECTION, and it is the whole of the park budget -- there is no
-- park-counter column anywhere and this statement adds none. Left unbounded, a window that parks on
-- every single tick would climb max_attempts for ever while a status report cheerfully described it
-- as a healthy 'deferred'. Past the ceiling the protection simply stops: the claim keeps charging
-- attempt_count, the retry budget starts closing, and the shard eventually dead-letters into a
-- completeness report that says it is missing, rather than sitting parked and silent indefinitely.
--
-- How this query works, clause by clause:
--
--   SET status = 'deferred'
--     Parked, waiting on something outside this service. Distinct from 'retry_wait', which means
--     "failed and serving a backoff", so an operator can tell the two apart at a glance.
--
--   SET next_attempt_at = COALESCE(CAST(resume_at AS timestamptz), now())
--     When the shard wakes up. COALESCE returns its first non-NULL argument, so a caller that
--     passes no resume time gets now() -- immediately claimable, which is exactly what a budget
--     yield wants, since leaving the lease to rot for the rest of its term would keep the next tick
--     out for no reason. The CAST exists purely to pin the parameter's type: a bare bound parameter
--     inside COALESCE gives PostgreSQL nothing to infer from.
--
--   max_attempts = item.max_attempts + CASE WHEN (...) < max_consecutive_parks THEN 1 ELSE 0 END
--     Add one to the ceiling, or add nothing, depending on the count below. A CASE expression is
--     SQL's if/else: it evaluates the condition per row and yields the matching branch's value.
--
--   SELECT count(*) FROM agri.job_attempt AS parked WHERE parked.job_work_item_id = item.id
--     A CORRELATED subquery -- it references item.id from the row being updated, so it is
--     evaluated against that specific shard. It counts that shard's own parked attempts.
--
--   AND parked.status = 'deferred'
--     Only parks count. Failures and successes are not parks and must not close the park budget.
--
--   AND parked.fencing_token > COALESCE((SELECT max(progress.fencing_token) FROM ...), 0)
--     THIS IS WHAT MAKES THE COUNT "CONSECUTIVE" RATHER THAN "TOTAL". The inner subquery finds the
--     fencing token of the shard's newest checkpoint -- i.e. the claim under which it last made
--     real progress. Counting only parked attempts NEWER than that means the count is "parks since
--     the last progress", which resets to zero the moment a window walks a chunk. So the normal
--     shape of a long multi-tick window -- walk a chunk, park for the clock, repeat -- starts from
--     zero on every tick and is never penalised. COALESCE supplies 0 when the shard has never
--     checkpointed at all, since max() over no rows returns NULL and every comparison against NULL
--     is UNKNOWN, which would make the whole condition fail and quietly disable the protection.
--
--   the subquery reads the PRE-UPDATE row
--     In PostgreSQL an UPDATE's expressions see the row as it was before this statement. But
--     close_attempt_deferred has ALREADY run in this same transaction, so the attempt being parked
--     right now is itself already 'deferred' and is therefore included in its own count. That is
--     intended: the ceiling stops rising on the park that reaches the limit, not one park later.
--
--   SET lease_owner = NULL, lease_expires_at = NULL
--     The rental ends. These two must move TOGETHER, because
--     ck_job_work_item_complete_lease_pair requires the owner and the expiry to be both present or
--     both absent; clearing one alone aborts the statement.
--
--   WHERE item.id = work_item_id AND item.fencing_token = fencing_token
--     AND item.lease_owner = lease_owner AND item.status IN ('leased', 'running')
--     THE FENCE -- the three columns that together are this worker's whole authority over the
--     shard, plus the guard that only a shard actually in hand may be parked. The fencing token is
--     a counter bumped by every claim and never reset, so a superseded worker holds a stale copy,
--     matches nothing, and cannot park a shard another worker is actively driving.
--
--   RETURNING item.id
--     RETURNING hands back a row per row actually updated, in the same round trip. The caller reads
--     presence or absence of that row as "parked" or "fence lost".
UPDATE agri.job_work_item AS item
SET status = 'deferred',
    next_attempt_at = COALESCE(CAST(:resume_at AS timestamptz), now()),
    max_attempts = item.max_attempts + CASE
        WHEN (
            SELECT count(*)
            FROM agri.job_attempt AS parked
            WHERE parked.job_work_item_id = item.id
              AND parked.status = 'deferred'
              AND parked.fencing_token > COALESCE(
                  (
                      SELECT max(progress.fencing_token)
                      FROM agri.job_checkpoint AS progress
                      WHERE progress.job_work_item_id = item.id
                  ),
                  0
              )
        ) < :max_consecutive_parks THEN 1
        ELSE 0
    END,
    lease_owner = NULL,
    lease_expires_at = NULL,
    heartbeat_at = now()
WHERE item.id = :work_item_id
  AND item.fencing_token = :fencing_token
  AND item.lease_owner = :lease_owner
  AND item.status IN ('leased', 'running')
RETURNING item.id
