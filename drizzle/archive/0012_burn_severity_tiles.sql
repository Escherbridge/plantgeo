-- Serve the 478 published burn-severity rows that migration 0011 gave a
-- geo.layers row but no tile function, so the layer has never had any path to
-- the map at all. Modelled exactly on geo.evacuation_zone_tiles
-- (0009_evacuation_zone_tiles.sql): same bounds/is_public/status='published'
-- filter shape, MVT tag taken from the function-name stem. Created after
-- 0008_geometry_dimension, so the search_path pin geo.geometry now requires is
-- declared inline here rather than retrofitted with a later ALTER FUNCTION.
--
-- The MVT tag is 'burn_severity' and src/lib/map/layers.ts burnSeverityLayer
-- declares that exact string as its source-layer. A mismatch renders nothing
-- and reports no error, so the two are only ever changed together.
--
-- On the SELECT list, and the lesson 0010_sensor_tile_properties.sql paid for:
-- every key here is one that services/agri-data-service/.../ingest/mtbs.py
-- build_mtbs_write actually writes, under its exact camelCase upstream spelling,
-- aliased to the snake_case column the style and hover layers read.
--
-- acres and fire_year are cast out of jsonb rather than served as text because
-- the style interpolates on acres, and a text attribute would make that
-- expression fall through to its default for every feature. The casts are
-- guarded on jsonb_typeof rather than written bare: mtbs.py types both as
-- optional numbers, and a bare cast would turn one malformed upstream row into
-- a 500 for the whole tile instead of one feature missing one attribute.
--
-- severity_class is emitted even though it is null on all 478 published rows
-- today. That is not the dead-column bug 0010 fixed -- there the keys had never
-- been written by any producer; here the producer writes the key faithfully and
-- the upstream simply has nothing to put in it. MTBS distributes burn severity
-- as a thematic raster, and this polygon layer carries the per-fire dNBR
-- thresholds instead (mtbs.py MtbsSeverityThresholds), which are mapping
-- calibration and not an outcome measure -- so nothing here or in the style may
-- treat them as a severity value. Carrying the column means a future MTBS
-- release that does classify its polygons lights up with a paint change alone.
CREATE OR REPLACE FUNCTION geo.burn_severity_tiles(z integer, x integer, y integer)
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

  SELECT COALESCE(ST_AsMVT(tile, 'burn_severity', 4096, 'geom'), ''::bytea) INTO mvt
  FROM (
    SELECT
      f.id,
      ST_AsMVTGeom(ST_Transform(f.geom, 3857), bounds_3857, 4096, 64, true) AS geom,
      f.properties ->> 'fireId' AS fire_id,
      f.properties ->> 'fireName' AS fire_name,
      CASE
        WHEN jsonb_typeof(f.properties -> 'fireYear') = 'number'
          THEN (f.properties ->> 'fireYear')::integer
      END AS fire_year,
      f.properties ->> 'ignitionDate' AS ignition_date,
      f.properties ->> 'fireType' AS fire_type,
      f.properties ->> 'assessmentType' AS assessment_type,
      CASE
        WHEN jsonb_typeof(f.properties -> 'acres') = 'number'
          THEN (f.properties ->> 'acres')::double precision
      END AS acres,
      f.properties ->> 'severityClass' AS severity_class,
      -- The release publication date, not the ignition: what the warehouse
      -- could have known, which is what the time slider reads.
      f.properties ->> 'observedAt' AS observed_at
    FROM geo.features f
    JOIN geo.layers l ON f.layer_id = l.id
    WHERE l.name = 'burn-severity'
      AND l.is_public IS TRUE
      AND f.status = 'published'
      AND f.geom IS NOT NULL
      AND f.geom && bounds_4326
      AND ST_Intersects(f.geom, bounds_4326)
  ) AS tile;

  RETURN mvt;
END;
$$;
