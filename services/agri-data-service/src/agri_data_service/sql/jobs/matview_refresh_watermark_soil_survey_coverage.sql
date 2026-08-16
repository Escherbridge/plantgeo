-- matview_refresh_watermark_soil_survey_coverage
-- Purpose: the newest SSURGO coverage fetch time, the change watermark for the soil-survey
--          rollups (geo.mv_soil_survey_grid, geo.mv_soil_survey_union).
-- Loaded by: agri_data_service.jobs.matview_refresh
-- Params: none.
--
-- fetched_at, NOT updated_at. geo.soil_survey_coverage (drizzle/0013) has no updated_at column at
-- all, so naming one here raises 42703 and fails the whole tick's planning pass before a single
-- view is refreshed. fetched_at is the ledger's write-time column and already carries
-- ix_soil_survey_coverage_fetched_at, so this read is O(1).
--
-- Returns exactly one row.
SELECT max(fetched_at) AS watermark
FROM geo.soil_survey_coverage
