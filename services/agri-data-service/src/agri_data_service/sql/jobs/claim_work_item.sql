-- claim_work_item
-- Purpose: hand exactly one claimable shard of a run to this worker, under a brand-new fencing
--          token, and return everything the worker needs to run it.
-- Loaded by: agri_data_service.jobs.lease
-- Params: job_run_id (uuid) -- the run whose shards are in scope for this tick.
--         worker_id (text) -- the identity stamped into lease_owner. Together with the fencing
--         token, this string is the whole of the worker's authority over the shard.
--         lease_seconds (double precision) -- how long the claim is good for before any other
--         worker is entitled to take the shard away.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, so a colon-led word in
-- a comment mints a bind parameter nobody supplies and execution fails.
--
-- What this returns: one row describing the shard this worker now owns, or no rows at all. No
-- rows is not an error -- it is the ordinary "nothing to do right now" answer, and the caller
-- treats it as the end of the tick.
--
-- BACKGROUND, because the shape below only makes sense with it. A "run" is one backfill campaign
-- for one lane; a "work item" (a shard) is one window of that campaign, e.g. one day of one
-- source. Several one-shot cron containers may be awake at the same moment, all pointed at the
-- same run, and none of them can see the others. Two guarantees have to come out of this one
-- statement: no two workers may hold the same shard, and a worker whose container is killed must
-- not take its shard to the grave with it.
--
-- The lease is how the second guarantee is met: a claim is a time-limited rental, not a
-- transfer. The fencing token is how the first is met past the moment the rental lapses. The
-- token is a counter on the item row that goes up by one on every claim and is NEVER reset. Every
-- later write this worker makes repeats the token it was given, so if a second worker has since
-- claimed the shard -- pushing the counter past this worker's copy -- the slow worker's writes
-- match zero rows and it learns it has been superseded. Without that, a worker that stalled long
-- enough for its lease to expire would wake up and cheerfully overwrite the work of whoever
-- replaced it.
--
-- WHY THIS IS ONE STATEMENT AND NOT SEVERAL. Every constraint on these tables is NOT DEFERRABLE,
-- which means the database checks it the instant a statement runs rather than waiting for the
-- transaction to commit. So status, lease_owner, lease_expires_at and fencing_token must all
-- move in the same statement (that is what ck_job_work_item_complete_lease_pair and
-- ck_job_work_item_active_item_has_fenced_lease require of any row that holds a lease), and
-- attempt_count moves with them so that the attempt row the caller opens next can reuse it as
-- its attempt_number.
--
-- How this query works, clause by clause:
--
--   WITH candidate AS (...)
--     A CTE ("common table expression") -- a named subquery written up front and then referenced
--     below like a table. Its only job is to pick, and lock, the single row this worker will
--     take. Splitting selection from the update is what makes the choice orderly: the UPDATE
--     underneath does not go hunting, it is simply told which row it got.
--
--   WHERE item.job_run_id = job_run_id
--     Scope. Only shards belonging to the run this tick was told to drive are eligible.
--
--   AND item.attempt_count < item.max_attempts
--     Not optional, and not merely an optimisation. attempt_count is the number of tries this
--     shard has been given; max_attempts is its budget. Without this line the statement could
--     select a shard whose budget is already spent, and the attempt_count + 1 below would
--     violate ck_job_work_item_attempt_count_within_limit -- which aborts the whole statement.
--     The worker would receive a database error where the correct answer was "no work".
--
--   AND item.available_at <= now()
--     Shards can be scheduled into the future (a window whose upstream data does not exist yet).
--     now() is the current transaction's timestamp, taken from the DATABASE clock, never from the
--     worker's, because two workers with skewed clocks must never disagree about whether a lease
--     has expired.
--
--   AND ( (...fresh arm...) OR (...expired-lease arm...) )
--     Two independent ways a shard becomes claimable, and they are the whole eligibility rule.
--
--   item.status IN ('queued', 'retry_wait', 'deferred')
--     AND (item.next_attempt_at IS NULL OR item.next_attempt_at <= now())
--     The fresh arm: nobody holds this shard. 'queued' has never run; 'retry_wait' failed and is
--     serving a backoff; 'deferred' is parked waiting on upstream. The next_attempt_at test lets
--     the last two through only once their wait is over. The IS NULL half is spelled out because
--     a comparison against NULL is neither true nor false in SQL -- it is UNKNOWN, and a row
--     whose next_attempt_at is unset would silently fail a bare next_attempt_at <= now() test and
--     become permanently unclaimable.
--
--   item.status IN ('leased', 'running')
--     AND item.lease_expires_at IS NOT NULL AND item.lease_expires_at <= now()
--     The expired-lease arm, and it is what makes a killed container recoverable with no
--     filesystem state whatsoever. A shard marked 'leased' or 'running' whose lease has already
--     lapsed belonged to a worker that never came back -- redeployed, evicted, crashed. The next
--     tick simply claims that abandoned lease straight out of the ledger. Nothing has to be
--     replayed from disk, because nothing was ever kept on disk.
--
--   ORDER BY item.priority DESC, item.available_at, item.id
--     Which shard, when several qualify: highest priority first, then oldest window first, with
--     the id as a final tie-break so the order is total and two workers never derive different
--     "first" rows from the same set.
--
--   FOR UPDATE SKIP LOCKED
--     The heart of the concurrency argument, and two separate things.
--     FOR UPDATE takes a write lock on each row this SELECT returns and holds it until the
--     transaction ends. Nobody else can update that row in the meantime, so between choosing the
--     row and updating it nothing can change underneath.
--     SKIP LOCKED says: if a row is already locked by somebody else's transaction, do not wait
--     for it -- pretend it is not there and move on to the next candidate. Without SKIP LOCKED,
--     ten workers starting at once would all pick the same top row, nine would block on the
--     first, and the queue would run one shard at a time. With it, each of the ten walks down the
--     ordered list and takes the first row nobody else has, so ten workers claim ten different
--     shards in parallel with no coordination between them at all.
--
--   LIMIT 1
--     One shard per claim. The worker drives it to a landing and comes back for another.
--
--   UPDATE agri.job_work_item AS item ... FROM candidate WHERE item.id = candidate.id
--     Applies the claim to precisely the row the CTE locked. Because that row is already locked
--     by this transaction, this update cannot fail on a race and cannot pick a different row.
--
--   SET fencing_token = item.fencing_token + 1
--     Mints the new token. Strictly monotonic, never reset, never reused -- which is exactly what
--     makes it usable as proof of ownership by every later statement.
--
--   SET attempt_count = item.attempt_count + 1
--     Spends one try from the shard's budget. The caller reads this back as the attempt_number
--     of the attempt row it is about to open, so the two can never drift apart.
--
--   SET lease_expires_at = now() + make_interval(secs => lease_seconds)
--     Interval arithmetic. make_interval(secs => N) builds a span of N seconds, which added to
--     now() gives the moment the rental lapses. make_interval is used rather than the literal
--     INTERVAL syntax because a bound parameter cannot be placed inside a quoted interval
--     literal.
--
--   SET started_at = COALESCE(item.started_at, now())
--     COALESCE returns its first non-NULL argument. So this stamps a start time only on the very
--     first claim and leaves it alone on every re-claim -- started_at means "when work on this
--     shard first began", not "when it was last picked up".
--
--   SET next_attempt_at = NULL
--     The shard is no longer waiting for anything; it is being worked on.
--
--   RETURNING item.id, item.job_run_id, ...
--     RETURNING hands back columns from the rows the UPDATE actually changed, in the same round
--     trip. That matters twice over: it is the only way to learn the post-increment fencing_token
--     and attempt_count this worker was given, and if the UPDATE changed nothing it returns
--     nothing, which is precisely the "no claimable work" signal the caller checks for.
WITH candidate AS (
    SELECT item.id
    FROM agri.job_work_item AS item
    WHERE item.job_run_id = :job_run_id
      AND item.attempt_count < item.max_attempts
      AND item.available_at <= now()
      AND (
            (
                item.status IN ('queued', 'retry_wait', 'deferred')
                AND (item.next_attempt_at IS NULL OR item.next_attempt_at <= now())
            )
            OR (
                item.status IN ('leased', 'running')
                AND item.lease_expires_at IS NOT NULL
                AND item.lease_expires_at <= now()
            )
      )
    ORDER BY item.priority DESC, item.available_at, item.id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE agri.job_work_item AS item
SET status = 'leased',
    fencing_token = item.fencing_token + 1,
    attempt_count = item.attempt_count + 1,
    lease_owner = :worker_id,
    lease_expires_at = now() + make_interval(secs => :lease_seconds),
    heartbeat_at = now(),
    next_attempt_at = NULL,
    started_at = COALESCE(item.started_at, now())
FROM candidate
WHERE item.id = candidate.id
RETURNING item.id,
          item.job_run_id,
          item.shard_key,
          item.kind,
          item.payload,
          item.fencing_token,
          item.attempt_count,
          item.max_attempts,
          item.checkpoint_sequence,
          item.progress_fraction,
          item.lease_expires_at
