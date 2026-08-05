-- Serve the 381 published evacuation-zones rows that have never had a tile
-- function. Modelled exactly on geo.sensor_tiles (0001_handy_riptide.sql):
-- same bounds/status='published' filter shape, MVT tag pluralized from the
-- function-name stem. Created after 0008_geometry_dimension, so the
-- search_path pin geo.geometry now requires must be declared inline here
-- rather than retrofitted with a later ALTER FUNCTION.
CREATE OR REPLACE FUNCTION geo.evacuation_zone_tiles(z integer, x integer, y integer)
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

  SELECT COALESCE(ST_AsMVT(tile, 'evacuation_zones', 4096, 'geom'), ''::bytea) INTO mvt
  FROM (
    SELECT
      f.id,
      ST_AsMVTGeom(ST_Transform(f.geom, 3857), bounds_3857, 4096, 64, true) AS geom,
      f.properties ->> 'evacuationAreaName' AS evacuation_area_name,
      f.properties ->> 'fireName' AS fire_name,
      f.properties ->> 'county' AS county,
      f.properties ->> 'severity' AS severity,
      f.properties ->> 'evacuationLevelLabel' AS evacuation_level_label,
      f.properties ->> 'structuresWithin' AS structures_within,
      f.properties ->> 'populationWithin' AS population_within
    FROM geo.features f
    JOIN geo.layers l ON f.layer_id = l.id
    WHERE l.name = 'evacuation-zones'
      AND l.is_public IS TRUE
      AND f.status = 'published'
      AND f.geom IS NOT NULL
      AND f.geom && bounds_4326
      AND ST_Intersects(f.geom, bounds_4326)
  ) AS tile;

  RETURN mvt;
END;
$$;
