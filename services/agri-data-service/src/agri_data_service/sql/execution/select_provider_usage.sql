-- Purpose: every executor attempt in the requested window, one row per (attempt, metered host) it
--          touched -- the row-level feed for `agri-service ops jobs-usage-report`'s per-lane,
--          per-host and per-day sections (spec Sec 4.9.2, design Sec 2.3, plan 0W.4). Grouping,
--          rates and percentiles are computed in Python (execution/usage_report.py), not here, so
--          elapsed-time p50/p95 come from real per-attempt values rather than an average of
--          already-aggregated ones.
-- Loaded by: agri_data_service.execution.usage_report
-- Params: since (timestamptz), until (timestamptz, exclusive), lane_ids (text[], nullable -- NULL
--         means every lane), pool (text, nullable -- NULL means every pool)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and would mint a bind
-- parameter nobody supplies.
--
-- Escaping a literal colon: `execution/gap_repair_contract.py::REPAIR_LANE_SUFFIX` is a colon
-- followed by "gap-repair", and this statement's `regexp_replace` calls need that exact literal as
-- a pattern argument. Written with a bare leading colon it would ALSO look like a bind parameter to
-- SQLAlchemy's scan (a colon immediately followed by word characters, same as any other phantom
-- bind -- this note itself avoids writing it that way for the same reason) -- so every occurrence
-- below carries a leading backslash instead. `sqlalchemy.TextClause` treats a backslash-escaped
-- colon as literal text, never a bind candidate, and strips the backslash when it compiles the
-- statement, so PostgreSQL only ever sees the plain suffix -- never the backslash.
--
-- What this returns: one row per (job_attempt, host it metered), plus exactly one row for an
-- attempt that metered no host at all (host is NULL there) -- so an attempt that ran but sent
-- nothing (a config lane, a pre-spawn refusal, telemetry off) still contributes its outcome and
-- exit-class counts and its charged/suspect figures to the report instead of silently disappearing
-- from a host-keyed join. Only attempts o5a's usage fold actually stamped are considered: see the
-- `metrics ? 'spawned'` filter below.
--
-- `charged`/`suspect` here are diagnostic, not authoritative: `select_provider_month_to_date.sql`
-- (section 1) is the ONLY statement a budget decision reads (spec Sec 4.9.2, WQ-4). This section is
-- the "operator spend as log-only" view (plan 0W.4) -- excluding `running`/`deferred` attempts below
-- keeps it reading the same closed, terminal population month_to_date does, but an operator summing
-- these columns across buckets should still treat them as a diagnostic total, not a bill.
--
-- `attempt.status NOT IN ('running', 'deferred')` mirrors select_provider_month_to_date.sql's own
-- `scoped` CTE: a `deferred` attempt (a shutdown yield or a parked shard, `job_executor_service.py::
-- _interrupted_outcome`) has not reached a terminal outcome for its shard -- the spec's charging
-- table excludes it, and a row-level feed that disagreed with the rollup it is supposed to explain
-- would be worse than one that simply omits the same rows.
--
-- How this query works, clause by clause:
--
--   regexp_replace(definition.name, '\:gap-repair$', '') AS lane_id
--     Repair definitions share their owning lane's name with REPAIR_LANE_SUFFIX appended (see
--     "Escaping a literal colon" above for the pattern literal's leading backslash). Stripping it
--     here is what "repair definitions roll up to their lane" means (spec Sec 4.9.2, plan 0W.4): a
--     forward run and its repair land on the same `lane_id` grouping key downstream in Python.
--
--   attempt.metrics ? 'spawned'
--     jsonb's `?` operator asks "does this JSON object have this top-level key". Every attempt this
--     wave's executor stamps carries `spawned` (o5a); a `NULL`/`{}` metrics blob predates that and
--     is filtered out here so the report never manufactures history for an attempt o5a's fold never
--     touched -- the same predicate `select_provider_month_to_date.sql`'s `epoch` CTE uses to find
--     the metering epoch.
--
--   WHERE attempt.started_at >= since AND attempt.started_at < until
--     The requested window, half-open so two adjacent windows never double-count a boundary attempt.
--
--   AND (lane_ids IS NULL OR regexp_replace(definition.name, '\:gap-repair$', '') = ANY(lane_ids))
--     ANY(array) matches a value against every element of a bound array parameter. The NULL
--     short-circuit is what makes `--lane` optional: an unbound filter must never turn into
--     "matches nothing".
--
--   LEFT JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(...) = 'object' THEN ... ELSE '{}'::jsonb END)
--                      AS host_entry(host, entry) ON TRUE
--     jsonb_each expands a JSON object into one row per key/value pair -- here, `usage.hosts`'s
--     `{host: {provider, pool, ...counters}}` map becomes one row per host this attempt sent to.
--     jsonb_each RAISES on anything that is not a JSON object, and `usage.hosts` is JSON `null` --
--     not SQL NULL, so a bare COALESCE never catches it -- for a `not_spawned`, `reported` or
--     `suspect` attempt (`job_executor_service.py::_not_spawned_usage` writes `"hosts": null`
--     literally, and the spawned path falls back to it too when `_fold_turn_usage` itself returned
--     nothing). `jsonb_typeof` reads back JSON's own type tag ('object', 'null', ...) so the CASE
--     substitutes an empty object for anything that is not already one, exactly like COALESCE does
--     for actual SQL NULL, before jsonb_each ever sees it. LATERAL lets the right side reference
--     `windowed.metrics` from the row it is joined to (an ordinary subquery cannot); LEFT keeps the
--     attempt row even when `hosts` is empty, absent or null, with `host`/`entry` coming back NULL
--     -- see "What this returns" above. `ON TRUE` means no further join condition: the correlation
--     already lives inside the LATERAL subquery itself.
--
--   host_entry.entry ->> 'provider' / ->> 'pool'
--     Each host's own resolved identity, exactly as `foundation/observability/usage.py::
--     provider_for_host` wrote it into that host's counter object when the attempt ran. Read back
--     here rather than re-resolved, so a report can never disagree with what was actually metered.
--
--   NULLIF(host_entry.entry ->> 'weighted_calls_metered', '')::numeric
--     Every per-host and per-attempt counter is read the same defensive way: text out of the jsonb,
--     NULLIF blanks an empty string before the cast (a counter a process never touched is absent,
--     not ""), and the numeric/boolean cast lets Python sum or branch on these directly instead of
--     re-parsing JSON text for every row.
--
--   WHERE pool IS NULL OR host_entry.entry ->> 'pool' = pool
--     Same optional-filter shape as `lane_ids`, applied after the LATERAL join because the pool
--     lives on the host entry, not on the attempt row. An attempt with no hosts (`host_entry.entry`
--     is NULL) is excluded once a pool filter is set, which is correct: it metered nothing in that
--     pool.
--
--   ORDER BY windowed.started_at, windowed.definition_name, host_entry.host
--     A stable, total order so paging or re-running the report over the same window returns the
--     same sequence -- which matters for anyone diffing two reports.
WITH windowed AS (
    SELECT
        attempt.id AS attempt_id,
        attempt.job_work_item_id,
        attempt.status AS attempt_status,
        attempt.started_at,
        attempt.finished_at,
        attempt.metrics,
        regexp_replace(definition.name, '\:gap-repair$', '') AS lane_id,
        definition.name AS definition_name,
        run.id AS job_run_id,
        run.scheduled_for
    FROM agri.job_attempt AS attempt
    JOIN agri.job_work_item AS item ON item.id = attempt.job_work_item_id
    JOIN agri.job_run AS run ON run.id = item.job_run_id
    JOIN agri.job_definition AS definition ON definition.id = run.job_definition_id
    WHERE attempt.metrics ? 'spawned'
      AND attempt.status NOT IN ('running', 'deferred')
      AND attempt.started_at >= CAST(:since AS timestamptz)
      AND attempt.started_at < CAST(:until AS timestamptz)
      AND (
        CAST(:lane_ids AS text[]) IS NULL
        OR regexp_replace(definition.name, '\:gap-repair$', '') = ANY(CAST(:lane_ids AS text[]))
      )
)
SELECT
    windowed.attempt_id,
    windowed.job_run_id,
    windowed.job_work_item_id,
    windowed.lane_id,
    windowed.definition_name,
    windowed.scheduled_for,
    windowed.started_at,
    windowed.finished_at,
    windowed.attempt_status,
    (windowed.started_at AT TIME ZONE 'UTC')::date AS started_on,
    windowed.metrics ->> 'exit_class' AS exit_class,
    windowed.metrics ->> 'turn_outcome' AS turn_outcome,
    windowed.metrics ->> 'probe_status' AS probe_status,
    (windowed.metrics ->> 'spawned')::boolean AS spawned,
    (windowed.metrics ->> 'report_present')::boolean AS report_present,
    (windowed.metrics ->> 'usage_reported')::boolean AS usage_reported,
    (windowed.metrics ->> 'usage_complete')::boolean AS usage_complete,
    (windowed.metrics ->> 'unwritten_known')::boolean AS unwritten_known,
    NULLIF(windowed.metrics ->> 'elapsed_seconds', '')::numeric AS elapsed_seconds,
    NULLIF(windowed.metrics ->> 'start_lag_seconds', '')::numeric AS start_lag_seconds,
    NULLIF(windowed.metrics ->> 'rss_peak_kib', '')::numeric AS rss_peak_kib,
    NULLIF(windowed.metrics ->> 'cpu_seconds', '')::numeric AS cpu_seconds,
    NULLIF(windowed.metrics ->> 'meter_errors', '')::numeric AS meter_errors,
    NULLIF(windowed.metrics ->> 'requests', '')::numeric AS requests,
    NULLIF(windowed.metrics ->> 'weighted_calls', '')::numeric AS weighted_calls,
    NULLIF(windowed.metrics ->> 'fetch_attempts', '')::numeric AS fetch_attempts,
    NULLIF(windowed.metrics ->> 'rows_written', '')::numeric AS rows_written,
    NULLIF(windowed.metrics ->> 'bytes_written', '')::numeric AS bytes_written,
    NULLIF(windowed.metrics ->> 'publication_debt', '')::numeric AS publication_debt,
    windowed.metrics -> 'usage' ->> 'charged_basis' AS charged_basis,
    NULLIF(windowed.metrics -> 'usage' ->> 'charged', '')::numeric AS charged,
    NULLIF(windowed.metrics -> 'usage' ->> 'suspect', '')::numeric AS suspect,
    host_entry.host,
    host_entry.entry ->> 'provider' AS provider,
    host_entry.entry ->> 'pool' AS pool,
    NULLIF(host_entry.entry ->> 'http_requests', '')::numeric AS http_requests,
    NULLIF(host_entry.entry ->> 'http_2xx', '')::numeric AS http_2xx,
    NULLIF(host_entry.entry ->> 'http_3xx', '')::numeric AS http_3xx,
    NULLIF(host_entry.entry ->> 'http_4xx', '')::numeric AS http_4xx,
    NULLIF(host_entry.entry ->> 'http_429', '')::numeric AS http_429,
    NULLIF(host_entry.entry ->> 'http_5xx', '')::numeric AS http_5xx,
    NULLIF(host_entry.entry ->> 'transport_failures', '')::numeric AS transport_failures,
    NULLIF(host_entry.entry ->> 'bytes_in', '')::numeric AS bytes_in,
    NULLIF(host_entry.entry ->> 'backoff_seconds', '')::numeric AS backoff_seconds,
    NULLIF(host_entry.entry ->> 'weighted_calls_metered', '')::numeric AS weighted_calls_metered,
    host_entry.entry ->> 'last_send_outcome' AS last_send_outcome
FROM windowed
LEFT JOIN LATERAL jsonb_each(
    CASE
        WHEN jsonb_typeof(windowed.metrics -> 'usage' -> 'hosts') = 'object'
        THEN windowed.metrics -> 'usage' -> 'hosts'
        ELSE '{}'::jsonb
    END
) AS host_entry(host, entry) ON TRUE
WHERE CAST(:pool AS text) IS NULL OR host_entry.entry ->> 'pool' = CAST(:pool AS text)
ORDER BY windowed.started_at, windowed.definition_name, host_entry.host
