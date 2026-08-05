-- geo.sensor_tiles() has always built its ST_AsMVT SELECT list from
-- sensor_type/status/name, but no producer -- push or pull -- has ever
-- written those keys into geo.features.properties. The NWS ground-station
-- producer (services/agri-data-service/src/agri_data_service/ingest/sensors.py
-- build_sensor_reading_write, lines 455-477) writes network, sensor_id,
-- station_name, and the reading's own timestamp under the camelCase key
-- observedAt. network is guaranteed on every published row: the roster
-- filter (_matches_networks, sensors.py:207-211) rejects a station before
-- it is ever collected unless its provider matches the configured network
-- allowlist, which is never empty (resolve_station_networks falls back to
-- DEFAULT_STATION_NETWORKS rather than returning an empty set). station_name
-- stays optional, matching the producer's own optional write.
--
-- Extracted with ->> 'observedAt' (the upstream/producer key, verbatim) but
-- aliased to observed_at, because src/lib/map/hover-fields.ts formatSensorStation
-- and src/lib/map/layers.ts sensorsLayer both read the snake_case tile columns
-- network / sensor_id / station_name / observed_at. Signature (z,x,y) and the
-- f.status = 'published' filter are unchanged, so infra/martin/martin.yaml needs
-- no update. Modelled on geo.evacuation_zone_tiles (0009_evacuation_zone_tiles.sql);
-- created after 0008_geometry_dimension, so the search_path pin must be declared
-- inline here rather than retrofitted with a later ALTER FUNCTION.
CREATE OR REPLACE FUNCTION geo.sensor_tiles(z integer, x integer, y integer)
RETURNS bytea
LANGUAGE plpgsql
STABLE
PARALLEL SAFE
SET search_path = public, pg_catalog
AS $$
DECLARE
  bounds_3857 geometry;
  bounds_4326 geometry;
  mvt bytea;
BEGIN
  bounds_3857 := ST_TileEnvelope(z, x, y);
  bounds_4326 := ST_Transform(bounds_3857, 4326);

  SELECT COALESCE(ST_AsMVT(tile, 'sensors', 4096, 'geom'), ''::bytea) INTO mvt
  FROM (
    SELECT
      f.id,
      ST_AsMVTGeom(ST_Transform(f.geom, 3857), bounds_3857, 4096, 64, true) AS geom,
      f.properties ->> 'network' AS network,
      f.properties ->> 'sensor_id' AS sensor_id,
      f.properties ->> 'station_name' AS station_name,
      f.properties ->> 'observedAt' AS observed_at
    FROM geo.features f
    JOIN geo.layers l ON f.layer_id = l.id
    WHERE l.name = 'sensors'
      AND l.is_public IS TRUE
      AND f.status = 'published'
      AND f.geom IS NOT NULL
      AND f.geom && bounds_4326
      AND ST_Intersects(f.geom, bounds_4326)
  ) AS tile;

  RETURN mvt;
END;
$$;
