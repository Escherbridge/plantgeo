-- Purpose: list the sensor stations that published at least one reading on one UTC day -- the
--          station_ids argument pipeline/lanes/sensors.py's day export batches over.
-- Loaded by: agri_data_service.pipeline.parquet.lane_registry
-- Params: observed_day (date -- the UTC calendar day being exported)
--
-- DAY-SCOPED ON PURPOSE. An unscoped "every station this layer has ever seen" query would re-scan
-- the whole ~187k-row sensors population once per exported day; scoping to the day rides
-- ix_features_layer_observation_day instead, and returns exactly the stations the day export can
-- actually find rows for rather than a set padded with stations that went quiet.
--
-- The four predicates below are transcribed from sensors_day_export.sql's own candidate CTE so the
-- two cannot disagree about which rows count as "this layer, live". A station selected here that
-- the export then filters out would silently widen every batch; one filtered out here that the
-- export would have kept would silently drop real readings.
SELECT DISTINCT feature.properties ->> 'sensor_id' AS station_id
FROM geo.features AS feature
JOIN geo.layers AS layer ON layer.id = feature.layer_id
WHERE layer.name = 'sensors'
  AND layer.is_public IS TRUE
  AND feature.status = 'published'
  AND feature.properties ->> 'sensor_id' IS NOT NULL
  AND pg_input_is_valid(feature.properties ->> 'observedAt', 'timestamptz')
  AND geo.feature_observation_day(feature.properties) = (:observed_day)::date
ORDER BY station_id
