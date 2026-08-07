-- Put the observation day on every style-baked tile layer, so the time slider can
-- reach layers that were never on it.
--
-- fire-perimeters, evacuation-zones, burn-severity and sensors are Martin function
-- sources baked into the map style, not React-mounted readers. Nothing in that path
-- has ever taken a date: the functions select every published row in the tile
-- envelope, so scrubbing the slider to 2023 left August-2026 fire perimeters and
-- evacuation zones sitting on the map with nothing saying so. Those four layers hold
-- 388 + 119 + 478 + 15,769 published rows in production and every one of them was
-- drawn at every date on a four-year axis.
--
-- The fix is an ATTRIBUTE, not a parameter. Two alternatives were rejected:
--
--   * A `query json` parameter per function, filtered server-side. That means DROP +
--     CREATE on four live functions (Postgres cannot add a parameter with CREATE OR
--     REPLACE) and a Martin restart to re-read the catalog, and a missing function
--     404s the WHOLE composite -- `martin-dynamic` carries six sources in one
--     MapLibre source, so one bad signature blanks every dynamic layer on the map.
--   * Splitting the composite so each layer could carry its own dated URL. Same
--     blast radius, and Martin cannot emit `vector_layers` for function sources, so
--     the composite split has its own constraints (see src/lib/map/sources.ts).
--
-- Emitting the day instead keeps every signature identical, so this is CREATE OR
-- REPLACE with no catalog change and no restart -- Martin's own `tile_expiry: 5m`
-- ages the cached tiles out on its own. The client filters with a MapLibre
-- expression, which also means scrubbing re-filters already-downloaded tiles
-- instead of refetching them: a day-granular scrub over these layers costs zero
-- requests. At this row count shipping every feature and filtering in the browser
-- is the cheaper design, not a compromise.
--
-- The rule lives in ONE place, geo.feature_observation_day, because the tile filter
-- and the slider's axis MUST agree. They are derived from the same
-- COALESCE(observedAt, updatedAt, polygonDateTime) that
-- src/lib/server/services/environmental-read-model.ts uses for
-- OBSERVATION_TIME_TEXT; if these two ever disagree the slider will advertise a day
-- as published and the tiles will draw nothing for it, which reads as a rendering
-- bug and is the hardest class of gap to diagnose.

-- Measured against production 2026-08-06: burn-severity and evacuation-zones and
-- sensors date on `observedAt`, fire-perimeters on `polygonDateTime`, and no row of
-- any of them carries more than one of the three keys.
CREATE OR REPLACE FUNCTION geo.feature_observation_day(feature_properties jsonb)
RETURNS date
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
SET search_path = public, pg_catalog
AS $$
  -- The regex guard is not decoration. Without it one malformed upstream timestamp
  -- raises inside ST_AsMVT and 500s the entire tile -- every feature in it, not just
  -- the bad row -- which is the failure mode 0012 called out for bare numeric casts.
  -- A row that cannot be dated returns NULL and is then treated as undated by the
  -- client filter, which shows it at every date rather than hiding it.
  SELECT CASE
    WHEN COALESCE(
      feature_properties ->> 'observedAt',
      feature_properties ->> 'updatedAt',
      feature_properties ->> 'polygonDateTime'
    ) ~ '^\d{4}-\d{2}-\d{2}'
    THEN (
      COALESCE(
        feature_properties ->> 'observedAt',
        feature_properties ->> 'updatedAt',
        feature_properties ->> 'polygonDateTime'
      )
    )::timestamptz::date
  END;
$$;
--> statement-breakpoint

-- Emitted as text rather than as a date so the MVT attribute arrives in the browser
-- as a plain "YYYY-MM-DD" string. MapLibre expressions compare strings
-- lexicographically, and ISO-8601 dates sort correctly that way, so
-- ["<=", ["get","observed_day"], "2024-03-01"] is exactly the "as the record stood
-- on that day" test with no date parsing in the style at all.
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
      f.properties ->> 'observedAt' AS observed_at,
      geo.feature_observation_day(f.properties)::text AS observed_day
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
--> statement-breakpoint

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
      f.properties ->> 'populationWithin' AS population_within,
      geo.feature_observation_day(f.properties)::text AS observed_day
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
--> statement-breakpoint

CREATE OR REPLACE FUNCTION geo.fire_risk_tiles(z integer, x integer, y integer)
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

  SELECT COALESCE(ST_AsMVT(tile, 'fire_risk', 4096, 'geom'), ''::bytea) INTO mvt
  FROM (
    SELECT
      f.id,
      ST_AsMVTGeom(ST_Transform(f.geom, 3857), bounds_3857, 4096, 64, true) AS geom,
      f.properties ->> 'risk_level' AS risk_level,
      f.properties ->> 'severity' AS severity,
      f.properties ->> 'name' AS name,
      geo.feature_observation_day(f.properties)::text AS observed_day
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
--> statement-breakpoint

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
      f.properties ->> 'observedAt' AS observed_at,
      geo.feature_observation_day(f.properties)::text AS observed_day
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
