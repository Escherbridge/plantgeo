-- Emit the intervention classification the platform actually writes.
-- createIntervention persists "priority" (High/Medium/Low) and never writes
-- "intervention_type", so the previous tile payload carried a column that was
-- always NULL and the map styled every zone with a single fallback colour.
-- "intervention_type" is retained for a future typed producer.
CREATE OR REPLACE FUNCTION geo.intervention_tiles(z integer, x integer, y integer)
RETURNS bytea
LANGUAGE plpgsql
STABLE
PARALLEL SAFE
AS $$
DECLARE
  bounds_3857 geometry;
  bounds_4326 geometry;
  mvt bytea;
BEGIN
  bounds_3857 := ST_TileEnvelope(z, x, y);
  bounds_4326 := ST_Transform(bounds_3857, 4326);

  SELECT COALESCE(ST_AsMVT(tile, 'interventions', 4096, 'geom'), ''::bytea) INTO mvt
  FROM (
    SELECT
      f.id,
      ST_AsMVTGeom(ST_Transform(f.geom, 3857), bounds_3857, 4096, 64, true) AS geom,
      f.properties ->> 'intervention_type' AS intervention_type,
      f.properties ->> 'priority' AS priority,
      f.properties ->> 'status' AS status,
      f.properties ->> 'name' AS name,
      f.properties ->> 'description' AS description
    FROM geo.features f
    JOIN geo.layers l ON f.layer_id = l.id
    WHERE l.name = 'interventions'
      AND l.is_public IS TRUE
      AND f.status = 'published'
      AND f.geom IS NOT NULL
      AND f.geom && bounds_4326
      AND ST_Intersects(f.geom, bounds_4326)
  ) AS tile;

  RETURN mvt;
END;
$$;
