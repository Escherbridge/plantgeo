-- matview_refresh_watermark_features_updated_at
-- Purpose: the newest write time in geo.features, used as the change watermark for every
--          feature-backed matview whose contents depend only on what has been written
--          (geo.mv_layer_feature_stats, geo.mv_feature_observation_day). A tick whose value
--          matches the one recorded for a view skips that view's REFRESH entirely.
-- Loaded by: agri_data_service.jobs.matview_refresh
-- Params: none.
--
-- O(1) ONLY WITH AN INDEX LEADING ON updated_at. geo.ix_features_updated_at is that index, and
-- drizzle/0029 asserts its existence before creating anything -- idx_features_layer_updated_at
-- leads with layer_id and cannot serve this, which is why it has read 18,949,770,956 tuples to
-- return 31,035.
--
-- Returns exactly one row. watermark is NULL on an empty table, which canonicalizes to a stable
-- signature and therefore reads as "unchanged", which is correct: nothing to refresh from.
SELECT max(updated_at) AS watermark
FROM geo.features
