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
-- What this returns: exactly one row, the state as it now stands, so a caller can log what it
-- believes it just wrote without a second read.
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
INSERT INTO agri.matview_refresh_state (view_name, source_watermark, refreshed_at, duration_ms, row_count, outcome)
VALUES (:view_name, :source_watermark, :refreshed_at, :duration_ms, :row_count, :outcome)
ON CONFLICT (view_name) DO UPDATE SET
    source_watermark = EXCLUDED.source_watermark,
    refreshed_at = COALESCE(EXCLUDED.refreshed_at, agri.matview_refresh_state.refreshed_at),
    duration_ms = EXCLUDED.duration_ms,
    row_count = EXCLUDED.row_count,
    outcome = EXCLUDED.outcome
RETURNING view_name, source_watermark, refreshed_at, duration_ms, row_count, outcome
