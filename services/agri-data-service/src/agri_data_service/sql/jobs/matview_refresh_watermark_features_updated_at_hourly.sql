-- matview_refresh_watermark_features_updated_at_hourly
-- Purpose: the same geo.features write watermark, PLUS the current hour, for the one view whose
--          contents move with the clock rather than with the data.
-- Loaded by: agri_data_service.jobs.matview_refresh
-- Params: none.
--
-- WHY THE CLOCK COMPONENT IS NOT PADDING. geo.mv_layer_hourly_activity materialises a window
-- relative to now() (created_at >= date_trunc('hour', now()) - interval '168 hours'), so it goes
-- wrong by one bucket per hour even when nothing is written and max(updated_at) has not moved.
-- drizzle/0029 states this as a MUST for exactly this view. Its 3,600 s max-staleness bound would
-- eventually force the refresh anyway, but relying on that makes the watermark gate do nothing
-- here; the hour component makes the gate the mechanism and the bound the backstop.
--
-- Returns exactly one row.
SELECT max(updated_at) AS watermark,
       date_trunc('hour', now()) AS current_hour
FROM geo.features
