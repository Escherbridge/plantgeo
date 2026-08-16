-- select_matview_refresh_state
-- Purpose: read every row this service has ever recorded about a matview's last refresh, in one
--          statement per tick (design doc section 5.3 step 1: "Read all rows of
--          agri.matview_refresh_state in one query"). The table holds one row per view name across
--          every lane that writes to it -- this lane's own eleven views plus, for observability
--          parity, jobs.strategy_mv_refresh's three -- so a single unfiltered read is cheaper than
--          one lookup per view and lets the caller reason about every view's staleness at once.
-- Loaded by: agri_data_service.jobs.matview_refresh, agri_data_service.jobs.strategy_mv_refresh
-- Params: none.
--
-- What this returns: zero or more rows, at most a few dozen -- the table has one row per matview
-- this codebase knows how to refresh, never one per refresh attempt. source_watermark and outcome
-- are NEVER NULL once a row exists (every INSERT this service issues supplies both); refreshed_at,
-- duration_ms and row_count ARE nullable, and NULL there means "recorded before any refresh of
-- this view actually succeeded" -- a state a caller must treat as maximally stale, ordering it
-- first rather than dividing by a missing timestamp.
SELECT view_name, source_watermark, refreshed_at, duration_ms, row_count, outcome
FROM agri.matview_refresh_state
