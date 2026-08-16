-- matview_refresh_watermark_forecast_publication
-- Purpose: the newest forecast publication time, the change watermark for
--          agri.mv_forecast_ml_daily_serving.
-- Loaded by: agri_data_service.jobs.matview_refresh
-- Params: none.
--
-- Publication, not run completion: the serving matview covers PUBLISHED forecasts, so a run that
-- finished and was never published must not make it look stale.
--
-- Returns exactly one row.
SELECT max(published_at) AS watermark
FROM agri.forecast_publication
