-- upsert_matview_refresh_state
-- Purpose: record what happened to one matview's refresh attempt -- design doc section 5.3 step 7,
--          "Write outcome/duration/rowcount/watermark back to agri.matview_refresh_state". Called
--          once per view per tick this lane (or jobs.strategy_mv_refresh, for observability parity)
--          actually attempted -- never for a view the watermark gate skipped, since nothing changed
--          for it to record.
-- Loaded by: agri_data_service.jobs.matview_refresh, agri_data_service.jobs.strategy_mv_refresh
-- Params: view_name (text) -- the schema-qualified view, e.g. "geo.mv_signal_cell_daily".
--         source_watermark (text) -- the canonical-JSON rendering of this attempt's watermark read,
--           always supplied (the caller evaluates the watermark before deciding to attempt a
--           refresh at all, whether the attempt then succeeds or fails).
--         refreshed_at (timestamptz, nullable) -- now() on a successful or self-healed refresh;
--           NULL on a failed attempt, meaning "leave the last successful timestamp as it was" --
--           see the ON CONFLICT clause below for why NULL means keep, not clear.
--         duration_ms (integer, nullable) -- how long the attempt (success or failure) took.
--         row_count (bigint, nullable) -- the view's row count read back after a successful
--           refresh; NULL on a failure, since nothing new was read.
--         outcome (text) -- this attempt's MatviewRefreshOutcome literal, e.g.
--           "refreshed_concurrently" or "failed".
--
--         last_attempt_at and consecutive_failures take NO parameter -- both are DERIVED here from
--           the clock and from the two parameters above, so neither of the two lanes that load this
--           file needed a signature change to acquire the backoff. See their clauses below.
--
-- What this returns: exactly one row, the state as it now stands -- including the two derived
-- columns -- so a caller can log what it believes it just wrote without a second read.
--
-- How this query works, clause by clause:
--
--   INSERT ... ON CONFLICT (view_name) DO UPDATE
--     agri.matview_refresh_state.view_name is the primary key (design doc section 5.2), so every
--     call after the first for a given view is an update, never a second row.
--
--   source_watermark = EXCLUDED.source_watermark
--     Always overwritten, unconditionally, whether this attempt succeeded or failed: it records
--     the source state THIS attempt observed, which is what the next tick's watermark comparison
--     needs regardless of how the attempt landed.
--
--   refreshed_at = COALESCE(EXCLUDED.refreshed_at, agri.matview_refresh_state.refreshed_at)
--     A failed attempt passes refreshed_at (query parameter) as NULL, which COALESCE reads as
--     "keep whatever was stored before" rather than clearing a real prior success. On the very
--     first-ever row for a view that fails on its first attempt, the existing side of the COALESCE
--     does not exist yet either, so the column legitimately stays NULL -- exactly the "never
--     successfully refreshed" state the caller's (priority, refreshed_at ASC NULLS FIRST) ordering
--     is built to prioritise.
--
--   duration_ms / row_count / outcome = EXCLUDED.*
--     Always overwritten: these describe THIS attempt, not the last successful one, so an operator
--     reading the row after a failure sees the failure's own duration and outcome rather than a
--     stale success's numbers sitting underneath a misleading refreshed_at.
--
--   last_attempt_at = now()
--     Written on EVERY recorded attempt, whichever way it landed, and taken from the DATABASE
--     clock rather than from a caller-supplied parameter -- the two lanes that write this table
--     run in different processes and the backoff below compares against this column, so one clock
--     is the only shape that cannot skew against itself. This is the column refreshed_at could
--     never be: the COALESCE above means a view that has never once succeeded keeps refreshed_at
--     NULL forever, so "how long since we last TRIED this" had no answer in the pre-0025 shape,
--     and the caller's gate read it as "never attempted" and re-attempted it on every single tick.
--
--   consecutive_failures = CASE ... END
--     Three-way, and the ELSE branch is the load-bearing one:
--       * a non-NULL refreshed_at means this attempt SUCCEEDED -> reset to 0, so a view someone
--         actually fixes leaves backoff immediately rather than decaying out of it.
--       * outcome = 'failed' means a REFRESH statement was issued and raised -> increment. This is
--         the only outcome that costs real seconds, and it is the only one that earns a backoff.
--       * anything else -- 'skipped_missing' today -- LEAVES THE COUNTER ALONE. A missing relation
--         costs one catalogue lookup and issues no REFRESH at all, so there is nothing to back
--         off from; counting it would also make a database that simply has not had drizzle/0029
--         applied yet look like a repeatedly-failing one, which is a different fault with a
--         different fix. Both lanes that write this file use the same 'failed' literal
--         (MatviewRefreshOutcome in jobs/matview_refresh.py, MaterializedViewRefreshStatus in
--         jobs/strategy_mv_refresh.py), so neither needs a new bind parameter to get this.
--
--     NOTE the deliberate asymmetry with refreshed_at's COALESCE, because it looks like an
--     inconsistency and is not: refreshed_at answers "when was this view last CORRECT", which a
--     failure must not erase, while consecutive_failures answers "is this view failing RIGHT NOW",
--     which only a success may clear. Keeping the COALESCE and adding this counter is what makes
--     the two questions separately answerable; changing the COALESCE would have destroyed the
--     first answer to fix the second.
INSERT INTO agri.matview_refresh_state (
    view_name, source_watermark, refreshed_at, duration_ms, row_count, outcome,
    last_attempt_at, consecutive_failures
)
VALUES (
    :view_name, :source_watermark, :refreshed_at, :duration_ms, :row_count, :outcome,
    now(), CASE WHEN :outcome = 'failed' THEN 1 ELSE 0 END
)
ON CONFLICT (view_name) DO UPDATE SET
    source_watermark = EXCLUDED.source_watermark,
    refreshed_at = COALESCE(EXCLUDED.refreshed_at, agri.matview_refresh_state.refreshed_at),
    duration_ms = EXCLUDED.duration_ms,
    row_count = EXCLUDED.row_count,
    outcome = EXCLUDED.outcome,
    last_attempt_at = now(),
    consecutive_failures = CASE
        WHEN EXCLUDED.refreshed_at IS NOT NULL THEN 0
        WHEN EXCLUDED.outcome = 'failed' THEN agri.matview_refresh_state.consecutive_failures + 1
        ELSE agri.matview_refresh_state.consecutive_failures
    END
RETURNING view_name, source_watermark, refreshed_at, duration_ms, row_count, outcome,
          last_attempt_at, consecutive_failures
