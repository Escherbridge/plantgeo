-- fire_risk_tiles projected risk_level/name, which ingested WFIGS rows never carry;
-- emit the properties the ingester actually writes instead.
CREATE OR REPLACE FUNCTION geo.fire_risk_tiles(z integer, x integer, y integer)
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

  SELECT COALESCE(ST_AsMVT(tile, 'fire_risk', 4096, 'geom'), ''::bytea) INTO mvt
  FROM (
    SELECT
      f.id,
      ST_AsMVTGeom(ST_Transform(f.geom, 3857), bounds_3857, 4096, 64, true) AS geom,
      f.properties ->> 'incidentName' AS incident_name,
      CASE WHEN f.properties ->> 'gisAcres' ~ '^-?[0-9]+(\.[0-9]+)?$'
        THEN (f.properties ->> 'gisAcres')::numeric END AS gis_acres,
      CASE WHEN f.properties ->> 'percentContained' ~ '^-?[0-9]+(\.[0-9]+)?$'
        THEN (f.properties ->> 'percentContained')::numeric END AS percent_contained,
      f.properties ->> 'severity' AS severity,
      f.properties ->> 'fireCause' AS fire_cause,
      f.properties ->> 'pooState' AS poo_state,
      f.properties ->> 'fireDiscoveryDateTime' AS discovered_at
    FROM geo.features f
    JOIN geo.layers l ON f.layer_id = l.id
    WHERE l.name = 'fire-perimeters'
      AND l.is_public IS TRUE
      AND f.status = 'published'
      AND f.geom IS NOT NULL
      AND f.geom && bounds_4326
      AND ST_Intersects(f.geom, bounds_4326)
  ) AS tile;

  RETURN mvt;
END;
$$;
