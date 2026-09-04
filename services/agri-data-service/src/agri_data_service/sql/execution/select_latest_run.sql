-- Purpose: select the bounded lane-wide run candidate a versioned executor must settle or continue.
-- Loaded by: agri_data_service.execution.job_executor_service
-- Params: name/current_version (text), supersession_fingerprint_prefix (text),
--         failure_streak_limit (integer)
--
-- The three candidate branches are deliberately index-bounded instead of ranking the complete run
-- lifetime on every scheduler poll:
--
--   prior_version_open / current_version_open
--     Each reads at most one queued/running candidate in scheduled order. The existing
--     ix_job_run_status_scheduled index supplies the small open-run set; the definition join limits
--     it to this stable lane name and separates prior versions from the current version. A prior open
--     run has selection rank 0, so current-version work can never hide unfinished upgrade work.
--
--   latest_terminal_per_definition / latest_terminal
--     ix_job_run_definition_created supplies the newest-created terminal candidate for each stored
--     definition version with one backward index probe. Executor buckets are opened monotonically and
--     never while that lane already has an open run, so that candidate is the version's latest cadence
--     checkpoint. The outer LIMIT chooses the greatest schedule across the few definition versions,
--     never across the lane's complete run history.
--
--   candidate_runs / selected_run
--     At most three rows survive: one prior open, one current open and one terminal checkpoint. The
--     fixed selection rank chooses in exactly that order and LIMIT returns a single scheduler state.
--
-- The final EXISTS predicates inspect work items only for that one run. work_claimable mirrors the
-- worker's claim/reaper contract. terminal_items_need_rollup identifies the crash boundary where the
-- child reached succeeded/dead_letter/cancelled but the process died before refreshing its parent run;
-- the planner must drive the exact definition once more so run_job_slice repairs the authoritative rollup.
--
-- The last two columns exist only for a checkpoint that settled 'failed' or 'partial'; both are
-- short-circuited to a constant for every other status, so the common healthy-lane poll touches
-- neither job_incident nor a second job_run range.
--
--   superseded_by_operator / the operator's release
--     One agri.job_incident row whose fingerprint is the executor's supersession prefix followed by
--     this run's id -- a probe of uq_job_incident_fingerprint, never a scan. The fingerprint alone is
--     the marker: that namespace is written only by `ops jobs-supersede-run`, always as a resolved
--     incident, so a status predicate here would let an operator "reopening" the incident freeze the
--     lane with no verb able to release it. The marker is an incident rather than a job_event because
--     job_event partitions are dropped after 30 days while the evidence must outlive the run it
--     explains (see execution/AGENTS.md, "Failed checkpoints are superseded by the clock or by an
--     operator").
--
--   consecutive_failures / the clock's breaker
--     How many of this definition's most recent terminal runs, newest first, settled without success
--     before one that did not -- the failure streak the planner's breaker reads. Bounded by
--     failure_streak_limit so it is one backward probe of ix_job_run_definition_created, never a walk
--     of the run history: the inner query takes the newest N terminal runs, the window function marks
--     each row whose predecessors (in that newest-first order) all failed too, and the count of marked
--     rows is the unbroken streak, capped at N.
WITH prior_version_open AS (
    SELECT run.id,
           run.job_definition_id,
           definition.version AS definition_version,
           definition.enabled AS definition_enabled,
           run.scheduled_for,
           run.status,
           run.created_at
    FROM agri.job_run AS run
    JOIN agri.job_definition AS definition ON definition.id = run.job_definition_id
    WHERE definition.name = :name
      AND definition.version <> CAST(:current_version AS text)
      AND run.status IN ('queued', 'running')
    ORDER BY run.scheduled_for, run.created_at, run.id
    LIMIT 1
),
current_version_open AS (
    SELECT run.id,
           run.job_definition_id,
           definition.version AS definition_version,
           definition.enabled AS definition_enabled,
           run.scheduled_for,
           run.status,
           run.created_at
    FROM agri.job_run AS run
    JOIN agri.job_definition AS definition ON definition.id = run.job_definition_id
    WHERE definition.name = :name
      AND definition.version = CAST(:current_version AS text)
      AND run.status IN ('queued', 'running')
    ORDER BY run.scheduled_for, run.created_at, run.id
    LIMIT 1
),
latest_terminal_per_definition AS (
    SELECT terminal.id,
           terminal.job_definition_id,
           definition.version AS definition_version,
           definition.enabled AS definition_enabled,
           terminal.scheduled_for,
           terminal.status,
           terminal.created_at
    FROM agri.job_definition AS definition
    JOIN LATERAL (
        SELECT run.id,
               run.job_definition_id,
               run.scheduled_for,
               run.status,
               run.created_at
        FROM agri.job_run AS run
        WHERE run.job_definition_id = definition.id
          AND run.status NOT IN ('queued', 'running')
        ORDER BY run.created_at DESC, run.id DESC
        LIMIT 1
    ) AS terminal ON true
    WHERE definition.name = :name
),
latest_terminal AS (
    SELECT terminal.*
    FROM latest_terminal_per_definition AS terminal
    ORDER BY terminal.scheduled_for DESC, terminal.created_at DESC, terminal.id DESC
    LIMIT 1
),
candidate_runs AS (
    SELECT 0 AS selection_rank, run.* FROM prior_version_open AS run
    UNION ALL
    SELECT 1 AS selection_rank, run.* FROM current_version_open AS run
    UNION ALL
    SELECT 2 AS selection_rank, run.* FROM latest_terminal AS run
),
selected_run AS (
    SELECT candidate.*
    FROM candidate_runs AS candidate
    ORDER BY candidate.selection_rank
    LIMIT 1
)
SELECT run.id,
       run.job_definition_id,
       run.definition_version,
       run.definition_enabled,
       run.scheduled_for,
       run.status,
       EXISTS (
           SELECT 1
           FROM agri.job_work_item AS item
           WHERE item.job_run_id = run.id
       ) AS has_work_items,
       EXISTS (
           SELECT 1
           FROM agri.job_work_item AS item
           WHERE item.job_run_id = run.id
             AND item.available_at <= now()
             AND (
                   (
                       item.status IN ('queued', 'retry_wait', 'deferred')
                       AND item.attempt_count < item.max_attempts
                       AND (item.next_attempt_at IS NULL OR item.next_attempt_at <= now())
                   )
                   OR (
                       item.status IN ('leased', 'running')
                       AND item.lease_expires_at IS NOT NULL
                       AND item.lease_expires_at <= now()
                   )
             )
       ) AS work_claimable,
       run.status IN ('queued', 'running')
       AND EXISTS (
           SELECT 1
           FROM agri.job_work_item AS item
           WHERE item.job_run_id = run.id
       )
       AND NOT EXISTS (
           SELECT 1
           FROM agri.job_work_item AS item
           WHERE item.job_run_id = run.id
             AND item.status NOT IN ('succeeded', 'dead_letter', 'cancelled')
       ) AS terminal_items_need_rollup,
       run.status IN ('failed', 'partial')
       AND EXISTS (
           SELECT 1
           FROM agri.job_incident AS incident
           WHERE incident.fingerprint = CAST(:supersession_fingerprint_prefix AS text) || CAST(run.id AS text)
       ) AS superseded_by_operator,
       CASE
           WHEN run.status IN ('failed', 'partial') THEN (
               SELECT count(*)
               FROM (
                   SELECT bool_and(recent.status IN ('failed', 'partial')) OVER (
                              ORDER BY recent.created_at DESC, recent.id DESC
                              ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                          ) AS unbroken
                   FROM (
                       SELECT newest.status, newest.created_at, newest.id
                       FROM agri.job_run AS newest
                       WHERE newest.job_definition_id = run.job_definition_id
                         AND newest.status NOT IN ('queued', 'running')
                       ORDER BY newest.created_at DESC, newest.id DESC
                       LIMIT CAST(:failure_streak_limit AS integer)
                   ) AS recent
               ) AS streak
               WHERE streak.unbroken
           )
           ELSE 0
       END AS consecutive_failures
FROM selected_run AS run
