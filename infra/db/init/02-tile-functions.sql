CREATE OR REPLACE FUNCTION geo.fire_risk_tiles(z integer, x integer, y integer)
RETURNS bytea
LANGUAGE plpgsql
STABLE
PARALLEL SAFE
AS $$
DECLARE
  mvt bytea;
  bounds geometry;
BEGIN
  bounds := ST_TileEnvelope(z, x, y);

  SELECT INTO mvt ST_AsMVT(tile, 'fire_risk', 4096, 'geom')
  FROM (
    SELECT
      f.id,
      ST_AsMVTGeom(f.geom, bounds, 4096, 64, true) AS geom,
      f.properties->>'risk_level' AS risk_level,
      f.properties->>'severity' AS severity,
      f.properties->>'name' AS name
    FROM geo.features f
    JOIN geo.layers l ON f.layer_id = l.id
    WHERE l.name = 'fire-perimeters'
      AND f.geom && bounds
      AND ST_Intersects(f.geom, bounds)
  ) AS tile;

  RETURN mvt;
END;
$$;

CREATE OR REPLACE FUNCTION geo.sensor_tiles(z integer, x integer, y integer)
RETURNS bytea
LANGUAGE plpgsql
STABLE
PARALLEL SAFE
AS $$
DECLARE
  mvt bytea;
  bounds geometry;
BEGIN
  bounds := ST_TileEnvelope(z, x, y);

  SELECT INTO mvt ST_AsMVT(tile, 'sensors', 4096, 'geom')
  FROM (
    SELECT
      f.id,
      ST_AsMVTGeom(f.geom, bounds, 4096, 64, true) AS geom,
      f.properties->>'sensor_type' AS sensor_type,
      f.properties->>'status' AS status,
      f.properties->>'name' AS name
    FROM geo.features f
    JOIN geo.layers l ON f.layer_id = l.id
    WHERE l.name = 'sensors'
      AND f.geom && bounds
      AND ST_Intersects(f.geom, bounds)
  ) AS tile;

  RETURN mvt;
END;
$$;

CREATE OR REPLACE FUNCTION geo.intervention_tiles(z integer, x integer, y integer)
RETURNS bytea
LANGUAGE plpgsql
STABLE
PARALLEL SAFE
AS $$
DECLARE
  mvt bytea;
  bounds geometry;
BEGIN
  bounds := ST_TileEnvelope(z, x, y);

  SELECT INTO mvt ST_AsMVT(tile, 'interventions', 4096, 'geom')
  FROM (
    SELECT
      f.id,
      ST_AsMVTGeom(f.geom, bounds, 4096, 64, true) AS geom,
      f.properties->>'intervention_type' AS intervention_type,
      f.properties->>'status' AS status,
      f.properties->>'name' AS name,
      f.properties->>'description' AS description
    FROM geo.features f
    JOIN geo.layers l ON f.layer_id = l.id
    WHERE l.name = 'interventions'
      AND f.geom && bounds
      AND ST_Intersects(f.geom, bounds)
  ) AS tile;

  RETURN mvt;
END;
$$;

CREATE OR REPLACE FUNCTION geo.building_tiles(z integer, x integer, y integer)
RETURNS bytea
LANGUAGE plpgsql
STABLE
PARALLEL SAFE
AS $$
DECLARE
  mvt bytea;
  bounds geometry;
BEGIN
  bounds := ST_TileEnvelope(z, x, y);

  SELECT INTO mvt ST_AsMVT(tile, 'buildings', 4096, 'geom')
  FROM (
    SELECT
      b.id,
      ST_AsMVTGeom(b.geom, bounds, 4096, 64, true) AS geom,
      b.height,
      b.levels,
      b.building_type,
      b.name
    FROM geo.osm_buildings b
    WHERE b.geom && bounds
      AND ST_Intersects(b.geom, bounds)
  ) AS tile;

  RETURN mvt;
END;
$$;
