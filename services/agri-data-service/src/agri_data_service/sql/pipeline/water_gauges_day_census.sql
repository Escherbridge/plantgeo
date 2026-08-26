-- water_gauges_day_census
-- Purpose: count every published water-gauges row on the publisher-named day used by the
--          Parquet partitioner and map time axis.
-- Loaded by: scripts/water_gauges_postgres_drain.py
-- Params: none
--
-- How this query works, clause by clause:
--
--   FROM geo.layers JOIN geo.features
--     Resolves the water-gauges layer by its stable slug and reads only that layer's feature rows.
--
--   WHERE layers.name = 'water-gauges' AND features.status = 'published'
--     Excludes every other lane and every draft or rejected row, matching
--     water_gauges_day_export.sql exactly.
--
--   geo.feature_observation_day(features.properties)
--     Uses the publisher-named first ten timestamp characters. A UTC instant-to-date conversion
--     would move valid rows to a different partition day and make this census disagree with both
--     the exporter and the client slider.
--
--   GROUP BY / count
--     Produces one bounded result row per real source day. Missing calendar days do not appear and
--     therefore cannot be mistaken for obligations or turned into fabricated absence markers.
SELECT
    geo.feature_observation_day(features.properties) AS observed_day,
    count(*)::bigint                                  AS row_count
FROM geo.layers AS layers
JOIN geo.features AS features
    ON features.layer_id = layers.id
WHERE layers.name = 'water-gauges'
  AND features.status = 'published'
GROUP BY geo.feature_observation_day(features.properties)
ORDER BY observed_day
