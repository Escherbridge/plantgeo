-- matview_refresh_watermark_watershed_features
-- Purpose: the newest write time among watershed features only, the change watermark for
--          geo.watershed_rollup.
-- Loaded by: agri_data_service.jobs.matview_refresh
-- Params: none.
--
-- SCOPED TO ONE LAYER on purpose. The rollup is built from the watersheds layer alone, so using
-- the whole-table geo.features watermark would rebuild a 12-to-4 hierarchical union every time an
-- unrelated lane landed a fire detection.
--
-- Returns exactly one row.
SELECT max(f.updated_at) AS watermark
FROM geo.features AS f
JOIN geo.layers AS l ON l.id = f.layer_id
WHERE l.name = 'watersheds'
