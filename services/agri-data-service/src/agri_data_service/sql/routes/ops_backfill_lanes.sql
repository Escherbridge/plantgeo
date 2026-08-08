-- Purpose: one row per backfill lane run for the /ops/backfill dashboard -- work-item
--          state counts, completion, the throughput input, the honest "last recorded
--          activity" timestamp, and the count of leases that have already expired.
-- Loaded by: agri_data_service.routes.ops
-- Params: throughput_window_hours (int) -- how many trailing hours the succeeded and
--         dead-lettered counters look back over. The route divides those counters by
--         this same number to get a per-hour rate, so the two must agree.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap"
-- in sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and would
-- mint a bind parameter nobody supplies.
--
-- What this returns: one row per (job definition, job run). A "run" is one logical
-- backfill campaign for one lane; its work items are the individual windows to walk.
--
-- How this query works, clause by clause:
--
--   WITH item_rollup AS (...)
--     A CTE ("common table expression") -- a named subquery written up front and then
--     referenced below like a table. This one reads agri.job_work_item once and
--     collapses it to one row per run.
--
--   GROUP BY item.job_run_id
--     Collapses many work-item rows into one row per run. Every column selected
--     alongside it must therefore be an aggregate (count, max, min, avg) -- there is no
--     single "the status" for a group of 1,882 windows, only counts of each status.
--
--   count(*) FILTER (WHERE item.status = 'queued')
--     FILTER restricts one aggregate to the rows matching its condition, while other
--     aggregates in the same SELECT still see every row. It is how all eight state
--     counters come out of a single pass over the table instead of eight separate
--     queries. Rows that fail the FILTER are not counted; they are not dropped.
--
--   count(*) FILTER (WHERE item.status IN ('leased', 'running')
--                      AND item.lease_expires_at < now())
--     Stalled candidates. A worker holds a window by writing a lease with an expiry;
--     while the lease is live nobody else may claim that window. A lease already in the
--     past with the item still 'leased'/'running' means the worker died without
--     releasing it, and the window is stuck until a reaper tick reclaims it.
--
--   item.completed_at >= now() - make_interval(hours => CAST(...))
--     Interval arithmetic. now() is the current transaction timestamp;
--     make_interval(hours => N) builds a span of N hours; subtracting the span from the
--     timestamp gives the start of the trailing window. make_interval is used rather
--     than the literal syntax because a bound parameter cannot appear inside a quoted
--     interval literal. The CAST pins the parameter to integer so the database never has
--     to guess its type. completed_at is written on both terminal paths (succeeded and
--     dead-lettered), which is what makes it a sound throughput axis.
--
--   min(item.next_attempt_at) FILTER (WHERE item.status IN ('retry_wait', 'deferred'))
--     The earliest moment a parked window becomes claimable again -- i.e. when the lane
--     next has something to do, if nothing else is queued.
--
--   min(item.shard_key) FILTER (WHERE item.status NOT IN ('succeeded', 'cancelled'))
--     The lane's frontier. Shard keys sort lexicographically by date, so the smallest
--     unsettled one is the oldest window still outstanding.
--
--   avg(item.progress_fraction) FILTER (...)
--     Mean partial progress across the windows still outstanding. A window that is half
--     walked reports 0.5 here; a fresh one reports 0.
--
--   WITH attempt_rollup AS (...) / checkpoint_rollup AS (...)
--     Two more CTEs at a different grain. An "attempt" is one worker's fenced try at one
--     window; a "checkpoint" is a resumable cursor that worker saved mid-window. They
--     cannot be folded into item_rollup: joining them there would multiply each work-item
--     row by its number of attempts and inflate every count above. Aggregating them
--     separately and joining one-row-per-run to one-row-per-run keeps the counts exact.
--
--   JOIN agri.job_work_item AS item ON item.id = attempt.job_work_item_id
--     Attempts and checkpoints only carry a work-item id, so they are joined back to the
--     work item purely to learn which run they belong to.
--
--   count(DISTINCT attempt.worker_id) FILTER (WHERE attempt.started_at >= now() - interval '2 hours')
--     How many distinct worker processes have opened an attempt recently. DISTINCT
--     counts each worker id once no matter how many attempts it opened.
--
--   LEFT JOIN item_rollup ON item_rollup.job_run_id = run.id
--     LEFT keeps the run row even when the CTE has nothing for it -- a run that was
--     planned but never fanned out has no work items, and an ordinary JOIN would hide it
--     entirely. The missing columns arrive as NULL, which the SELECT turns into 0 with
--     COALESCE where a zero is the honest answer.
--
--   GREATEST(a, b, c, ...) AS last_recorded_activity_at
--     GREATEST returns the largest of its arguments and, unlike most functions, ignores
--     NULL arguments -- it is NULL only when every argument is NULL. So this is "the most
--     recent moment the ledger recorded anything at all for this run", across item
--     completions, item starts, heartbeats, attempt opens, attempt closes and
--     checkpoints. It is deliberately NOT called "last cron run": a tick that claims
--     nothing writes nothing durable, so an idle healthy lane and a lane whose cron has
--     not fired in days are indistinguishable from here. Note also that updated_at is
--     absent on purpose -- nothing in the runtime moves it, so it is frozen at insert.
--
--   ORDER BY definition.name, run.scheduled_for DESC, run.logical_run_key
--     A stable, total order so the dashboard's rows do not shuffle between refreshes.
--     Newest run first within a lane, because lowering a lane's floor mints a second run
--     and the newer one is usually the one being watched.
WITH item_rollup AS (
    SELECT
        item.job_run_id,
        count(*) AS total_items,
        count(*) FILTER (WHERE item.status = 'queued') AS queued_items,
        count(*) FILTER (WHERE item.status = 'leased') AS leased_items,
        count(*) FILTER (WHERE item.status = 'running') AS running_items,
        count(*) FILTER (WHERE item.status = 'retry_wait') AS retry_wait_items,
        count(*) FILTER (WHERE item.status = 'deferred') AS deferred_items,
        count(*) FILTER (WHERE item.status = 'succeeded') AS succeeded_items,
        count(*) FILTER (WHERE item.status = 'dead_letter') AS dead_letter_items,
        count(*) FILTER (WHERE item.status = 'cancelled') AS cancelled_items,
        count(*) FILTER (WHERE item.status NOT IN ('succeeded', 'cancelled')) AS outstanding_items,
        count(*) FILTER (
            WHERE item.status IN ('leased', 'running')
              AND item.lease_expires_at < now()
        ) AS stalled_lease_items,
        count(*) FILTER (
            WHERE item.status = 'succeeded'
              AND item.completed_at >= now() - make_interval(hours => CAST(:throughput_window_hours AS integer))
        ) AS succeeded_in_window,
        count(*) FILTER (
            WHERE item.status = 'dead_letter'
              AND item.completed_at >= now() - make_interval(hours => CAST(:throughput_window_hours AS integer))
        ) AS dead_lettered_in_window,
        max(item.completed_at) AS last_item_completed_at,
        max(item.started_at) AS last_item_started_at,
        max(item.heartbeat_at) AS last_item_heartbeat_at,
        min(item.next_attempt_at) FILTER (
            WHERE item.status IN ('retry_wait', 'deferred')
        ) AS next_attempt_at,
        min(item.shard_key) FILTER (
            WHERE item.status NOT IN ('succeeded', 'cancelled')
        ) AS oldest_outstanding_shard_key,
        avg(item.progress_fraction) FILTER (
            WHERE item.status NOT IN ('succeeded', 'cancelled')
        ) AS outstanding_progress_fraction
    FROM agri.job_work_item AS item
    GROUP BY item.job_run_id
),
attempt_rollup AS (
    SELECT
        item.job_run_id,
        max(attempt.started_at) AS last_attempt_started_at,
        max(attempt.finished_at) AS last_attempt_finished_at,
        max(attempt.heartbeat_at) AS last_attempt_heartbeat_at,
        count(*) FILTER (WHERE attempt.status = 'running') AS open_attempts,
        count(DISTINCT attempt.worker_id) FILTER (
            WHERE attempt.started_at >= now() - interval '2 hours'
        ) AS recent_worker_count
    FROM agri.job_attempt AS attempt
    JOIN agri.job_work_item AS item ON item.id = attempt.job_work_item_id
    GROUP BY item.job_run_id
),
checkpoint_rollup AS (
    SELECT
        item.job_run_id,
        max(checkpoint.created_at) AS last_checkpoint_at
    FROM agri.job_checkpoint AS checkpoint
    JOIN agri.job_work_item AS item ON item.id = checkpoint.job_work_item_id
    GROUP BY item.job_run_id
)
SELECT
    definition.name AS definition_name,
    definition.version AS definition_version,
    definition.enabled AS definition_enabled,
    definition.schedule AS definition_schedule,
    run.id AS job_run_id,
    run.logical_run_key,
    run.status AS run_status,
    run.scheduled_for,
    run.started_at AS run_started_at,
    run.completed_at AS run_completed_at,
    run.total_work_items,
    run.succeeded_work_items,
    run.failed_work_items,
    COALESCE(item_rollup.total_items, 0) AS total_items,
    COALESCE(item_rollup.queued_items, 0) AS queued_items,
    COALESCE(item_rollup.leased_items, 0) AS leased_items,
    COALESCE(item_rollup.running_items, 0) AS running_items,
    COALESCE(item_rollup.retry_wait_items, 0) AS retry_wait_items,
    COALESCE(item_rollup.deferred_items, 0) AS deferred_items,
    COALESCE(item_rollup.succeeded_items, 0) AS succeeded_items,
    COALESCE(item_rollup.dead_letter_items, 0) AS dead_letter_items,
    COALESCE(item_rollup.cancelled_items, 0) AS cancelled_items,
    COALESCE(item_rollup.outstanding_items, 0) AS outstanding_items,
    COALESCE(item_rollup.stalled_lease_items, 0) AS stalled_lease_items,
    COALESCE(item_rollup.succeeded_in_window, 0) AS succeeded_in_window,
    COALESCE(item_rollup.dead_lettered_in_window, 0) AS dead_lettered_in_window,
    COALESCE(attempt_rollup.open_attempts, 0) AS open_attempts,
    COALESCE(attempt_rollup.recent_worker_count, 0) AS recent_worker_count,
    item_rollup.oldest_outstanding_shard_key,
    item_rollup.outstanding_progress_fraction,
    item_rollup.next_attempt_at,
    item_rollup.last_item_completed_at,
    attempt_rollup.last_attempt_started_at,
    checkpoint_rollup.last_checkpoint_at,
    GREATEST(
        item_rollup.last_item_completed_at,
        item_rollup.last_item_started_at,
        item_rollup.last_item_heartbeat_at,
        attempt_rollup.last_attempt_started_at,
        attempt_rollup.last_attempt_finished_at,
        attempt_rollup.last_attempt_heartbeat_at,
        checkpoint_rollup.last_checkpoint_at
    ) AS last_recorded_activity_at
FROM agri.job_run AS run
JOIN agri.job_definition AS definition ON definition.id = run.job_definition_id
LEFT JOIN item_rollup ON item_rollup.job_run_id = run.id
LEFT JOIN attempt_rollup ON attempt_rollup.job_run_id = run.id
LEFT JOIN checkpoint_rollup ON checkpoint_rollup.job_run_id = run.id
ORDER BY definition.name, run.scheduled_for DESC, run.logical_run_key
