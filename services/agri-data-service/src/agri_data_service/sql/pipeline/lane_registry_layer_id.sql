-- Purpose: resolve one geo.layers row's id from its slug, for the two lanes whose day export takes
--          a pre-resolved layer id rather than joining on the layer name itself.
-- Loaded by: agri_data_service.pipeline.parquet.lane_registry
-- Params: layer_name (text -- the geo.layers.name slug, e.g. fire-detections)
--
-- Both fire_detections_day_export.sql and weather_observations_day_export.sql take that id as a
-- bound literal on purpose (see their own headers): a bound uuid lets the planner use
-- ix_features_layer_observation_day, while joining geo.layers inside the day query does not.
-- Resolving it once per day here keeps that property without any lane spelling the lookup twice.
--
-- No status or visibility predicate. This resolves an identifier, not a population; every
-- row-level filter belongs in the day export that consumes the id, where it is already
-- transcribed from the canonical serving query.
SELECT layer.id AS layer_id
FROM geo.layers AS layer
WHERE layer.name = :layer_name
