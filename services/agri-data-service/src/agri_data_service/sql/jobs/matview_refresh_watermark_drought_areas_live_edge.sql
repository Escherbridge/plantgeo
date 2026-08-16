-- matview_refresh_watermark_drought_areas_live_edge
-- Purpose: the drought publication watermark PLUS the current UTC date, for the one drought view
--          whose newest covered day advances with the calendar rather than with an ingest.
-- Loaded by: agri_data_service.jobs.matview_refresh
-- Params: none.
--
-- WHY THE DATE COMPONENT IS LOAD-BEARING. geo.mv_drought_observation_day's live-edge branch
-- carries the newest release forward to (now() AT TIME ZONE 'UTC')::date, so its newest covered
-- day advances every midnight with zero ingest. drizzle/0029 states this as a MUST. Without it,
-- between UTC midnight and the 24 h backstop the census's newest covered day sits at yesterday
-- while resolveDroughtRelease -- which computes the same carry-forward live, in TypeScript --
-- serves drought for today, and the slider then reports a coverage gap for a day the drought
-- layer is painting. That is the exact confident wrongness this workstream exists to remove.
--
-- Returns exactly one row.
SELECT max(valid_date) AS watermark,
       count(*) AS row_count,
       (now() AT TIME ZONE 'UTC')::date AS current_utc_day
FROM geo.drought_areas
