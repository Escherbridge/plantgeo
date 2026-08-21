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
-- Resolves 'watersheds' to its layer_id via an uncorrelated subquery rather than joining
-- geo.layers onto the scan: geo.features is partitioned by layer_id, and a join filtered on
-- l.name never propagates far enough for the planner to prune to that one partition -- this gate
-- would otherwise pay the full 11-partition fan-out on every refresh tick.
--
-- Returns exactly one row.
SELECT max(f.updated_at) AS watermark
FROM geo.features AS f
WHERE f.layer_id = (SELECT id FROM geo.layers WHERE name = 'watersheds')
