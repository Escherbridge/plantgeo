-- Put the observation day on every style-baked tile layer, so the time slider can
-- reach layers that were never on it: fire-perimeters, evacuation-zones,
-- burn-severity and sensors are Martin function sources baked into the map style,
-- and nothing in that path has ever taken a date. All four drew every published row
-- at every date on a four-year axis.
--
-- The fix is an ATTRIBUTE, not a per-function `query json` parameter, so every
-- signature stays identical and this is CREATE OR REPLACE with no catalog change and
-- no Martin restart. The rule lives in ONE place, geo.feature_observation_day,
-- because the tile filter and the slider's axis MUST agree on the day.
--
-- Why an attribute rather than a parameter or a composite split, why the day is the
-- PUBLISHER-NAMED day, and why the IMMUTABLE declaration below is a deliberate
-- promotion: src/lib/server/db/AGENTS.md §tile-observation-day.
-- src/__tests__/lib/observation-day-contract.test.ts fails if this function and
-- environmental-read-model.ts's OBSERVATION_DAY ever stop deriving the day alike.

-- Measured against production 2026-08-06: burn-severity and evacuation-zones and
-- sensors date on `observedAt`, fire-perimeters on `polygonDateTime`, and no row of
-- any of them carries more than one of the three keys.
CREATE OR REPLACE FUNCTION geo.feature_observation_day(feature_properties jsonb)
RETURNS date
LANGUAGE sql
-- Declared IMMUTABLE over two callees PostgreSQL catalogues STABLE. That is a knowing,
-- safe promotion, not an oversight -- AGENTS.md §tile-observation-day proves both halves.
IMMUTABLE
PARALLEL SAFE
SET search_path = public, pg_catalog
AS $$
  -- The ten characters the publisher named, read once so the COALESCE order cannot fork.
  -- NEVER `(...)::timestamptz::date` and never `(... AT TIME ZONE 'UTC')::date`: an
  -- instant-based conversion moves 6,279 of the 16,743 production water-gauge rows onto
  -- the day AFTER the one they name, which is the disagreement this function prevents.
  --
  -- BOTH guards must hold before to_date runs, and neither is decoration. The regex proves
  -- the shape; pg_input_is_valid proves the day EXISTS, because `to_date('2026-02-31',
  -- 'YYYY-MM-DD')` raises "date/time field value out of range" and one raise inside
  -- ST_AsMVT blanks the entire tile -- every feature in it, not just the bad row. A row
  -- that cannot be dated returns NULL and is treated as undated by the client filter,
  -- which shows it at every date rather than hiding it.
  SELECT CASE
           WHEN named.day ~ '^\d{4}-\d{2}-\d{2}$'
                AND pg_input_is_valid(named.day, 'date')
             THEN to_date(named.day, 'YYYY-MM-DD')
         END
    FROM (
      SELECT substring(
        COALESCE(
          feature_properties ->> 'observedAt',
          feature_properties ->> 'updatedAt',
          feature_properties ->> 'polygonDateTime'
        ),
        1,
        10
      )
    ) AS named(day);
$$;
--> statement-breakpoint

-- `::text`, so the MVT attribute arrives as a plain "YYYY-MM-DD" string that a MapLibre
-- expression can compare lexicographically with no date parsing in the style at all.
-- See src/lib/server/db/AGENTS.md §tile-observation-day.
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
