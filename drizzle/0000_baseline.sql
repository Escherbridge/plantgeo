-- PlantGeo Drizzle greenfield baseline.
--
-- This single revision replaces migrations 0000..0040, which are retained unedited in
-- `drizzle/archive/`. It is generated from production's ACTUAL schema by pg_dump 18, not
-- hand-transcribed, so it cannot drift from what production really is. Regenerate it with
-- `scripts/generate-drizzle-baseline.mjs`; that file documents how the artifact is produced.
--
-- WHY THE CHAIN WAS COLLAPSED, in one line each -- `drizzle/archive/README.md` has the evidence:
--   * Four steps had to run OUT OF BAND, BETWEEN migrations, but drizzle-orm applies every
--     pending migration in ONE transaction, and CREATE INDEX CONCURRENTLY cannot run in one.
--   * Seven of the last ten migrations had never been applied to production at all.
--
-- PREREQUISITES, in order. This baseline creates no extensions, no `agri` objects and no rows:
--   1. CREATE EXTENSION postgis, pgcrypto, vector, btree_gist
--   2. Alembic `upgrade head`, run separately -- seven objects below read Alembic-owned agri
--      tables (agri.signal_observation, agri.spatial_cell, agri.strategies and seven more).
--   3. This baseline.
--   4. `drizzle/seed/` -- this dump is --schema-only, and an empty `geo.layers` makes
--      /api/ready return 503, which fails the Railway healthcheck.
-- `scripts/bootstrap-database.mjs` does 1, 3 and 4, and REFUSES 3 until 2 has been done. It does
-- not run Alembic itself: that is a Python toolchain the Next.js image does not carry.
--
-- VERIFIED 2026-09-08 by building an empty database on the production server and comparing every
-- column, index, constraint, function, view definition and trigger against production by
-- catalogue query: zero differences in public, geo and tracking.

--
-- PostgreSQL database dump
--



SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: geo; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS geo;


--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS public;


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


--
-- Name: tracking; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS tracking;


--
-- Name: building_tiles(integer, integer, integer); Type: FUNCTION; Schema: geo; Owner: -
--

CREATE FUNCTION geo.building_tiles(z integer, x integer, y integer) RETURNS bytea
    LANGUAGE plpgsql STABLE PARALLEL SAFE
    SET search_path TO 'public', 'pg_catalog'
    AS $$
DECLARE
  bounds_3857 geometry;
  bounds_4326 geometry;
  mvt bytea;
BEGIN
  bounds_3857 := ST_TileEnvelope(z, x, y);
  bounds_4326 := ST_Transform(bounds_3857, 4326);

  SELECT COALESCE(ST_AsMVT(tile, 'buildings', 4096, 'geom'), ''::bytea) INTO mvt
  FROM (
    SELECT
      b.id,
      ST_AsMVTGeom(ST_Transform(b.geom, 3857), bounds_3857, 4096, 64, true) AS geom,
      b.height,
      b.levels,
      b.building_type,
      b.name
    FROM geo.osm_buildings b
    WHERE b.is_public IS TRUE
      AND b.geom && bounds_4326
      AND ST_Intersects(b.geom, bounds_4326)
  ) AS tile;

  RETURN mvt;
END;
$$;


--
-- Name: burn_severity_tiles(integer, integer, integer); Type: FUNCTION; Schema: geo; Owner: -
--

CREATE FUNCTION geo.burn_severity_tiles(z integer, x integer, y integer) RETURNS bytea
    LANGUAGE plpgsql STABLE PARALLEL SAFE
    SET search_path TO 'public', 'pg_catalog'
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
    LIMIT 10000
  ) AS tile;

  RETURN mvt;
END;
$$;


--
-- Name: evacuation_zone_tiles(integer, integer, integer); Type: FUNCTION; Schema: geo; Owner: -
--

CREATE FUNCTION geo.evacuation_zone_tiles(z integer, x integer, y integer) RETURNS bytea
    LANGUAGE plpgsql STABLE PARALLEL SAFE
    SET search_path TO 'public', 'pg_catalog'
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
    LIMIT 10000
  ) AS tile;

  RETURN mvt;
END;
$$;


--
-- Name: feature_observation_day(jsonb); Type: FUNCTION; Schema: geo; Owner: -
--

CREATE FUNCTION geo.feature_observation_day(feature_properties jsonb) RETURNS date
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    SET search_path TO 'public', 'pg_catalog'
    AS $_$
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
          feature_properties ->> 'polygonDateTime',
          -- LAST, and the position is the whole point. `fireDiscoveryDateTime` is when the
          -- INCIDENT was found; `polygonDateTime` is when the GEOMETRY on this row was
          -- mapped. They are different events and discovery is routinely the earlier one:
          -- a fire is reported, and the perimeter is flown, digitized and republished for
          -- days or weeks afterwards, each release a new snapshot of the same incident.
          -- Ranked ABOVE polygonDateTime it would re-date all 106 rows that do carry a
          -- polygon timestamp back onto their incident's discovery day, collapsing a
          -- perimeter's revision history onto one day and making the slider show a
          -- late-August footprint as though it had been known in mid-July. Ranked last it
          -- fires only where every timestamp that describes the geometry itself is absent,
          -- which is exactly the 13 rows and nothing else.
          --
          -- It is a fallback, not a co-equal key, so it is also NOT in
          -- PUBLISHER_NAMED_DAY_RULE.observationTimeKeys: the slider axis
          -- (environmental-read-model.ts) still buckets days from the three shared keys.
          -- That divergence advertises no day the axis lacks -- measured, all nine days
          -- these 13 rows land on already appear on the fire-perimeters axis from rows
          -- with a real polygonDateTime -- and the client filter is `<=`, not `=`, so a
          -- dated row draws from its own day forward regardless. See
          -- src/__tests__/lib/observation-day-contract.test.ts, which pins the chain as
          -- the rule's keys in order followed by exactly this one tile-only fallback.
          feature_properties ->> 'fireDiscoveryDateTime'
        ),
        1,
        10
      )
    ) AS named(day);
$_$;


--
-- Name: fire_risk_tiles(integer, integer, integer); Type: FUNCTION; Schema: geo; Owner: -
--

CREATE FUNCTION geo.fire_risk_tiles(z integer, x integer, y integer) RETURNS bytea
    LANGUAGE plpgsql STABLE PARALLEL SAFE
    SET search_path TO 'public', 'pg_catalog'
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
    LIMIT 10000
  ) AS tile;

  RETURN mvt;
END;
$$;


--
-- Name: intervention_tiles(integer, integer, integer); Type: FUNCTION; Schema: geo; Owner: -
--

CREATE FUNCTION geo.intervention_tiles(z integer, x integer, y integer) RETURNS bytea
    LANGUAGE plpgsql STABLE PARALLEL SAFE
    SET search_path TO 'public', 'pg_catalog'
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
    LIMIT 10000
  ) AS tile;

  RETURN mvt;
END;
$$;


--
-- Name: refresh_preaggregate(text); Type: FUNCTION; Schema: geo; Owner: -
--

CREATE FUNCTION geo.refresh_preaggregate(target_view text) RETURNS bigint
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
DECLARE
  allowed CONSTANT text[] := ARRAY[
    'geo.mv_feature_observation_day',
    'geo.mv_signal_observation_day',
    'geo.mv_drought_observation_day',
    'geo.mv_signal_cell_daily',
    'geo.mv_drought_release_index',
    'geo.mv_layer_feature_stats',
    'geo.mv_layer_hourly_activity',
    'geo.mv_soil_survey_grid',
    'geo.mv_soil_survey_union'
  ];
  view_oid oid;
  is_populated boolean;
  row_total bigint;
BEGIN
  IF target_view <> ALL (allowed) THEN
    RAISE EXCEPTION
      'geo.refresh_preaggregate refuses %: not one of the nine pre-aggregation matviews', target_view
      USING HINT = 'Add the view to drizzle/0029 and to the matview-refresh lane before refreshing it here.';
  END IF;

  view_oid := to_regclass(target_view);
  IF view_oid IS NULL THEN
    RAISE EXCEPTION '% is on the allow-list but does not exist in this database', target_view;
  END IF;

  SELECT relispopulated INTO is_populated FROM pg_class WHERE oid = view_oid;

  IF is_populated THEN
    EXECUTE format('REFRESH MATERIALIZED VIEW CONCURRENTLY %s', target_view::regclass::text);
  ELSE
    RAISE WARNING
      '% has never been populated; refreshing NON-concurrently (ACCESS EXCLUSIVE) to heal it', target_view;
    EXECUTE format('REFRESH MATERIALIZED VIEW %s', target_view::regclass::text);
  END IF;

  EXECUTE format('SELECT count(*) FROM %s', target_view::regclass::text) INTO row_total;
  RETURN row_total;
END;
$$;


--
-- Name: FUNCTION refresh_preaggregate(target_view text); Type: COMMENT; Schema: geo; Owner: -
--

COMMENT ON FUNCTION geo.refresh_preaggregate(target_view text) IS 'The ingest-time refresh door for the nine pre-aggregation matviews: allow-listed by name, CONCURRENTLY when the view is populated and plain-with-a-WARNING when it is not. The SCHEDULED path is the matview-refresh lane in the jobs pulse; this is for a verb that just landed data and should not wait out the interval. Returns the view''s row count after the refresh.';


--
-- Name: refresh_watershed_rollup(); Type: FUNCTION; Schema: geo; Owner: -
--

CREATE FUNCTION geo.refresh_watershed_rollup() RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
  REFRESH MATERIALIZED VIEW CONCURRENTLY geo.watershed_rollup;
END;
$$;


--
-- Name: sensor_tiles(integer, integer, integer); Type: FUNCTION; Schema: geo; Owner: -
--

CREATE FUNCTION geo.sensor_tiles(z integer, x integer, y integer) RETURNS bytea
    LANGUAGE plpgsql STABLE PARALLEL SAFE
    SET search_path TO 'public', 'pg_catalog'
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
    SELECT DISTINCT ON (
             f.properties ->> 'sensor_id',
             f.geom,
             geo.feature_observation_day(f.properties)
           )
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
    -- The three DISTINCT ON keys first, in order, as PostgreSQL requires. Then the tie-break:
    -- `observedAt` is ISO-8601 text, so DESC is chronological without parsing a date, and
    -- `f.id DESC` makes the winner total rather than arbitrary among equal timestamps.
    ORDER BY
      f.properties ->> 'sensor_id',
      f.geom,
      geo.feature_observation_day(f.properties),
      f.properties ->> 'observedAt' DESC,
      f.id DESC
    -- Matches infra/martin/martin.yaml's max_feature_count, which cannot be enforced on a
    -- function source. Binding today at z<=4; it truncates stations, never days.
    LIMIT 10000
  ) AS tile;

  RETURN mvt;
END;
$$;


--
-- Name: FUNCTION sensor_tiles(z integer, x integer, y integer); Type: COMMENT; Schema: geo; Owner: -
--

COMMENT ON FUNCTION geo.sensor_tiles(z integer, x integer, y integer) IS 'Sensor MVT tiles, deduplicated to one feature per (sensor_id, location, observation day) and capped at 10,000 features. Each station publishes ~13-30 same-day readings at one identical coordinate, so before drizzle/0038 the z0 tile carried 186,904 features / 14.3 MB, of which all but ~14,375 were exact overdraw; it is now 10,000 features / 753 KB with all 23 observation days intact. The LIMIT sits above an ORDER BY led by sensor_id ON PURPOSE: when it binds it drops whole STATIONS, never whole DAYS, because src/lib/map/tile-layer-date-filter.ts filters these features by observed_day <= selectedDate and a missing day blanks the map for that day. Do not replace this with a bare LIMIT -- measured, a bare LIMIT 10000 at z0/0/0 keeps 19 of 23 days. See drizzle/0038_tile_low_zoom_routing.sql.';


--
-- Name: soil_field(double precision, double precision, double precision, double precision, text, text, date, integer, double precision, double precision, integer); Type: FUNCTION; Schema: geo; Owner: -
--

CREATE FUNCTION geo.soil_field(bbox_west double precision, bbox_south double precision, bbox_east double precision, bbox_north double precision, target_signal text, target_support_key text, through_day date, max_age_days integer, lattice_degrees double precision, blur_sigma_degrees double precision, blur_radius_cells integer) RETURNS TABLE(observed_day date, node_lon double precision, node_lat double precision, smoothed_value double precision, raw_value double precision, source_cell_count integer)
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$
  WITH halo AS (
    SELECT ST_MakeEnvelope(
      bbox_west  - blur_radius_cells * lattice_degrees,
      bbox_south - blur_radius_cells * lattice_degrees,
      bbox_east  + blur_radius_cells * lattice_degrees,
      bbox_north + blur_radius_cells * lattice_degrees,
      4326
    ) AS envelope
  ),
  covered_cell AS (
    SELECT cell.id, ST_X(cell.centroid) AS lon, ST_Y(cell.centroid) AS lat
    FROM agri.spatial_cell AS cell, halo
    WHERE cell.geometry && halo.envelope
  ),
  served AS (
    SELECT max(candidate.observed_at) AS observed_at
    FROM agri.signal_observation AS candidate
    WHERE candidate.cell_id IN (SELECT id FROM covered_cell)
      AND candidate.signal_name = target_signal
      AND candidate.support_key = target_support_key
      AND candidate.observed_at <= (through_day + 1)::timestamptz
      AND candidate.observed_at >  (through_day - max_age_days)::timestamptz
  ),
  observation AS (
    SELECT
      covered_cell.lon,
      covered_cell.lat,
      reading.normalized_value AS value,
      reading.coverage_fraction AS weight
    FROM covered_cell
    JOIN agri.signal_observation AS reading ON reading.cell_id = covered_cell.id
    CROSS JOIN served
    WHERE reading.signal_name = target_signal
      AND reading.support_key = target_support_key
      AND reading.observed_at = served.observed_at
      AND reading.is_observed
      AND reading.quality_flag = 'accepted'
      AND reading.normalized_value IS NOT NULL
  ),
  node AS (
    SELECT
      floor(observation.lon / lattice_degrees) * lattice_degrees + lattice_degrees / 2
        AS node_lon,
      floor(observation.lat / lattice_degrees) * lattice_degrees + lattice_degrees / 2
        AS node_lat,
      sum(observation.value * observation.weight)
        / NULLIF(sum(observation.weight), 0) AS raw_value,
      count(*)::integer AS source_cell_count
    FROM observation
    GROUP BY 1, 2
  )
  SELECT
    (SELECT (served.observed_at AT TIME ZONE 'UTC')::date FROM served) AS observed_day,
    target.node_lon,
    target.node_lat,
    sum(
      neighbour.raw_value
      * exp(-(   (neighbour.node_lon - target.node_lon) ^ 2
               + (neighbour.node_lat - target.node_lat) ^ 2)
            / (2 * GREATEST(blur_sigma_degrees, 1e-9::double precision) ^ 2))
    )
    / NULLIF(sum(
        exp(-(   (neighbour.node_lon - target.node_lon) ^ 2
               + (neighbour.node_lat - target.node_lat) ^ 2)
            / (2 * GREATEST(blur_sigma_degrees, 1e-9::double precision) ^ 2))
      ), 0) AS smoothed_value,
    target.raw_value,
    target.source_cell_count
  FROM node AS target
  JOIN node AS neighbour
    ON abs(neighbour.node_lon - target.node_lon) <= blur_radius_cells * lattice_degrees
   AND abs(neighbour.node_lat - target.node_lat) <= blur_radius_cells * lattice_degrees
  WHERE target.node_lon BETWEEN bbox_west AND bbox_east
    AND target.node_lat BETWEEN bbox_south AND bbox_north
  GROUP BY target.node_lon, target.node_lat, target.raw_value, target.source_cell_count
  ORDER BY target.node_lat, target.node_lon;
$$;


--
-- Name: FUNCTION soil_field(bbox_west double precision, bbox_south double precision, bbox_east double precision, bbox_north double precision, target_signal text, target_support_key text, through_day date, max_age_days integer, lattice_degrees double precision, blur_sigma_degrees double precision, blur_radius_cells integer); Type: COMMENT; Schema: geo; Owner: -
--

COMMENT ON FUNCTION geo.soil_field(bbox_west double precision, bbox_south double precision, bbox_east double precision, bbox_north double precision, target_signal text, target_support_key text, through_day date, max_age_days integer, lattice_degrees double precision, blur_sigma_degrees double precision, blur_radius_cells integer) IS 'Zoom-aggregated ERA5-Land soil field: native 0.25-degree cells for one signal averaged onto a coarser lattice and Gaussian-smoothed across it. Measure-agnostic -- the caller names the signal -- so soil moisture and soil temperature share one definition. Returns grid nodes, not geometry; the marching-squares isobands are built from these in src/lib/geo/isobands.ts.';


--
-- Name: strategy_recommendations_tiles(integer, integer, integer); Type: FUNCTION; Schema: geo; Owner: -
--

CREATE FUNCTION geo.strategy_recommendations_tiles(z integer, x integer, y integer) RETURNS bytea
    LANGUAGE plpgsql STABLE PARALLEL SAFE
    SET search_path TO 'public', 'pg_catalog'
    AS $$
DECLARE
  bounds_3857 geometry;
  bounds_4326 geometry;
  mvt bytea;
BEGIN
  bounds_3857 := ST_TileEnvelope(z, x, y);
  bounds_4326 := ST_Transform(bounds_3857, 4326);

  IF z <= 6 THEN
    SELECT COALESCE(ST_AsMVT(tile, 'strategy-recommendations', 4096, 'geom'), ''::bytea) INTO mvt
    FROM (
      SELECT
        cell_id,
        strategy_id,
        strategy_slug,
        strategy_name,
        strategy_category,
        suitability_score,
        effect_utility_score,
        label_release_key,
        source_count,
        label_review_tier,
        ST_AsMVTGeom(ST_Transform(geom, 3857), bounds_3857, 4096, 64, true) AS geom
      FROM geo.mv_strategy_recommendations_coarse
      WHERE geom && bounds_4326 AND ST_Intersects(geom, bounds_4326)
    ) AS tile;
  ELSIF z <= 11 THEN
    SELECT COALESCE(ST_AsMVT(tile, 'strategy-recommendations', 4096, 'geom'), ''::bytea) INTO mvt
    FROM (
      SELECT
        cell_id,
        strategy_id,
        strategy_slug,
        strategy_name,
        strategy_category,
        suitability_score,
        effect_utility_score,
        label_release_key,
        source_count,
        label_review_tier,
        ST_AsMVTGeom(ST_Transform(geom, 3857), bounds_3857, 4096, 64, true) AS geom
      FROM geo.mv_strategy_recommendations_regional
      WHERE geom && bounds_4326 AND ST_Intersects(geom, bounds_4326)
    ) AS tile;
  ELSE
    SELECT COALESCE(ST_AsMVT(tile, 'strategy-recommendations', 4096, 'geom'), ''::bytea) INTO mvt
    FROM (
      SELECT
        cell_id,
        strategy_id,
        strategy_slug,
        strategy_name,
        strategy_category,
        suitability_score,
        effect_utility_score,
        label_release_key,
        source_count,
        label_review_tier,
        ST_AsMVTGeom(ST_Transform(geom, 3857), bounds_3857, 4096, 64, true) AS geom
      FROM geo.mv_strategy_recommendations_detail
      WHERE geom && bounds_4326 AND ST_Intersects(geom, bounds_4326)
    ) AS tile;
  END IF;

  RETURN mvt;
END;
$$;


--
-- Name: sync_feature_geom_from_properties(); Type: FUNCTION; Schema: geo; Owner: -
--

CREATE FUNCTION geo.sync_feature_geom_from_properties() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
DECLARE
  parsed geometry;
  repaired geometry;
BEGIN
  IF jsonb_typeof(NEW.properties -> 'geometry') <> 'object' THEN
    NEW.geom := NULL;
    RETURN NEW;
  END IF;

  BEGIN
    parsed := ST_GeomFromGeoJSON((NEW.properties -> 'geometry')::text);
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'geo.features.properties.geometry must be valid GeoJSON: %', SQLERRM
      USING ERRCODE = '22023';
  END;

  IF ST_SRID(parsed) = 0 THEN
    parsed := ST_SetSRID(parsed, 4326);
  END IF;

  IF ST_SRID(parsed) <> 4326 THEN
    RAISE EXCEPTION 'geo.features.properties.geometry must be a valid EPSG:4326 geometry'
      USING ERRCODE = '22023';
  END IF;

  IF NOT ST_IsValid(parsed) THEN
    repaired := ST_MakeValid(parsed);
    IF GeometryType(repaired) = 'GEOMETRYCOLLECTION' THEN
      -- Keep components matching the original topological dimension
      -- (1=point, 2=line, 3=polygon for ST_CollectionExtract).
      repaired := ST_CollectionExtract(repaired, ST_Dimension(parsed) + 1);
    END IF;
    IF repaired IS NULL OR ST_IsEmpty(repaired) OR NOT ST_IsValid(repaired) THEN
      RAISE EXCEPTION 'geo.features.properties.geometry must be a valid EPSG:4326 geometry'
        USING ERRCODE = '22023';
    END IF;
    parsed := repaired;
    -- Web read paths serve properties, not geom: write the repaired geometry
    -- back and flag the repair so callers know the shape changed.
    NEW.properties := jsonb_set(
      jsonb_set(NEW.properties, '{geometry}', ST_AsGeoJSON(parsed)::jsonb),
      '{geometry_repaired}',
      'true'::jsonb
    );
  END IF;

  NEW.geom := parsed;
  RETURN NEW;
END;
$$;


--
-- Name: sync_historical_point_geom(); Type: FUNCTION; Schema: geo; Owner: -
--

CREATE FUNCTION geo.sync_historical_point_geom() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $$
BEGIN
  IF NEW.lat BETWEEN -90 AND 90 AND NEW.lon BETWEEN -180 AND 180 THEN
    NEW.geom := ST_SetSRID(ST_MakePoint(NEW.lon, NEW.lat), 4326);
  ELSE
    NEW.geom := NULL;
  END IF;
  RETURN NEW;
END;
$$;


--
-- Name: watershed_tiles(integer, integer, integer); Type: FUNCTION; Schema: geo; Owner: -
--

CREATE FUNCTION geo.watershed_tiles(z integer, x integer, y integer) RETURNS bytea
    LANGUAGE plpgsql STABLE PARALLEL SAFE
    SET search_path TO 'public', 'pg_catalog'
    AS $$
DECLARE
  bounds_3857 geometry;
  bounds_4326 geometry;
  target_level integer;
  mvt bytea;
BEGIN
  bounds_3857 := ST_TileEnvelope(z, x, y);
  bounds_4326 := ST_Transform(bounds_3857, 4326);

  -- Which rung of the hierarchy this zoom can legibly draw. 12 is the published detail, read
  -- straight from geo.features; everything coarser comes from the rollup.
  target_level := CASE
    WHEN z >= 10 THEN 12
    WHEN z >= 8 THEN 10
    WHEN z >= 6 THEN 8
    WHEN z >= 4 THEN 6
    ELSE 4
  END;

  IF target_level = 12 THEN
    SELECT COALESCE(ST_AsMVT(tile, 'watersheds', 4096, 'geom'), ''::bytea) INTO mvt
    FROM (
      SELECT
        f.id,
        ST_AsMVTGeom(ST_Transform(f.geom, 3857), bounds_3857, 4096, 64, true) AS geom,
        f.properties ->> 'huc12' AS huc12,
        f.properties ->> 'huc12' AS huc,
        12 AS huc_level,
        NULL::integer AS basin_count,
        f.properties ->> 'name' AS name,
        CASE
          WHEN jsonb_typeof(f.properties -> 'areasqkm') = 'number'
            THEN (f.properties ->> 'areasqkm')::double precision
        END AS areasqkm,
        f.properties ->> 'tohuc' AS tohuc,
        f.properties ->> 'states' AS states,
        f.properties ->> 'hutype' AS hutype,
        geo.feature_observation_day(f.properties)::text AS observed_day
      FROM geo.features f
      JOIN geo.layers l ON f.layer_id = l.id
      WHERE l.name = 'watersheds'
        AND l.is_public IS TRUE
        AND f.status = 'published'
        AND f.geom IS NOT NULL
        AND f.geom && bounds_4326
        AND ST_Intersects(f.geom, bounds_4326)
    ) AS tile;
  ELSE
    SELECT COALESCE(ST_AsMVT(tile, 'watersheds', 4096, 'geom'), ''::bytea) INTO mvt
    FROM (
      SELECT
        r.huc AS id,
        ST_AsMVTGeom(ST_Transform(r.geom, 3857), bounds_3857, 4096, 64, true) AS geom,
        NULL::text AS huc12,
        r.huc,
        r.huc_level,
        r.basin_count,
        NULL::text AS name,
        r.areasqkm,
        NULL::text AS tohuc,
        NULL::text AS states,
        NULL::text AS hutype,
        r.observed_day::text AS observed_day
      FROM geo.watershed_rollup r
      WHERE r.huc_level = target_level
        AND r.geom && bounds_4326
        AND ST_Intersects(r.geom, bounds_4326)
    ) AS tile;
  END IF;

  RETURN mvt;
END;
$$;


--
-- Name: sync_position_geom_from_metadata(); Type: FUNCTION; Schema: tracking; Owner: -
--

CREATE FUNCTION tracking.sync_position_geom_from_metadata() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  latitude double precision;
  longitude double precision;
BEGIN
  IF jsonb_typeof(NEW.metadata -> 'lat') = 'number'
    AND jsonb_typeof(NEW.metadata -> 'lon') = 'number' THEN
    latitude := (NEW.metadata ->> 'lat')::double precision;
    longitude := (NEW.metadata ->> 'lon')::double precision;
    IF latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180 THEN
      NEW.geom := ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography;
      RETURN NEW;
    END IF;
  END IF;

  NEW.geom := NULL;
  RETURN NEW;
END;
$$;


--
-- Name: climate_field_observation; Type: VIEW; Schema: geo; Owner: -
--

CREATE VIEW geo.climate_field_observation AS
 SELECT observation.id AS observation_id,
    observation.cell_id,
    cell.cell_key,
    cell.grid_name,
    cell.resolution_m,
    cell.geometry AS cell_geometry,
    cell.centroid AS cell_centroid,
    governed.signal,
    observation.signal_name,
    observation.support_key,
    observation.observed_at,
    ((observation.observed_at AT TIME ZONE 'UTC'::text))::date AS observed_day,
    observation.normalized_value,
    observation.normalized_unit,
    observation.coverage_fraction,
    release.retrieved_at AS release_retrieved_at,
    source.key AS data_source_key,
    source.name AS data_source_name,
    source.license_name,
    source.allowed_client_exposure
   FROM ((((agri.signal_observation observation
     JOIN ( VALUES ('air_temperature_mean'::text,'air-temperature'::text,'C'::text), ('air_temperature_max'::text,'air-temperature'::text,'C'::text), ('air_temperature_min'::text,'air-temperature'::text,'C'::text), ('dew_point_temperature'::text,'dew-point'::text,'C'::text), ('precipitation'::text,'precipitation'::text,'mm/day'::text), ('relative_humidity'::text,'relative-humidity'::text,'%'::text), ('surface_shortwave_radiation'::text,'shortwave-radiation'::text,'MJ/m^2/day'::text), ('wind_speed'::text,'wind-speed'::text,'m/s'::text), ('soil_wetness_surface'::text,'soil-wetness-surface'::text,'fraction_of_saturation'::text), ('soil_wetness_root_zone'::text,'soil-wetness-root-zone'::text,'fraction_of_saturation'::text), ('soil_wetness_profile'::text,'soil-wetness-profile'::text,'fraction_of_saturation'::text)) governed(signal_name, signal, normalized_unit) ON (((governed.signal_name = (observation.signal_name)::text) AND (governed.normalized_unit = (observation.normalized_unit)::text))))
     JOIN agri.spatial_cell cell ON ((cell.id = observation.cell_id)))
     JOIN agri.source_release release ON ((release.id = observation.source_release_id)))
     JOIN agri.data_source source ON ((source.id = release.data_source_id)))
  WHERE (((source.key)::text = 'nasa-power-daily'::text) AND observation.is_observed AND ((observation.quality_flag)::text = 'accepted'::text) AND (observation.normalized_value IS NOT NULL));


--
-- Name: VIEW climate_field_observation; Type: COMMENT; Schema: geo; Owner: -
--

COMMENT ON VIEW geo.climate_field_observation IS 'NASA POWER daily meteorology (air temperature mean/max/min, dew point, precipitation, relative humidity, shortwave radiation, wind speed) and pilot soil wetness (surface, root zone, profile) per 0.5-degree cell per day per signal, with the cell geometry and the data_source row that licences it. Read by getPublishedClimateField at its single serving tier. Carries observation_id and release_retrieved_at because overlapping archive releases leave duplicate (cell, signal, day) keys the reader must resolve.';


--
-- Name: COLUMN climate_field_observation.signal; Type: COMMENT; Schema: geo; Owner: -
--

COMMENT ON COLUMN geo.climate_field_observation.signal IS 'The client-facing signal id from src/lib/environmental/climate-field.ts. The wire never carries a raw signal_name: the client sends this id and the server resolves it.';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: drought_areas; Type: TABLE; Schema: geo; Owner: -
--

CREATE TABLE geo.drought_areas (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    valid_date character varying(10) NOT NULL,
    dm_category integer NOT NULL,
    geom public.geometry(MultiPolygon,4326) NOT NULL,
    source_url text NOT NULL,
    ingested_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT drought_areas_dm_category_range CHECK (((dm_category >= 0) AND (dm_category <= 4))),
    CONSTRAINT drought_areas_valid_date_iso CHECK (((valid_date)::text ~ '^\d{4}-\d{2}-\d{2}$'::text))
);


--
-- Name: features; Type: TABLE; Schema: geo; Owner: -
--

CREATE TABLE geo.features (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    layer_id uuid NOT NULL,
    properties jsonb DEFAULT '{}'::jsonb NOT NULL,
    status character varying(20) DEFAULT 'published'::character varying,
    review_note text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    geom public.geometry(Geometry,4326),
    geometry_id uuid,
    data_available_at timestamp with time zone
);


--
-- Name: geometry; Type: TABLE; Schema: geo; Owner: -
--

CREATE TABLE geo.geometry (
    geometry_id uuid DEFAULT gen_random_uuid() NOT NULL,
    natural_key character varying(255) NOT NULL,
    version_valid_from timestamp with time zone NOT NULL,
    version_valid_to timestamp with time zone,
    geom_kind character varying(16) NOT NULL,
    geom public.geometry(Geometry,4326) NOT NULL,
    centroid public.geometry(Point,4326) NOT NULL,
    grid_name character varying(100),
    cell_key character varying(180),
    resolution_m integer,
    producer character varying(100) NOT NULL,
    superseded_by uuid,
    last_confirmed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_geometry_cell_fields CHECK (((((geom_kind)::text <> 'grid_cell'::text) AND (grid_name IS NULL) AND (cell_key IS NULL) AND (resolution_m IS NULL)) OR (((geom_kind)::text = 'grid_cell'::text) AND (grid_name IS NOT NULL) AND (cell_key IS NOT NULL) AND (resolution_m IS NOT NULL) AND (resolution_m > 0)))),
    CONSTRAINT ck_geometry_kind CHECK (((geom_kind)::text = ANY ((ARRAY['point'::character varying, 'polygon'::character varying, 'line'::character varying, 'grid_cell'::character varying])::text[]))),
    CONSTRAINT ck_geometry_natural_key_namespaced CHECK (((natural_key)::text ~~ ((producer)::text || ':%'::text))),
    CONSTRAINT ck_geometry_supersede CHECK ((((version_valid_to IS NULL) AND (superseded_by IS NULL)) OR ((version_valid_to IS NOT NULL) AND (superseded_by IS NOT NULL)))),
    CONSTRAINT ck_geometry_version_order CHECK (((version_valid_to IS NULL) OR (version_valid_to > version_valid_from)))
);


--
-- Name: geometry_current; Type: VIEW; Schema: geo; Owner: -
--

CREATE VIEW geo.geometry_current AS
 SELECT geometry_id,
    natural_key,
    version_valid_from,
    version_valid_to,
    geom_kind,
    geom,
    centroid,
    grid_name,
    cell_key,
    resolution_m,
    producer,
    superseded_by,
    last_confirmed_at
   FROM geo.geometry
  WHERE (version_valid_to IS NULL);


--
-- Name: historical_fire_data; Type: TABLE; Schema: geo; Owner: -
--

CREATE TABLE geo.historical_fire_data (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    date_bucket timestamp with time zone NOT NULL,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    geom public.geometry(Point,4326),
    fire_risk_score double precision,
    detected_anomalies integer DEFAULT 0,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: historical_vegetation; Type: TABLE; Schema: geo; Owner: -
--

CREATE TABLE geo.historical_vegetation (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    date_bucket timestamp with time zone NOT NULL,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    geom public.geometry(Point,4326),
    ndvi_value double precision,
    ecological_health_index double precision,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: historical_water_drought; Type: TABLE; Schema: geo; Owner: -
--

CREATE TABLE geo.historical_water_drought (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    date_bucket timestamp with time zone NOT NULL,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    geom public.geometry(Point,4326),
    water_scarcity_index double precision,
    streamflow_cfs double precision,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: layers; Type: TABLE; Schema: geo; Owner: -
--

CREATE TABLE geo.layers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    type character varying(50) DEFAULT 'vector'::character varying NOT NULL,
    description text,
    style jsonb DEFAULT '{}'::jsonb,
    is_public boolean DEFAULT false,
    min_zoom integer DEFAULT 0,
    max_zoom integer DEFAULT 22,
    team_id uuid,
    sort_order integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: mv_drought_observation_day; Type: MATERIALIZED VIEW; Schema: geo; Owner: -
--

CREATE MATERIALIZED VIEW geo.mv_drought_observation_day AS
 SELECT 'polygon'::text AS surface_kind,
    'drought-areas'::text AS surface_name,
    (covered.covered_day)::date AS observed_day,
    release.class_count AS observation_count,
    (0)::bigint AS unlinked_count,
    release.class_count AS distinct_key_count,
    release.newest_ingested_at AS newest_observed_at,
    '{}'::jsonb AS metric_counts
   FROM (( SELECT (d.valid_date)::date AS valid_date,
            lead((d.valid_date)::date) OVER (ORDER BY (d.valid_date)::date) AS next_valid_date,
            count(*) AS class_count,
            max(d.ingested_at) AS newest_ingested_at
           FROM geo.drought_areas d
          GROUP BY d.valid_date) release
     CROSS JOIN LATERAL generate_series((release.valid_date)::timestamp without time zone, (LEAST(
        CASE
            WHEN (release.next_valid_date IS NULL) THEN (release.valid_date + 14)
            ELSE (release.valid_date + (7 - 1))
        END,
        CASE
            WHEN (release.next_valid_date IS NULL) THEN ((now() AT TIME ZONE 'UTC'::text))::date
            ELSE (release.next_valid_date - 1)
        END))::timestamp without time zone, '1 day'::interval) covered(covered_day))
  WITH NO DATA;


--
-- Name: MATERIALIZED VIEW mv_drought_observation_day; Type: COMMENT; Schema: geo; Owner: -
--

COMMENT ON MATERIALIZED VIEW geo.mv_drought_observation_day IS 'One row per day a stored USDM release COVERS, under the same bounded carry-forward resolveDroughtRelease applies. Projects valid_date and counts only -- never geom, which hides 495 MB of TOAST behind 1,040 rows. Its live-edge branch reads now(), so its refresh watermark must include the current UTC date or it will be skipped on a day it needed to advance.';


--
-- Name: mv_drought_release_index; Type: MATERIALIZED VIEW; Schema: geo; Owner: -
--

CREATE MATERIALIZED VIEW geo.mv_drought_release_index AS
 WITH release AS (
         SELECT (d.valid_date)::date AS valid_date,
            count(*) AS class_count,
            max(d.ingested_at) AS newest_ingested_at
           FROM geo.drought_areas d
          GROUP BY d.valid_date
        )
 SELECT valid_date,
    lag(valid_date) OVER (ORDER BY valid_date) AS prev_valid_date,
    lead(valid_date) OVER (ORDER BY valid_date) AS next_valid_date,
    class_count,
    min(valid_date) OVER () AS earliest_valid_date,
    max(valid_date) OVER () AS latest_valid_date,
    newest_ingested_at
   FROM release
  ORDER BY valid_date
  WITH NO DATA;


--
-- Name: MATERIALIZED VIEW mv_drought_release_index; Type: COMMENT; Schema: geo; Owner: -
--

COMMENT ON MATERIALIZED VIEW geo.mv_drought_release_index IS 'One row per USDM valid_date with its neighbours and the record bounds, ~208 rows. Two index probes replace a full scan of a table hiding 495 MB of TOAST. Never projects geom.';


--
-- Name: mv_feature_observation_day; Type: MATERIALIZED VIEW; Schema: geo; Owner: -
--

CREATE MATERIALIZED VIEW geo.mv_feature_observation_day AS
 WITH day_total AS (
         SELECT l.name AS surface_name,
            geo.feature_observation_day(f.properties) AS observed_day,
            count(*) FILTER (WHERE (f.geometry_id IS NOT NULL)) AS observation_count,
            count(*) FILTER (WHERE (f.geometry_id IS NULL)) AS unlinked_count,
            count(DISTINCT f.geometry_id) AS distinct_key_count,
            max(
                CASE
                    WHEN pg_input_is_valid(COALESCE((f.properties ->> 'observedAt'::text), (f.properties ->> 'updatedAt'::text), (f.properties ->> 'polygonDateTime'::text)), 'timestamptz'::text) THEN (COALESCE((f.properties ->> 'observedAt'::text), (f.properties ->> 'updatedAt'::text), (f.properties ->> 'polygonDateTime'::text)))::timestamp with time zone
                    ELSE NULL::timestamp with time zone
                END) AS newest_observed_at
           FROM (geo.layers l
             JOIN geo.features f ON ((f.layer_id = l.id)))
          WHERE (((f.status)::text = 'published'::text) AND (geo.feature_observation_day(f.properties) IS NOT NULL) AND (((l.name)::text <> 'fire-perimeters'::text) OR (COALESCE((f.properties ->> 'observedAt'::text), (f.properties ->> 'updatedAt'::text), (f.properties ->> 'polygonDateTime'::text)) IS NOT NULL)))
          GROUP BY l.name, (geo.feature_observation_day(f.properties))
        ), metric_day AS (
         SELECT l.name AS surface_name,
            geo.feature_observation_day(f.properties) AS observed_day,
            metric.metric_key,
            count(*) AS candidate_count,
            count(*) FILTER (WHERE (f.geometry_id IS NULL)) AS unlinked_count
           FROM ((geo.layers l
             JOIN geo.features f ON ((f.layer_id = l.id)))
             JOIN ( VALUES ('water-gauges'::text,'streamflow-cfs'::text,'flowCfs'::text,(- (999999)::double precision),false), ('weather-observations'::text,'temperature'::text,'temperature'::text,NULL::double precision,false), ('weather-observations'::text,'humidity'::text,'humidity'::text,NULL::double precision,false), ('weather-observations'::text,'precipitation'::text,'precipitation'::text,NULL::double precision,false), ('weather-observations'::text,'wind-speed'::text,'windSpeed'::text,NULL::double precision,false), ('weather-observations'::text,'wind-direction'::text,'windDirection'::text,NULL::double precision,false), ('fire-detections'::text,'fire-radiative-power'::text,'frp'::text,(0)::double precision,true), ('fire-detections'::text,'fire-brightness'::text,'brightness'::text,(0)::double precision,false), ('fire-perimeters'::text,'perimeter-acres'::text,'gisAcres'::text,NULL::double precision,false), ('fire-perimeters'::text,'percent-contained'::text,'percentContained'::text,NULL::double precision,false)) metric(surface_name, metric_key, value_key, missing_sentinel, viirs_only) ON ((metric.surface_name = (l.name)::text)))
          WHERE (((f.status)::text = 'published'::text) AND (geo.feature_observation_day(f.properties) IS NOT NULL) AND (((l.name)::text <> 'fire-perimeters'::text) OR (COALESCE((f.properties ->> 'observedAt'::text), (f.properties ->> 'updatedAt'::text), (f.properties ->> 'polygonDateTime'::text)) IS NOT NULL)) AND (jsonb_typeof((f.properties -> metric.value_key)) = 'number'::text) AND ((metric.missing_sentinel IS NULL) OR
                CASE
                    WHEN (jsonb_typeof((f.properties -> metric.value_key)) = 'number'::text) THEN (((f.properties ->> metric.value_key))::double precision <> metric.missing_sentinel)
                    ELSE false
                END) AND ((NOT metric.viirs_only) OR (COALESCE((f.properties ->> 'product'::text), ''::text) !~~ 'MODIS%'::text)))
          GROUP BY l.name, (geo.feature_observation_day(f.properties)), metric.metric_key
        ), metric_counts AS (
         SELECT metric_day.surface_name,
            metric_day.observed_day,
            jsonb_object_agg(metric_day.metric_key, jsonb_build_object('candidate', metric_day.candidate_count, 'unlinked', metric_day.unlinked_count)) AS metric_counts
           FROM metric_day
          GROUP BY metric_day.surface_name, metric_day.observed_day
        )
 SELECT 'feature'::text AS surface_kind,
    day_total.surface_name,
    day_total.observed_day,
    day_total.observation_count,
    day_total.unlinked_count,
    day_total.distinct_key_count,
    day_total.newest_observed_at,
    COALESCE(metric_counts.metric_counts, '{}'::jsonb) AS metric_counts
   FROM (day_total
     LEFT JOIN metric_counts ON ((((metric_counts.surface_name)::text = (day_total.surface_name)::text) AND (metric_counts.observed_day = day_total.observed_day))))
  WITH NO DATA;


--
-- Name: MATERIALIZED VIEW mv_feature_observation_day; Type: COMMENT; Schema: geo; Owner: -
--

COMMENT ON MATERIALIZED VIEW geo.mv_feature_observation_day IS 'One row per (geo.layers.name, publisher-named day) over published geo.features: the day axis, the orphan count, and per-metric candidate/unlinked totals. Retires readObservationWindows and the unbounded half of getMetricAtDate. metric_counts is valid ONLY for an unbboxed request -- this relation is grained on (surface, day) and carries no geometry, so a viewport-scoped metric read must still count its own candidates.';


--
-- Name: mv_layer_feature_stats; Type: MATERIALIZED VIEW; Schema: geo; Owner: -
--

CREATE MATERIALIZED VIEW geo.mv_layer_feature_stats AS
 SELECT l.id AS layer_id,
    l.name AS layer_name,
    count(f.id) FILTER (WHERE ((f.status)::text = 'published'::text)) AS published_count,
    max(f.created_at) AS newest_created_at,
    max(f.updated_at) AS newest_updated_at
   FROM (geo.layers l
     LEFT JOIN geo.features f ON ((f.layer_id = l.id)))
  GROUP BY l.id, l.name
  ORDER BY l.id
  WITH NO DATA;


--
-- Name: MATERIALIZED VIEW mv_layer_feature_stats; Type: COMMENT; Schema: geo; Owner: -
--

COMMENT ON MATERIALIZED VIEW geo.mv_layer_feature_stats IS 'Published feature count and newest write time per layer, 11 rows. Retires getFeatureCountByLayer, getSystemStats and layerStats. LEFT JOIN so an empty layer still has a row and is reported as empty rather than as absent.';


--
-- Name: mv_layer_hourly_activity; Type: MATERIALIZED VIEW; Schema: geo; Owner: -
--

CREATE MATERIALIZED VIEW geo.mv_layer_hourly_activity AS
 SELECT l.id AS layer_id,
    date_trunc('hour'::text, f.created_at) AS hour_bucket,
    count(*) AS feature_count
   FROM (geo.layers l
     JOIN geo.features f ON ((f.layer_id = l.id)))
  WHERE (((f.status)::text = 'published'::text) AND (f.created_at >= (date_trunc('hour'::text, now()) - '168:00:00'::interval)))
  GROUP BY l.id, (date_trunc('hour'::text, f.created_at))
  ORDER BY l.id, (date_trunc('hour'::text, f.created_at))
  WITH NO DATA;


--
-- Name: MATERIALIZED VIEW mv_layer_hourly_activity; Type: COMMENT; Schema: geo; Owner: -
--

COMMENT ON MATERIALIZED VIEW geo.mv_layer_hourly_activity IS 'Published features created per layer per hour over the trailing 168 hours -- the exact ceiling the analytics router enforces, so every accepted N is a bounded SUM. Window is relative to now(), so its refresh watermark must include the current hour.';


--
-- Name: soil_field_observation; Type: VIEW; Schema: geo; Owner: -
--

CREATE VIEW geo.soil_field_observation AS
 SELECT observation.cell_id,
    cell.cell_key,
    cell.grid_name,
    cell.resolution_m,
    cell.geometry AS cell_geometry,
    cell.centroid AS cell_centroid,
    governed.measure,
    observation.signal_name,
    observation.support_key,
    observation.observed_at,
    ((observation.observed_at AT TIME ZONE 'UTC'::text))::date AS observed_day,
    observation.normalized_value,
    observation.normalized_unit,
    observation.coverage_fraction,
    source.key AS data_source_key,
    source.name AS data_source_name,
    source.license_name,
    source.allowed_client_exposure
   FROM ((((agri.signal_observation observation
     JOIN ( VALUES ('soil_water_content_layer_1'::text,'moisture'::text,'m^3/m^3'::text), ('soil_water_content_layer_2'::text,'moisture'::text,'m^3/m^3'::text), ('soil_water_content_layer_3'::text,'moisture'::text,'m^3/m^3'::text), ('soil_temperature_level_1'::text,'temperature'::text,'C'::text), ('soil_temperature_level_2'::text,'temperature'::text,'C'::text), ('soil_temperature_level_3'::text,'temperature'::text,'C'::text), ('soil_temperature_level_4'::text,'temperature'::text,'C'::text), ('vapor_pressure_deficit'::text,'vpd'::text,'kPa'::text)) governed(signal_name, measure, normalized_unit) ON (((governed.signal_name = (observation.signal_name)::text) AND (governed.normalized_unit = (observation.normalized_unit)::text))))
     JOIN agri.spatial_cell cell ON ((cell.id = observation.cell_id)))
     JOIN agri.source_release release ON ((release.id = observation.source_release_id)))
     JOIN agri.data_source source ON ((source.id = release.data_source_id)))
  WHERE (observation.is_observed AND ((observation.quality_flag)::text = 'accepted'::text) AND (observation.normalized_value IS NOT NULL));


--
-- Name: VIEW soil_field_observation; Type: COMMENT; Schema: geo; Owner: -
--

COMMENT ON VIEW geo.soil_field_observation IS 'ERA5-Land volumetric soil water (m^3/m^3), soil temperature (C) and daily-max vapor pressure deficit (kPa) per 0.25-degree cell per day per signal, with the cell geometry and the data_source row that licences it. Read by getPublishedSoilField at the detail zoom tier.';


--
-- Name: mv_signal_observation_day; Type: MATERIALIZED VIEW; Schema: geo; Owner: -
--

CREATE MATERIALIZED VIEW geo.mv_signal_observation_day AS
 SELECT 'signal'::text AS surface_kind,
    stream.layer_name AS surface_name,
    soil.observed_day,
    count(*) AS observation_count,
    (0)::bigint AS unlinked_count,
    count(DISTINCT soil.cell_id) AS distinct_key_count,
    max(soil.observed_at) AS newest_observed_at,
    '{}'::jsonb AS metric_counts
   FROM (geo.soil_field_observation soil
     JOIN ( VALUES ('moisture'::text,'soil-field-moisture'::text), ('temperature'::text,'soil-field-temperature'::text), ('vpd'::text,'soil-field-vpd'::text)) stream(measure, layer_name) ON ((stream.measure = soil.measure)))
  WHERE ((soil.support_key)::text = 'era5-land-0.1deg'::text)
  GROUP BY stream.layer_name, soil.observed_day
UNION ALL
 SELECT 'signal'::text AS surface_kind,
    stream.layer_name AS surface_name,
    climate.observed_day,
    count(*) AS observation_count,
    (0)::bigint AS unlinked_count,
    count(DISTINCT climate.cell_id) AS distinct_key_count,
    max(climate.observed_at) AS newest_observed_at,
    '{}'::jsonb AS metric_counts
   FROM (geo.climate_field_observation climate
     JOIN ( VALUES ('air-temperature'::text,'climate-field-air-temperature'::text), ('dew-point'::text,'climate-field-dew-point'::text), ('precipitation'::text,'climate-field-precipitation'::text), ('relative-humidity'::text,'climate-field-relative-humidity'::text), ('shortwave-radiation'::text,'climate-field-shortwave-radiation'::text), ('wind-speed'::text,'climate-field-wind-speed'::text), ('soil-wetness-surface'::text,'climate-field-soil-wetness-surface'::text), ('soil-wetness-root-zone'::text,'climate-field-soil-wetness-root-zone'::text), ('soil-wetness-profile'::text,'climate-field-soil-wetness-profile'::text)) stream(signal, layer_name) ON ((stream.signal = climate.signal)))
  WHERE ((climate.support_key)::text = 'surface'::text)
  GROUP BY stream.layer_name, climate.observed_day
  WITH NO DATA;


--
-- Name: MATERIALIZED VIEW mv_signal_observation_day; Type: COMMENT; Schema: geo; Owner: -
--

COMMENT ON MATERIALIZED VIEW geo.mv_signal_observation_day IS 'One row per (stream name, day) for the twelve signal-backed slider streams -- three ERA5-Land soil measures and nine NASA POWER climate signals -- grouped from the two governed views. This is the relation that retires the ~17M-row whole-table pass that returned a Cloudflare 524 on 2026-08-15 and took every slider down at once.';


--
-- Name: mv_soil_survey_grid; Type: MATERIALIZED VIEW; Schema: geo; Owner: -
--

CREATE MATERIALIZED VIEW geo.mv_soil_survey_grid AS
 WITH delineation AS (
         SELECT COALESCE((f.properties ->> 'drainageClass'::text), 'unknown'::text) AS drainage_class,
                CASE
                    WHEN (jsonb_typeof((f.properties -> 'hydric'::text)) = 'boolean'::text) THEN ((f.properties ->> 'hydric'::text))::boolean
                    ELSE NULL::boolean
                END AS hydric,
            COALESCE(g.centroid, public.st_centroid(f.geom)) AS centroid
           FROM ((geo.features f
             JOIN geo.layers l ON ((l.id = f.layer_id)))
             LEFT JOIN geo.geometry g ON ((g.geometry_id = f.geometry_id)))
          WHERE (((l.name)::text = 'soil-survey'::text) AND ((f.status)::text = 'published'::text) AND (f.geom IS NOT NULL))
        ), located AS (
         SELECT delineation.drainage_class,
            delineation.hydric,
            delineation.centroid
           FROM delineation
          WHERE ((delineation.centroid IS NOT NULL) AND (NOT public.st_isempty(delineation.centroid)))
        ), tier AS (
         SELECT k.k AS zoom_tier,
            ((0.125)::double precision * power((2)::double precision, (k.k)::double precision)) AS cell_degrees
           FROM generate_series(0, 12) k(k)
        )
 SELECT tier.zoom_tier,
    tier.cell_degrees,
    (floor((public.st_x(located.centroid) / tier.cell_degrees)))::bigint AS cell_col,
    (floor((public.st_y(located.centroid) / tier.cell_degrees)))::bigint AS cell_row,
    mode() WITHIN GROUP (ORDER BY located.drainage_class) AS drainage_class,
    count(*) AS map_unit_count,
    count(*) FILTER (WHERE located.hydric) AS hydric_count,
    count(*) FILTER (WHERE (located.hydric IS NOT NULL)) AS rated_count
   FROM (located
     CROSS JOIN tier)
  GROUP BY tier.zoom_tier, tier.cell_degrees, (floor((public.st_x(located.centroid) / tier.cell_degrees))), (floor((public.st_y(located.centroid) / tier.cell_degrees)))
  ORDER BY tier.zoom_tier, (floor((public.st_x(located.centroid) / tier.cell_degrees))), (floor((public.st_y(located.centroid) / tier.cell_degrees)))
  WITH NO DATA;


--
-- Name: MATERIALIZED VIEW mv_soil_survey_grid; Type: COMMENT; Schema: geo; Owner: -
--

COMMENT ON MATERIALIZED VIEW geo.mv_soil_survey_grid IS 'SSURGO delineation counts per lattice cell at 13 power-of-two zoom tiers (zoom_tier = k where step = 0.125 * 2^k, the ladder soilSummaryCellDegrees walks). NOT YET READ BY ANYTHING: readSummaryFeatures still counts its own input, because soilSummaryCellDegrees picks its step off an UNBOUNDED doubling ladder and returns it to the client as cellDegrees, so repointing it requires pinning that ladder to this enumerated tier set first. Built here so the reader change is a read-path edit rather than a migration; until that reader lands this relation is cost with no benefit, and it should be dropped rather than kept if the reader is abandoned.';


--
-- Name: mv_soil_survey_union; Type: MATERIALIZED VIEW; Schema: geo; Owner: -
--

CREATE MATERIALIZED VIEW geo.mv_soil_survey_union AS
 WITH delineation AS (
         SELECT COALESCE((f.properties ->> 'drainageClass'::text), 'unknown'::text) AS drainage_class,
                CASE
                    WHEN (jsonb_typeof((f.properties -> 'hydric'::text)) = 'boolean'::text) THEN ((f.properties ->> 'hydric'::text))::boolean
                    ELSE NULL::boolean
                END AS hydric,
            public.st_makevalid(public.st_snaptogrid(f.geom, (0.000001)::double precision)) AS geom
           FROM (geo.features f
             JOIN geo.layers l ON ((l.id = f.layer_id)))
          WHERE (((l.name)::text = 'soil-survey'::text) AND ((f.status)::text = 'published'::text) AND (f.geom IS NOT NULL))
        ), repaired AS (
         SELECT delineation.drainage_class,
            delineation.hydric,
            delineation.geom
           FROM delineation
          WHERE ((delineation.geom IS NOT NULL) AND (NOT public.st_isempty(delineation.geom)))
        ), tier AS (
         SELECT t.zoom_tier,
            t.simplify_tolerance_degrees
           FROM ( VALUES ('regional-average'::text,(0.0015)::double precision), ('coarse-average'::text,0.005)) t(zoom_tier, simplify_tolerance_degrees)
        ), tiled AS (
         SELECT tier.zoom_tier,
            tier.simplify_tolerance_degrees,
            repaired.drainage_class,
            (floor(public.st_x(public.st_centroid(repaired.geom))))::integer AS tile_x,
            (floor(public.st_y(public.st_centroid(repaired.geom))))::integer AS tile_y,
            public.st_collectionextract(public.st_makevalid(public.st_union(public.st_simplifypreservetopology(repaired.geom, tier.simplify_tolerance_degrees))), 3) AS geom,
            count(*) AS map_unit_count,
            count(*) FILTER (WHERE repaired.hydric) AS hydric_count,
            count(*) FILTER (WHERE (repaired.hydric IS NOT NULL)) AS rated_count
           FROM (repaired
             CROSS JOIN tier)
          GROUP BY tier.zoom_tier, tier.simplify_tolerance_degrees, repaired.drainage_class, (floor(public.st_x(public.st_centroid(repaired.geom)))), (floor(public.st_y(public.st_centroid(repaired.geom))))
        )
 SELECT zoom_tier,
    simplify_tolerance_degrees,
    drainage_class,
    public.st_collectionextract(public.st_makevalid(public.st_union(geom)), 3) AS geom,
    (sum(map_unit_count))::bigint AS map_unit_count,
    (sum(hydric_count))::bigint AS hydric_count,
    (sum(rated_count))::bigint AS rated_count
   FROM tiled
  GROUP BY zoom_tier, simplify_tolerance_degrees, drainage_class
  ORDER BY zoom_tier, drainage_class
  WITH NO DATA;


--
-- Name: MATERIALIZED VIEW mv_soil_survey_union; Type: COMMENT; Schema: geo; Owner: -
--

COMMENT ON MATERIALIZED VIEW geo.mv_soil_survey_union IS 'Dissolved SSURGO drainage-class shapes at the two averaged zoom tiers. NOT YET READ BY ANYTHING: readAggregatedFeatures unions the requesting bbox at a caller-chosen tolerance, and a global dissolve would have to be ST_Intersection''d back -- strictly worse -- so repointing it is a redesign, not an edit. Until that reader lands this relation is cost with no benefit. Built hierarchically -- per one-degree tile, then across tiles -- so the refresh peak stays inside a 3 GB cgroup. Each row is one large TOASTed geometry: clip with && before ST_Intersection, never project geom for an unbounded viewport.';


--
-- Name: v_strategy_recommendation_cells; Type: VIEW; Schema: geo; Owner: -
--

CREATE VIEW geo.v_strategy_recommendation_cells AS
 WITH subject_grid AS (
         SELECT DISTINCT ON (subj.id) subj.id AS analysis_subject_id,
            cell_1.grid_name,
            cell_1.resolution_m
           FROM (agri.analysis_subject subj
             JOIN agri.spatial_cell cell_1 ON (((cell_1.geometry OPERATOR(public.&&) subj.geometry) AND public.st_intersects(cell_1.geometry, subj.geometry))))
          ORDER BY subj.id, cell_1.resolution_m, cell_1.grid_name
        )
 SELECT candidate.strategy_id,
    receipt.id AS selection_receipt_id,
    cell.cell_key,
    cell.grid_name,
    cell.geometry AS cell_geom,
    cell.centroid AS cell_centroid,
    candidate.conservative_score,
    candidate.expected_effect,
    candidate.effect_lower_bound,
    candidate.effect_upper_bound,
    label_release.release_key AS label_release_key,
    label_release.status AS label_release_status
   FROM ((((((agri.strategy_selection_candidate candidate
     JOIN agri.strategy_selection_receipt receipt ON ((receipt.id = candidate.selection_receipt_id)))
     JOIN agri.analysis_subject subject ON ((subject.id = receipt.analysis_subject_id)))
     JOIN subject_grid ON ((subject_grid.analysis_subject_id = subject.id)))
     JOIN agri.spatial_cell cell ON ((((cell.grid_name)::text = (subject_grid.grid_name)::text) AND (cell.resolution_m = subject_grid.resolution_m) AND (cell.geometry OPERATOR(public.&&) subject.geometry) AND public.st_intersects(cell.geometry, subject.geometry))))
     LEFT JOIN agri.forecast_training_run training_run ON ((training_run.id = receipt.training_run_id)))
     LEFT JOIN agri.strategy_label_release label_release ON ((label_release.id = training_run.strategy_label_release_id)))
  WHERE (((candidate.eligibility_state)::text = 'eligible'::text) AND ((receipt.status)::text = 'finalized'::text) AND ((receipt.decision_state)::text = 'ranked'::text) AND ((receipt.audit_state)::text = 'clear'::text));


--
-- Name: mv_strategy_recommendations_coarse; Type: MATERIALIZED VIEW; Schema: geo; Owner: -
--

CREATE MATERIALIZED VIEW geo.mv_strategy_recommendations_coarse AS
 SELECT strategy.id AS strategy_id,
    strategy.slug AS strategy_slug,
    strategy.name AS strategy_name,
    strategy.category AS strategy_category,
    bucket.cell_id,
    bucket.suitability_score,
    bucket.effect_utility_score,
    bucket.effect_utility_lower,
    bucket.effect_utility_upper,
    bucket.label_release_key,
    bucket.source_count,
    bucket.label_review_tier,
    bucket.geom
   FROM (( SELECT located.strategy_id,
            ((((('coarse:'::text || (located.grid_name)::text) || ':'::text) || (floor(public.st_x(located.cell_centroid)))::bigint) || ':'::text) || (floor(public.st_y(located.cell_centroid)))::bigint) AS cell_id,
            avg(located.conservative_score) AS suitability_score,
            avg(located.expected_effect) AS effect_utility_score,
            min(located.effect_lower_bound) AS effect_utility_lower,
            max(located.effect_upper_bound) AS effect_utility_upper,
            (count(DISTINCT located.selection_receipt_id))::integer AS source_count,
                CASE
                    WHEN ((count(DISTINCT located.label_release_key) = 1) AND (count(*) FILTER (WHERE (located.label_release_key IS NULL)) = 0)) THEN min((located.label_release_key)::text)
                    ELSE NULL::text
                END AS label_release_key,
                CASE
                    WHEN (count(located.label_release_key) = 0) THEN 'no_label_release_bound'::text
                    WHEN ((count(DISTINCT located.label_release_key) > 1) OR (count(*) FILTER (WHERE (located.label_release_key IS NULL)) > 0)) THEN 'mixed_label_releases'::text
                    WHEN (min((located.label_release_status)::text) = 'validated'::text) THEN 'label_release_validated_owner_signature_unverified'::text
                    ELSE ('label_release_'::text || min((located.label_release_status)::text))
                END AS label_review_tier,
            public.st_collectionextract(public.st_makevalid(public.st_simplifypreservetopology(public.st_union(located.cell_geom), (0.01)::double precision)), 3) AS geom
           FROM geo.v_strategy_recommendation_cells located
          GROUP BY located.strategy_id, located.grid_name, ((floor(public.st_x(located.cell_centroid)))::bigint), ((floor(public.st_y(located.cell_centroid)))::bigint)) bucket
     JOIN agri.strategies strategy ON ((strategy.id = bucket.strategy_id)))
  WHERE ((bucket.geom IS NOT NULL) AND (NOT public.st_isempty(bucket.geom)))
  WITH NO DATA;


--
-- Name: mv_strategy_recommendations_detail; Type: MATERIALIZED VIEW; Schema: geo; Owner: -
--

CREATE MATERIALIZED VIEW geo.mv_strategy_recommendations_detail AS
 SELECT strategy.id AS strategy_id,
    strategy.slug AS strategy_slug,
    strategy.name AS strategy_name,
    strategy.category AS strategy_category,
    bucket.cell_id,
    bucket.suitability_score,
    bucket.effect_utility_score,
    bucket.effect_utility_lower,
    bucket.effect_utility_upper,
    bucket.label_release_key,
    bucket.source_count,
    bucket.label_review_tier,
    bucket.geom
   FROM (( SELECT located.strategy_id,
            ('detail:'::text || (located.cell_key)::text) AS cell_id,
            avg(located.conservative_score) AS suitability_score,
            avg(located.expected_effect) AS effect_utility_score,
            min(located.effect_lower_bound) AS effect_utility_lower,
            max(located.effect_upper_bound) AS effect_utility_upper,
            (count(DISTINCT located.selection_receipt_id))::integer AS source_count,
                CASE
                    WHEN ((count(DISTINCT located.label_release_key) = 1) AND (count(*) FILTER (WHERE (located.label_release_key IS NULL)) = 0)) THEN min((located.label_release_key)::text)
                    ELSE NULL::text
                END AS label_release_key,
                CASE
                    WHEN (count(located.label_release_key) = 0) THEN 'no_label_release_bound'::text
                    WHEN ((count(DISTINCT located.label_release_key) > 1) OR (count(*) FILTER (WHERE (located.label_release_key IS NULL)) > 0)) THEN 'mixed_label_releases'::text
                    WHEN (min((located.label_release_status)::text) = 'validated'::text) THEN 'label_release_validated_owner_signature_unverified'::text
                    ELSE ('label_release_'::text || min((located.label_release_status)::text))
                END AS label_review_tier,
            public.st_collectionextract(public.st_makevalid(public.st_union(located.cell_geom)), 3) AS geom
           FROM geo.v_strategy_recommendation_cells located
          GROUP BY located.strategy_id, located.cell_key) bucket
     JOIN agri.strategies strategy ON ((strategy.id = bucket.strategy_id)))
  WHERE ((bucket.geom IS NOT NULL) AND (NOT public.st_isempty(bucket.geom)))
  WITH NO DATA;


--
-- Name: mv_strategy_recommendations_regional; Type: MATERIALIZED VIEW; Schema: geo; Owner: -
--

CREATE MATERIALIZED VIEW geo.mv_strategy_recommendations_regional AS
 SELECT strategy.id AS strategy_id,
    strategy.slug AS strategy_slug,
    strategy.name AS strategy_name,
    strategy.category AS strategy_category,
    bucket.cell_id,
    bucket.suitability_score,
    bucket.effect_utility_score,
    bucket.effect_utility_lower,
    bucket.effect_utility_upper,
    bucket.label_release_key,
    bucket.source_count,
    bucket.label_review_tier,
    bucket.geom
   FROM (( SELECT located.strategy_id,
            ((((('regional:'::text || (located.grid_name)::text) || ':'::text) || (floor((public.st_x(located.cell_centroid) / (0.25)::double precision)))::bigint) || ':'::text) || (floor((public.st_y(located.cell_centroid) / (0.25)::double precision)))::bigint) AS cell_id,
            avg(located.conservative_score) AS suitability_score,
            avg(located.expected_effect) AS effect_utility_score,
            min(located.effect_lower_bound) AS effect_utility_lower,
            max(located.effect_upper_bound) AS effect_utility_upper,
            (count(DISTINCT located.selection_receipt_id))::integer AS source_count,
                CASE
                    WHEN ((count(DISTINCT located.label_release_key) = 1) AND (count(*) FILTER (WHERE (located.label_release_key IS NULL)) = 0)) THEN min((located.label_release_key)::text)
                    ELSE NULL::text
                END AS label_release_key,
                CASE
                    WHEN (count(located.label_release_key) = 0) THEN 'no_label_release_bound'::text
                    WHEN ((count(DISTINCT located.label_release_key) > 1) OR (count(*) FILTER (WHERE (located.label_release_key IS NULL)) > 0)) THEN 'mixed_label_releases'::text
                    WHEN (min((located.label_release_status)::text) = 'validated'::text) THEN 'label_release_validated_owner_signature_unverified'::text
                    ELSE ('label_release_'::text || min((located.label_release_status)::text))
                END AS label_review_tier,
            public.st_collectionextract(public.st_makevalid(public.st_simplifypreservetopology(public.st_union(located.cell_geom), (0.002)::double precision)), 3) AS geom
           FROM geo.v_strategy_recommendation_cells located
          GROUP BY located.strategy_id, located.grid_name, ((floor((public.st_x(located.cell_centroid) / (0.25)::double precision)))::bigint), ((floor((public.st_y(located.cell_centroid) / (0.25)::double precision)))::bigint)) bucket
     JOIN agri.strategies strategy ON ((strategy.id = bucket.strategy_id)))
  WHERE ((bucket.geom IS NOT NULL) AND (NOT public.st_isempty(bucket.geom)))
  WITH NO DATA;


--
-- Name: osm_buildings; Type: TABLE; Schema: geo; Owner: -
--

CREATE TABLE geo.osm_buildings (
    id bigint NOT NULL,
    geom public.geometry(Geometry,4326) NOT NULL,
    name text,
    building_type text,
    height real,
    levels integer,
    tags jsonb DEFAULT '{}'::jsonb,
    is_public boolean DEFAULT true NOT NULL
);


--
-- Name: osm_landuse; Type: TABLE; Schema: geo; Owner: -
--

CREATE TABLE geo.osm_landuse (
    id bigint NOT NULL,
    geom public.geometry(Geometry,4326) NOT NULL,
    name text,
    landuse text,
    leisure text,
    "natural" text,
    tags jsonb DEFAULT '{}'::jsonb
);


--
-- Name: osm_pois; Type: TABLE; Schema: geo; Owner: -
--

CREATE TABLE geo.osm_pois (
    id bigint NOT NULL,
    geom public.geometry(Point,4326) NOT NULL,
    name text,
    amenity text,
    shop text,
    tourism text,
    tags jsonb DEFAULT '{}'::jsonb
);


--
-- Name: osm_roads; Type: TABLE; Schema: geo; Owner: -
--

CREATE TABLE geo.osm_roads (
    id bigint NOT NULL,
    geom public.geometry(LineString,4326) NOT NULL,
    name text,
    highway text NOT NULL,
    surface text,
    oneway boolean DEFAULT false,
    lanes integer,
    maxspeed integer,
    tags jsonb DEFAULT '{}'::jsonb
);


--
-- Name: osm_waterways; Type: TABLE; Schema: geo; Owner: -
--

CREATE TABLE geo.osm_waterways (
    id bigint NOT NULL,
    geom public.geometry(Geometry,4326) NOT NULL,
    name text,
    waterway text NOT NULL,
    tags jsonb DEFAULT '{}'::jsonb
);


--
-- Name: poi; Type: TABLE; Schema: geo; Owner: -
--

CREATE TABLE geo.poi (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    category character varying(50),
    subcategory character varying(50),
    address text,
    phone character varying(30),
    website text,
    hours jsonb DEFAULT '{}'::jsonb,
    tags jsonb DEFAULT '{}'::jsonb,
    osm_id integer,
    created_at timestamp with time zone DEFAULT now(),
    geom public.geometry(Point,4326)
);


--
-- Name: raster_release; Type: TABLE; Schema: geo; Owner: -
--

CREATE TABLE geo.raster_release (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    collection text NOT NULL,
    property text NOT NULL,
    depth text NOT NULL,
    statistic text NOT NULL,
    source_name text NOT NULL,
    source_release text NOT NULL,
    source_url text NOT NULL,
    license_name text NOT NULL,
    attribution text NOT NULL,
    unit text NOT NULL,
    scale_divisor integer NOT NULL,
    nodata_value double precision,
    value_min double precision,
    value_max double precision,
    color_ramp jsonb NOT NULL,
    object_key text NOT NULL,
    archive_format text NOT NULL,
    checksum_sha256 text NOT NULL,
    size_bytes bigint NOT NULL,
    min_zoom integer NOT NULL,
    max_zoom integer NOT NULL,
    bounds public.geometry(Polygon,4326) NOT NULL,
    published_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    CONSTRAINT raster_release_checksum_shape CHECK ((checksum_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT raster_release_scale_positive CHECK ((scale_divisor > 0)),
    CONSTRAINT raster_release_zoom_range CHECK (((min_zoom >= 0) AND (max_zoom >= min_zoom)))
);


--
-- Name: TABLE raster_release; Type: COMMENT; Schema: geo; Owner: -
--

COMMENT ON TABLE geo.raster_release IS 'Append-only catalog of first-party raster tile archives on R2. A row is the assertion that a specific set of bytes, with a known checksum and licence, is being served as a map layer. Superseding a release stamps superseded_at and inserts a new row.';


--
-- Name: published_raster; Type: VIEW; Schema: geo; Owner: -
--

CREATE VIEW geo.published_raster AS
 SELECT id,
    collection,
    property,
    depth,
    statistic,
    source_name,
    source_release,
    source_url,
    license_name,
    attribution,
    unit,
    scale_divisor,
    nodata_value,
    value_min,
    value_max,
    color_ramp,
    object_key,
    archive_format,
    checksum_sha256,
    size_bytes,
    min_zoom,
    max_zoom,
    public.st_xmin((bounds)::public.box3d) AS bbox_west,
    public.st_ymin((bounds)::public.box3d) AS bbox_south,
    public.st_xmax((bounds)::public.box3d) AS bbox_east,
    public.st_ymax((bounds)::public.box3d) AS bbox_north,
    published_at
   FROM geo.raster_release release
  WHERE (superseded_at IS NULL);


--
-- Name: VIEW published_raster; Type: COMMENT; Schema: geo; Owner: -
--

COMMENT ON VIEW geo.published_raster IS 'Live raster releases with the bbox flattened to four ordinates. Read by getPublishedRasters; the only supported way to ask what raster layers exist.';


--
-- Name: soil_survey_coverage; Type: TABLE; Schema: geo; Owner: -
--

CREATE TABLE geo.soil_survey_coverage (
    cell_key character varying(40) NOT NULL,
    west double precision NOT NULL,
    south double precision NOT NULL,
    east double precision NOT NULL,
    north double precision NOT NULL,
    polygon_count integer NOT NULL,
    unreadable_count integer DEFAULT 0 NOT NULL,
    truncated boolean DEFAULT false NOT NULL,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_soil_survey_coverage_bounds CHECK (((west < east) AND (south < north))),
    CONSTRAINT ck_soil_survey_coverage_counts CHECK (((polygon_count >= 0) AND (unreadable_count >= 0)))
);


--
-- Name: v_observation_day_census; Type: VIEW; Schema: geo; Owner: -
--

CREATE VIEW geo.v_observation_day_census AS
 SELECT mv_feature_observation_day.surface_kind,
    mv_feature_observation_day.surface_name,
    mv_feature_observation_day.observed_day,
    mv_feature_observation_day.observation_count,
    mv_feature_observation_day.unlinked_count,
    mv_feature_observation_day.distinct_key_count,
    mv_feature_observation_day.newest_observed_at,
    mv_feature_observation_day.metric_counts
   FROM geo.mv_feature_observation_day
UNION ALL
 SELECT mv_signal_observation_day.surface_kind,
    mv_signal_observation_day.surface_name,
    mv_signal_observation_day.observed_day,
    mv_signal_observation_day.observation_count,
    mv_signal_observation_day.unlinked_count,
    mv_signal_observation_day.distinct_key_count,
    mv_signal_observation_day.newest_observed_at,
    mv_signal_observation_day.metric_counts
   FROM geo.mv_signal_observation_day
UNION ALL
 SELECT mv_drought_observation_day.surface_kind,
    mv_drought_observation_day.surface_name,
    mv_drought_observation_day.observed_day,
    mv_drought_observation_day.observation_count,
    mv_drought_observation_day.unlinked_count,
    mv_drought_observation_day.distinct_key_count,
    mv_drought_observation_day.newest_observed_at,
    mv_drought_observation_day.metric_counts
   FROM geo.mv_drought_observation_day;


--
-- Name: VIEW v_observation_day_census; Type: COMMENT; Schema: geo; Owner: -
--

COMMENT ON VIEW geo.v_observation_day_census IS 'The 24-surface slider day axis in one relation: 11 geo.layers names, 12 signal streams and drought-areas. The OUTER relation of the section-9 LEFT JOIN stays in TypeScript; this is the inner one. A surface_name this view cannot emit is silently dropped from the capability payload -- tiles render, history reports zero, no slider mounts.';


--
-- Name: watershed_rollup; Type: MATERIALIZED VIEW; Schema: geo; Owner: -
--

CREATE MATERIALIZED VIEW geo.watershed_rollup AS
 WITH detail AS (
         SELECT (f.properties ->> 'huc12'::text) AS huc,
            public.st_collectionextract(public.st_makevalid(public.st_snaptogrid(f.geom, (0.000001)::double precision)), 3) AS geom,
                CASE
                    WHEN (jsonb_typeof((f.properties -> 'areasqkm'::text)) = 'number'::text) THEN ((f.properties ->> 'areasqkm'::text))::double precision
                    ELSE NULL::double precision
                END AS areasqkm,
            geo.feature_observation_day(f.properties) AS observed_day
           FROM (geo.features f
             JOIN geo.layers l ON ((f.layer_id = l.id)))
          WHERE (((l.name)::text = 'watersheds'::text) AND (l.is_public IS TRUE) AND ((f.status)::text = 'published'::text) AND (f.geom IS NOT NULL) AND ((f.properties ->> 'huc12'::text) ~ '^[0-9]{12}$'::text))
        ), level_10 AS (
         SELECT 10 AS huc_level,
            "left"(detail.huc, 10) AS huc,
            public.st_makevalid(public.st_simplifypreservetopology(public.st_union(detail.geom), (0.0015)::double precision)) AS geom,
            sum(detail.areasqkm) AS areasqkm,
            (count(*))::integer AS basin_count,
            max(detail.observed_day) AS observed_day
           FROM detail
          GROUP BY ("left"(detail.huc, 10))
        ), level_8 AS (
         SELECT 8 AS huc_level,
            "left"(level_10.huc, 8) AS huc,
            public.st_makevalid(public.st_simplifypreservetopology(public.st_union(level_10.geom), (0.005)::double precision)) AS geom,
            sum(level_10.areasqkm) AS areasqkm,
            (sum(level_10.basin_count))::integer AS basin_count,
            max(level_10.observed_day) AS observed_day
           FROM level_10
          GROUP BY ("left"(level_10.huc, 8))
        ), level_6 AS (
         SELECT 6 AS huc_level,
            "left"(level_8.huc, 6) AS huc,
            public.st_makevalid(public.st_simplifypreservetopology(public.st_union(level_8.geom), (0.015)::double precision)) AS geom,
            sum(level_8.areasqkm) AS areasqkm,
            (sum(level_8.basin_count))::integer AS basin_count,
            max(level_8.observed_day) AS observed_day
           FROM level_8
          GROUP BY ("left"(level_8.huc, 6))
        ), level_4 AS (
         SELECT 4 AS huc_level,
            "left"(level_6.huc, 4) AS huc,
            public.st_makevalid(public.st_simplifypreservetopology(public.st_union(level_6.geom), (0.04)::double precision)) AS geom,
            sum(level_6.areasqkm) AS areasqkm,
            (sum(level_6.basin_count))::integer AS basin_count,
            max(level_6.observed_day) AS observed_day
           FROM level_6
          GROUP BY ("left"(level_6.huc, 4))
        )
 SELECT level_10.huc_level,
    level_10.huc,
    level_10.geom,
    level_10.areasqkm,
    level_10.basin_count,
    level_10.observed_day
   FROM level_10
UNION ALL
 SELECT level_8.huc_level,
    level_8.huc,
    level_8.geom,
    level_8.areasqkm,
    level_8.basin_count,
    level_8.observed_day
   FROM level_8
UNION ALL
 SELECT level_6.huc_level,
    level_6.huc,
    level_6.geom,
    level_6.areasqkm,
    level_6.basin_count,
    level_6.observed_day
   FROM level_6
UNION ALL
 SELECT level_4.huc_level,
    level_4.huc,
    level_4.geom,
    level_4.areasqkm,
    level_4.basin_count,
    level_4.observed_day
   FROM level_4
  WITH NO DATA;


--
-- Name: accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.accounts (
    user_id uuid NOT NULL,
    type text NOT NULL,
    provider text NOT NULL,
    provider_account_id text NOT NULL,
    refresh_token text,
    access_token text,
    expires_at integer,
    token_type text,
    scope text,
    id_token text,
    session_state text
);


--
-- Name: agricultural_solutions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agricultural_solutions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    suitability_rules jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: ai_conversations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_conversations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    geohash character varying(24) NOT NULL,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    title character varying(255) DEFAULT 'New Analysis'::character varying NOT NULL,
    message_count integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ai_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_messages (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    conversation_id uuid NOT NULL,
    role character varying(10) NOT NULL,
    content text NOT NULL,
    structured_response jsonb,
    token_count integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: alert_subscriptions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alert_subscriptions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    watched_location_id uuid,
    alert_type character varying(50) NOT NULL,
    threshold jsonb,
    email_enabled boolean DEFAULT true,
    in_app_enabled boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: api_keys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_keys (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    key_hash text NOT NULL,
    user_id uuid,
    team_id uuid,
    name text,
    permissions jsonb DEFAULT '[]'::jsonb,
    rate_limit integer DEFAULT 1000,
    last_used timestamp with time zone
);


--
-- Name: drought_data; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.drought_data (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    week_date character varying(20) NOT NULL,
    geojson jsonb NOT NULL,
    fetched_at timestamp with time zone DEFAULT now()
);


--
-- Name: email_verification_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.email_verification_tokens (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    token_hash text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: environmental_alerts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.environmental_alerts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    alert_type character varying(50) NOT NULL,
    severity character varying(20) NOT NULL,
    title text NOT NULL,
    body text,
    metadata jsonb,
    is_read boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now(),
    dedupe_key character varying(160)
);


--
-- Name: open_plant_data; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.open_plant_data (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    scientific_name character varying(200) NOT NULL,
    common_name character varying(200),
    solution_id uuid,
    climate_requirements jsonb DEFAULT '{}'::jsonb,
    water_requirements jsonb DEFAULT '{}'::jsonb,
    soil_requirements jsonb DEFAULT '{}'::jsonb,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: open_tooling_data; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.open_tooling_data (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(200) NOT NULL,
    solution_id uuid,
    category character varying(100),
    specifications jsonb DEFAULT '{}'::jsonb,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: password_reset_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.password_reset_tokens (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    token_hash text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: priority_zones; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.priority_zones (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    strategy_type character varying(50) NOT NULL,
    request_count integer NOT NULL,
    total_votes integer NOT NULL,
    centroid_lat double precision,
    centroid_lon double precision,
    geojson jsonb,
    computed_at timestamp with time zone DEFAULT now()
);


--
-- Name: request_votes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.request_votes (
    request_id uuid NOT NULL,
    user_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sessions (
    session_token text NOT NULL,
    user_id uuid NOT NULL,
    expires timestamp with time zone NOT NULL
);


--
-- Name: soil_grid_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.soil_grid_cache (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    ph double precision,
    organic_carbon double precision,
    nitrogen double precision,
    bulk_density double precision,
    cec double precision,
    cached_at timestamp with time zone DEFAULT now(),
    ocd double precision,
    complete boolean DEFAULT false NOT NULL,
    source_url text
);


--
-- Name: strategy_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.strategy_requests (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid,
    team_id uuid,
    strategy_type character varying(50) NOT NULL,
    title text NOT NULL,
    description text,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    status character varying(20) DEFAULT 'open'::character varying,
    vote_count integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: team_invitations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.team_invitations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    team_id uuid NOT NULL,
    email text NOT NULL,
    team_role character varying(20) DEFAULT 'member'::character varying NOT NULL,
    token_hash text NOT NULL,
    invited_by uuid,
    expires_at timestamp with time zone NOT NULL,
    accepted_at timestamp with time zone,
    accepted_by uuid,
    revoked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: team_join_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.team_join_links (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    team_id uuid NOT NULL,
    code_hash text NOT NULL,
    team_role character varying(20) DEFAULT 'viewer'::character varying NOT NULL,
    allowed_email_domain text,
    max_uses integer,
    use_count integer DEFAULT 0 NOT NULL,
    expires_at timestamp with time zone,
    revoked_at timestamp with time zone,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: team_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.team_members (
    team_id uuid NOT NULL,
    user_id uuid NOT NULL,
    team_role character varying(20) DEFAULT 'member'::character varying,
    joined_at timestamp with time zone DEFAULT now()
);


--
-- Name: teams; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.teams (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    slug character varying(100),
    description text,
    org_type character varying(50),
    specialties jsonb,
    website text,
    service_area jsonb,
    is_verified boolean DEFAULT false,
    verified_at timestamp with time zone,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text,
    email text NOT NULL,
    password_hash text,
    platform_role character varying(20) DEFAULT 'contributor'::character varying,
    verified boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now(),
    email_verified timestamp with time zone,
    image text,
    active_team_id uuid
);


--
-- Name: verification_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.verification_tokens (
    identifier text NOT NULL,
    token text NOT NULL,
    expires timestamp with time zone NOT NULL
);


--
-- Name: watched_locations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.watched_locations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    name text NOT NULL,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    radius_km integer DEFAULT 50,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: water_gauges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.water_gauges (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    site_no character varying(20) NOT NULL,
    site_name text,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    flow_cfs double precision,
    percentile integer,
    trend character varying(20),
    condition character varying(30),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: alerts; Type: TABLE; Schema: tracking; Owner: -
--

CREATE TABLE tracking.alerts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    asset_id uuid,
    geofence_id uuid,
    type character varying(50) NOT NULL,
    message text NOT NULL,
    acknowledged boolean DEFAULT false,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: assets; Type: TABLE; Schema: tracking; Owner: -
--

CREATE TABLE tracking.assets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    type character varying(50) DEFAULT 'vehicle'::character varying,
    status character varying(20) DEFAULT 'offline'::character varying,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: geofences; Type: TABLE; Schema: tracking; Owner: -
--

CREATE TABLE tracking.geofences (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    geometry jsonb DEFAULT '{}'::jsonb NOT NULL,
    alert_on_enter boolean DEFAULT true,
    alert_on_exit boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: positions; Type: TABLE; Schema: tracking; Owner: -
--

CREATE TABLE tracking.positions (
    "time" timestamp with time zone NOT NULL,
    asset_id uuid NOT NULL,
    heading double precision,
    speed double precision,
    altitude double precision,
    metadata jsonb DEFAULT '{}'::jsonb,
    geom public.geography(Point,4326)
);


--
-- Name: drought_areas drought_areas_pkey; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.drought_areas
    ADD CONSTRAINT drought_areas_pkey PRIMARY KEY (id);


--
-- Name: features features_pkey; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.features
    ADD CONSTRAINT features_pkey PRIMARY KEY (id);


--
-- Name: geometry geometry_pkey; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.geometry
    ADD CONSTRAINT geometry_pkey PRIMARY KEY (geometry_id);


--
-- Name: historical_fire_data historical_fire_data_pkey; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.historical_fire_data
    ADD CONSTRAINT historical_fire_data_pkey PRIMARY KEY (id);


--
-- Name: historical_vegetation historical_vegetation_pkey; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.historical_vegetation
    ADD CONSTRAINT historical_vegetation_pkey PRIMARY KEY (id);


--
-- Name: historical_water_drought historical_water_drought_pkey; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.historical_water_drought
    ADD CONSTRAINT historical_water_drought_pkey PRIMARY KEY (id);


--
-- Name: layers layers_name_unique; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.layers
    ADD CONSTRAINT layers_name_unique UNIQUE (name);


--
-- Name: layers layers_pkey; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.layers
    ADD CONSTRAINT layers_pkey PRIMARY KEY (id);


--
-- Name: osm_buildings osm_buildings_pkey; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.osm_buildings
    ADD CONSTRAINT osm_buildings_pkey PRIMARY KEY (id);


--
-- Name: osm_landuse osm_landuse_pkey; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.osm_landuse
    ADD CONSTRAINT osm_landuse_pkey PRIMARY KEY (id);


--
-- Name: osm_pois osm_pois_pkey; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.osm_pois
    ADD CONSTRAINT osm_pois_pkey PRIMARY KEY (id);


--
-- Name: osm_roads osm_roads_pkey; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.osm_roads
    ADD CONSTRAINT osm_roads_pkey PRIMARY KEY (id);


--
-- Name: osm_waterways osm_waterways_pkey; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.osm_waterways
    ADD CONSTRAINT osm_waterways_pkey PRIMARY KEY (id);


--
-- Name: poi poi_pkey; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.poi
    ADD CONSTRAINT poi_pkey PRIMARY KEY (id);


--
-- Name: raster_release raster_release_pkey; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.raster_release
    ADD CONSTRAINT raster_release_pkey PRIMARY KEY (id);


--
-- Name: soil_survey_coverage soil_survey_coverage_pkey; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.soil_survey_coverage
    ADD CONSTRAINT soil_survey_coverage_pkey PRIMARY KEY (cell_key);


--
-- Name: geometry uq_geometry_version; Type: CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.geometry
    ADD CONSTRAINT uq_geometry_version UNIQUE (natural_key, version_valid_from);


--
-- Name: accounts accounts_provider_provider_account_id_pk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_provider_provider_account_id_pk PRIMARY KEY (provider, provider_account_id);


--
-- Name: agricultural_solutions agricultural_solutions_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agricultural_solutions
    ADD CONSTRAINT agricultural_solutions_name_key UNIQUE (name);


--
-- Name: agricultural_solutions agricultural_solutions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agricultural_solutions
    ADD CONSTRAINT agricultural_solutions_pkey PRIMARY KEY (id);


--
-- Name: ai_conversations ai_conversations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_conversations
    ADD CONSTRAINT ai_conversations_pkey PRIMARY KEY (id);


--
-- Name: ai_messages ai_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_messages
    ADD CONSTRAINT ai_messages_pkey PRIMARY KEY (id);


--
-- Name: alert_subscriptions alert_subscriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_subscriptions
    ADD CONSTRAINT alert_subscriptions_pkey PRIMARY KEY (id);


--
-- Name: api_keys api_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_pkey PRIMARY KEY (id);


--
-- Name: drought_data drought_data_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drought_data
    ADD CONSTRAINT drought_data_pkey PRIMARY KEY (id);


--
-- Name: drought_data drought_data_week_date_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drought_data
    ADD CONSTRAINT drought_data_week_date_unique UNIQUE (week_date);


--
-- Name: email_verification_tokens email_verification_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_verification_tokens
    ADD CONSTRAINT email_verification_tokens_pkey PRIMARY KEY (id);


--
-- Name: environmental_alerts environmental_alerts_dedupe_key_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.environmental_alerts
    ADD CONSTRAINT environmental_alerts_dedupe_key_unique UNIQUE (dedupe_key);


--
-- Name: environmental_alerts environmental_alerts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.environmental_alerts
    ADD CONSTRAINT environmental_alerts_pkey PRIMARY KEY (id);


--
-- Name: open_plant_data open_plant_data_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.open_plant_data
    ADD CONSTRAINT open_plant_data_pkey PRIMARY KEY (id);


--
-- Name: open_tooling_data open_tooling_data_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.open_tooling_data
    ADD CONSTRAINT open_tooling_data_pkey PRIMARY KEY (id);


--
-- Name: password_reset_tokens password_reset_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_tokens
    ADD CONSTRAINT password_reset_tokens_pkey PRIMARY KEY (id);


--
-- Name: priority_zones priority_zones_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.priority_zones
    ADD CONSTRAINT priority_zones_pkey PRIMARY KEY (id);


--
-- Name: request_votes request_votes_request_id_user_id_pk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_votes
    ADD CONSTRAINT request_votes_request_id_user_id_pk PRIMARY KEY (request_id, user_id);


--
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (session_token);


--
-- Name: soil_grid_cache soil_grid_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.soil_grid_cache
    ADD CONSTRAINT soil_grid_cache_pkey PRIMARY KEY (id);


--
-- Name: strategy_requests strategy_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_requests
    ADD CONSTRAINT strategy_requests_pkey PRIMARY KEY (id);


--
-- Name: team_invitations team_invitations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_invitations
    ADD CONSTRAINT team_invitations_pkey PRIMARY KEY (id);


--
-- Name: team_join_links team_join_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_join_links
    ADD CONSTRAINT team_join_links_pkey PRIMARY KEY (id);


--
-- Name: team_members team_members_team_id_user_id_pk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_members
    ADD CONSTRAINT team_members_team_id_user_id_pk PRIMARY KEY (team_id, user_id);


--
-- Name: teams teams_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams
    ADD CONSTRAINT teams_pkey PRIMARY KEY (id);


--
-- Name: teams teams_slug_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams
    ADD CONSTRAINT teams_slug_unique UNIQUE (slug);


--
-- Name: users users_email_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_unique UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: verification_tokens verification_tokens_identifier_token_pk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verification_tokens
    ADD CONSTRAINT verification_tokens_identifier_token_pk PRIMARY KEY (identifier, token);


--
-- Name: watched_locations watched_locations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watched_locations
    ADD CONSTRAINT watched_locations_pkey PRIMARY KEY (id);


--
-- Name: water_gauges water_gauges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.water_gauges
    ADD CONSTRAINT water_gauges_pkey PRIMARY KEY (id);


--
-- Name: water_gauges water_gauges_site_no_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.water_gauges
    ADD CONSTRAINT water_gauges_site_no_unique UNIQUE (site_no);


--
-- Name: alerts alerts_pkey; Type: CONSTRAINT; Schema: tracking; Owner: -
--

ALTER TABLE ONLY tracking.alerts
    ADD CONSTRAINT alerts_pkey PRIMARY KEY (id);


--
-- Name: assets assets_pkey; Type: CONSTRAINT; Schema: tracking; Owner: -
--

ALTER TABLE ONLY tracking.assets
    ADD CONSTRAINT assets_pkey PRIMARY KEY (id);


--
-- Name: geofences geofences_pkey; Type: CONSTRAINT; Schema: tracking; Owner: -
--

ALTER TABLE ONLY tracking.geofences
    ADD CONSTRAINT geofences_pkey PRIMARY KEY (id);


--
-- Name: drought_areas_geom_gist; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX drought_areas_geom_gist ON geo.drought_areas USING gist (geom);


--
-- Name: drought_areas_valid_date_category_unique; Type: INDEX; Schema: geo; Owner: -
--

CREATE UNIQUE INDEX drought_areas_valid_date_category_unique ON geo.drought_areas USING btree (valid_date, dm_category);


--
-- Name: drought_areas_valid_date_idx; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX drought_areas_valid_date_idx ON geo.drought_areas USING btree (valid_date);


--
-- Name: features_layer_external_id_unique; Type: INDEX; Schema: geo; Owner: -
--

CREATE UNIQUE INDEX features_layer_external_id_unique ON geo.features USING btree (layer_id, ((properties ->> 'id'::text))) WHERE (properties ? 'id'::text);


--
-- Name: idx_features_geom; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_features_geom ON geo.features USING gist (geom);


--
-- Name: idx_features_layer; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_features_layer ON geo.features USING btree (layer_id);


--
-- Name: idx_features_layer_created_at; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_features_layer_created_at ON geo.features USING btree (layer_id, created_at);


--
-- Name: idx_features_layer_status; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_features_layer_status ON geo.features USING btree (layer_id, status);


--
-- Name: idx_features_layer_updated_at; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_features_layer_updated_at ON geo.features USING btree (layer_id, updated_at);


--
-- Name: idx_historical_fire_data_geom; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_historical_fire_data_geom ON geo.historical_fire_data USING gist (geom);


--
-- Name: idx_historical_fire_data_location; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_historical_fire_data_location ON geo.historical_fire_data USING btree (lat, lon, date_bucket DESC);


--
-- Name: idx_historical_vegetation_geom; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_historical_vegetation_geom ON geo.historical_vegetation USING gist (geom);


--
-- Name: idx_historical_vegetation_location; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_historical_vegetation_location ON geo.historical_vegetation USING btree (lat, lon, date_bucket DESC);


--
-- Name: idx_historical_water_drought_geom; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_historical_water_drought_geom ON geo.historical_water_drought USING gist (geom);


--
-- Name: idx_historical_water_drought_location; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_historical_water_drought_location ON geo.historical_water_drought USING btree (lat, lon, date_bucket DESC);


--
-- Name: idx_mv_strategy_recommendations_coarse_geom; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_mv_strategy_recommendations_coarse_geom ON geo.mv_strategy_recommendations_coarse USING gist (geom);


--
-- Name: idx_mv_strategy_recommendations_detail_geom; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_mv_strategy_recommendations_detail_geom ON geo.mv_strategy_recommendations_detail USING gist (geom);


--
-- Name: idx_mv_strategy_recommendations_regional_geom; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_mv_strategy_recommendations_regional_geom ON geo.mv_strategy_recommendations_regional USING gist (geom);


--
-- Name: idx_osm_buildings_geom; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_osm_buildings_geom ON geo.osm_buildings USING gist (geom);


--
-- Name: idx_osm_landuse_geom; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_osm_landuse_geom ON geo.osm_landuse USING gist (geom);


--
-- Name: idx_osm_pois_amenity; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_osm_pois_amenity ON geo.osm_pois USING btree (amenity);


--
-- Name: idx_osm_pois_geom; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_osm_pois_geom ON geo.osm_pois USING gist (geom);


--
-- Name: idx_osm_roads_geom; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_osm_roads_geom ON geo.osm_roads USING gist (geom);


--
-- Name: idx_osm_roads_highway; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_osm_roads_highway ON geo.osm_roads USING btree (highway);


--
-- Name: idx_osm_waterways_geom; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_osm_waterways_geom ON geo.osm_waterways USING gist (geom);


--
-- Name: idx_poi_geom; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX idx_poi_geom ON geo.poi USING gist (geom);


--
-- Name: ix_features_geometry_id; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX ix_features_geometry_id ON geo.features USING btree (geometry_id);


--
-- Name: ix_features_layer_geom; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX ix_features_layer_geom ON geo.features USING gist (layer_id, geom) WHERE (((status)::text = 'published'::text) AND (geom IS NOT NULL));


--
-- Name: ix_features_layer_observation_day; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX ix_features_layer_observation_day ON geo.features USING btree (layer_id, geo.feature_observation_day(properties)) INCLUDE (geometry_id) WHERE ((status)::text = 'published'::text);


--
-- Name: ix_features_updated_at; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX ix_features_updated_at ON geo.features USING btree (updated_at DESC);


--
-- Name: ix_geometry_asof; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX ix_geometry_asof ON geo.geometry USING btree (natural_key, version_valid_from DESC);


--
-- Name: ix_geometry_kind; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX ix_geometry_kind ON geo.geometry USING btree (geom_kind, producer);


--
-- Name: ix_mv_soil_survey_union_geom; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX ix_mv_soil_survey_union_geom ON geo.mv_soil_survey_union USING gist (geom);


--
-- Name: ix_raster_release_live_collection; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX ix_raster_release_live_collection ON geo.raster_release USING btree (collection, property) WHERE (superseded_at IS NULL);


--
-- Name: ix_soil_survey_coverage_fetched_at; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX ix_soil_survey_coverage_fetched_at ON geo.soil_survey_coverage USING btree (fetched_at);


--
-- Name: uq_geometry_current; Type: INDEX; Schema: geo; Owner: -
--

CREATE UNIQUE INDEX uq_geometry_current ON geo.geometry USING btree (natural_key) WHERE (version_valid_to IS NULL);


--
-- Name: uq_geometry_grid_cell; Type: INDEX; Schema: geo; Owner: -
--

CREATE UNIQUE INDEX uq_geometry_grid_cell ON geo.geometry USING btree (grid_name, cell_key) WHERE (version_valid_to IS NULL);


--
-- Name: uq_mv_drought_observation_day; Type: INDEX; Schema: geo; Owner: -
--

CREATE UNIQUE INDEX uq_mv_drought_observation_day ON geo.mv_drought_observation_day USING btree (surface_name, observed_day);


--
-- Name: uq_mv_drought_release_index; Type: INDEX; Schema: geo; Owner: -
--

CREATE UNIQUE INDEX uq_mv_drought_release_index ON geo.mv_drought_release_index USING btree (valid_date);


--
-- Name: uq_mv_feature_observation_day; Type: INDEX; Schema: geo; Owner: -
--

CREATE UNIQUE INDEX uq_mv_feature_observation_day ON geo.mv_feature_observation_day USING btree (surface_name, observed_day);


--
-- Name: uq_mv_layer_feature_stats; Type: INDEX; Schema: geo; Owner: -
--

CREATE UNIQUE INDEX uq_mv_layer_feature_stats ON geo.mv_layer_feature_stats USING btree (layer_id);


--
-- Name: uq_mv_layer_hourly_activity; Type: INDEX; Schema: geo; Owner: -
--

CREATE UNIQUE INDEX uq_mv_layer_hourly_activity ON geo.mv_layer_hourly_activity USING btree (layer_id, hour_bucket);


--
-- Name: uq_mv_signal_observation_day; Type: INDEX; Schema: geo; Owner: -
--

CREATE UNIQUE INDEX uq_mv_signal_observation_day ON geo.mv_signal_observation_day USING btree (surface_name, observed_day);


--
-- Name: uq_mv_soil_survey_grid; Type: INDEX; Schema: geo; Owner: -
--

CREATE UNIQUE INDEX uq_mv_soil_survey_grid ON geo.mv_soil_survey_grid USING btree (zoom_tier, cell_col, cell_row);


--
-- Name: uq_mv_soil_survey_union; Type: INDEX; Schema: geo; Owner: -
--

CREATE UNIQUE INDEX uq_mv_soil_survey_union ON geo.mv_soil_survey_union USING btree (zoom_tier, drainage_class);


--
-- Name: uq_mv_strategy_recommendations_coarse; Type: INDEX; Schema: geo; Owner: -
--

CREATE UNIQUE INDEX uq_mv_strategy_recommendations_coarse ON geo.mv_strategy_recommendations_coarse USING btree (strategy_id, cell_id);


--
-- Name: uq_mv_strategy_recommendations_detail; Type: INDEX; Schema: geo; Owner: -
--

CREATE UNIQUE INDEX uq_mv_strategy_recommendations_detail ON geo.mv_strategy_recommendations_detail USING btree (strategy_id, cell_id);


--
-- Name: uq_mv_strategy_recommendations_regional; Type: INDEX; Schema: geo; Owner: -
--

CREATE UNIQUE INDEX uq_mv_strategy_recommendations_regional ON geo.mv_strategy_recommendations_regional USING btree (strategy_id, cell_id);


--
-- Name: ux_raster_release_live; Type: INDEX; Schema: geo; Owner: -
--

CREATE UNIQUE INDEX ux_raster_release_live ON geo.raster_release USING btree (collection, property, depth, statistic, archive_format) WHERE (superseded_at IS NULL);


--
-- Name: watershed_rollup_geom_idx; Type: INDEX; Schema: geo; Owner: -
--

CREATE INDEX watershed_rollup_geom_idx ON geo.watershed_rollup USING gist (geom);


--
-- Name: watershed_rollup_level_huc_idx; Type: INDEX; Schema: geo; Owner: -
--

CREATE UNIQUE INDEX watershed_rollup_level_huc_idx ON geo.watershed_rollup USING btree (huc_level, huc);


--
-- Name: api_keys_key_hash_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX api_keys_key_hash_unique ON public.api_keys USING btree (key_hash);


--
-- Name: email_verification_tokens_token_hash_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX email_verification_tokens_token_hash_unique ON public.email_verification_tokens USING btree (token_hash);


--
-- Name: email_verification_tokens_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX email_verification_tokens_user_idx ON public.email_verification_tokens USING btree (user_id);


--
-- Name: password_reset_tokens_token_hash_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX password_reset_tokens_token_hash_unique ON public.password_reset_tokens USING btree (token_hash);


--
-- Name: password_reset_tokens_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX password_reset_tokens_user_idx ON public.password_reset_tokens USING btree (user_id);


--
-- Name: soil_grid_cache_cell_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX soil_grid_cache_cell_unique ON public.soil_grid_cache USING btree (lat, lon);


--
-- Name: team_invitations_email_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX team_invitations_email_idx ON public.team_invitations USING btree (email);


--
-- Name: team_invitations_team_email_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX team_invitations_team_email_idx ON public.team_invitations USING btree (team_id, email);


--
-- Name: team_invitations_token_hash_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX team_invitations_token_hash_unique ON public.team_invitations USING btree (token_hash);


--
-- Name: team_join_links_code_hash_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX team_join_links_code_hash_unique ON public.team_join_links USING btree (code_hash);


--
-- Name: team_join_links_team_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX team_join_links_team_idx ON public.team_join_links USING btree (team_id);


--
-- Name: idx_positions_asset; Type: INDEX; Schema: tracking; Owner: -
--

CREATE INDEX idx_positions_asset ON tracking.positions USING btree (asset_id, "time" DESC);


--
-- Name: idx_positions_geom; Type: INDEX; Schema: tracking; Owner: -
--

CREATE INDEX idx_positions_geom ON tracking.positions USING gist (geom);


--
-- Name: positions_asset_time_unique; Type: INDEX; Schema: tracking; Owner: -
--

CREATE UNIQUE INDEX positions_asset_time_unique ON tracking.positions USING btree (asset_id, "time");


--
-- Name: positions_time_idx; Type: INDEX; Schema: tracking; Owner: -
--

CREATE INDEX positions_time_idx ON tracking.positions USING btree ("time" DESC);


--
-- Name: features geo_features_sync_geom; Type: TRIGGER; Schema: geo; Owner: -
--

CREATE TRIGGER geo_features_sync_geom BEFORE INSERT OR UPDATE OF properties ON geo.features FOR EACH ROW EXECUTE FUNCTION geo.sync_feature_geom_from_properties();


--
-- Name: historical_fire_data geo_historical_fire_data_sync_geom; Type: TRIGGER; Schema: geo; Owner: -
--

CREATE TRIGGER geo_historical_fire_data_sync_geom BEFORE INSERT OR UPDATE OF lat, lon ON geo.historical_fire_data FOR EACH ROW EXECUTE FUNCTION geo.sync_historical_point_geom();


--
-- Name: historical_vegetation geo_historical_vegetation_sync_geom; Type: TRIGGER; Schema: geo; Owner: -
--

CREATE TRIGGER geo_historical_vegetation_sync_geom BEFORE INSERT OR UPDATE OF lat, lon ON geo.historical_vegetation FOR EACH ROW EXECUTE FUNCTION geo.sync_historical_point_geom();


--
-- Name: historical_water_drought geo_historical_water_drought_sync_geom; Type: TRIGGER; Schema: geo; Owner: -
--

CREATE TRIGGER geo_historical_water_drought_sync_geom BEFORE INSERT OR UPDATE OF lat, lon ON geo.historical_water_drought FOR EACH ROW EXECUTE FUNCTION geo.sync_historical_point_geom();


--
-- Name: positions tracking_positions_sync_geom; Type: TRIGGER; Schema: tracking; Owner: -
--

CREATE TRIGGER tracking_positions_sync_geom BEFORE INSERT OR UPDATE OF metadata ON tracking.positions FOR EACH ROW EXECUTE FUNCTION tracking.sync_position_geom_from_metadata();


--
-- Name: features features_geometry_id_fkey; Type: FK CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.features
    ADD CONSTRAINT features_geometry_id_fkey FOREIGN KEY (geometry_id) REFERENCES geo.geometry(geometry_id) ON DELETE RESTRICT;


--
-- Name: features features_layer_id_layers_id_fk; Type: FK CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.features
    ADD CONSTRAINT features_layer_id_layers_id_fk FOREIGN KEY (layer_id) REFERENCES geo.layers(id) ON DELETE CASCADE;


--
-- Name: geometry geometry_superseded_by_fkey; Type: FK CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.geometry
    ADD CONSTRAINT geometry_superseded_by_fkey FOREIGN KEY (superseded_by) REFERENCES geo.geometry(geometry_id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;


--
-- Name: layers layers_team_id_teams_id_fk; Type: FK CONSTRAINT; Schema: geo; Owner: -
--

ALTER TABLE ONLY geo.layers
    ADD CONSTRAINT layers_team_id_teams_id_fk FOREIGN KEY (team_id) REFERENCES public.teams(id);


--
-- Name: accounts accounts_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: ai_conversations ai_conversations_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_conversations
    ADD CONSTRAINT ai_conversations_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: ai_messages ai_messages_conversation_id_ai_conversations_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_messages
    ADD CONSTRAINT ai_messages_conversation_id_ai_conversations_id_fk FOREIGN KEY (conversation_id) REFERENCES public.ai_conversations(id) ON DELETE CASCADE;


--
-- Name: alert_subscriptions alert_subscriptions_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_subscriptions
    ADD CONSTRAINT alert_subscriptions_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: alert_subscriptions alert_subscriptions_watched_location_id_watched_locations_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_subscriptions
    ADD CONSTRAINT alert_subscriptions_watched_location_id_watched_locations_id_fk FOREIGN KEY (watched_location_id) REFERENCES public.watched_locations(id) ON DELETE CASCADE;


--
-- Name: api_keys api_keys_team_id_teams_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_team_id_teams_id_fk FOREIGN KEY (team_id) REFERENCES public.teams(id);


--
-- Name: api_keys api_keys_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: email_verification_tokens email_verification_tokens_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_verification_tokens
    ADD CONSTRAINT email_verification_tokens_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: environmental_alerts environmental_alerts_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.environmental_alerts
    ADD CONSTRAINT environmental_alerts_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: open_plant_data open_plant_data_solution_id_agricultural_solutions_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.open_plant_data
    ADD CONSTRAINT open_plant_data_solution_id_agricultural_solutions_id_fk FOREIGN KEY (solution_id) REFERENCES public.agricultural_solutions(id);


--
-- Name: open_tooling_data open_tooling_data_solution_id_agricultural_solutions_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.open_tooling_data
    ADD CONSTRAINT open_tooling_data_solution_id_agricultural_solutions_id_fk FOREIGN KEY (solution_id) REFERENCES public.agricultural_solutions(id);


--
-- Name: password_reset_tokens password_reset_tokens_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_tokens
    ADD CONSTRAINT password_reset_tokens_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: request_votes request_votes_request_id_strategy_requests_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_votes
    ADD CONSTRAINT request_votes_request_id_strategy_requests_id_fk FOREIGN KEY (request_id) REFERENCES public.strategy_requests(id) ON DELETE CASCADE;


--
-- Name: request_votes request_votes_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_votes
    ADD CONSTRAINT request_votes_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: sessions sessions_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: strategy_requests strategy_requests_team_id_teams_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_requests
    ADD CONSTRAINT strategy_requests_team_id_teams_id_fk FOREIGN KEY (team_id) REFERENCES public.teams(id);


--
-- Name: strategy_requests strategy_requests_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_requests
    ADD CONSTRAINT strategy_requests_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: team_invitations team_invitations_accepted_by_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_invitations
    ADD CONSTRAINT team_invitations_accepted_by_users_id_fk FOREIGN KEY (accepted_by) REFERENCES public.users(id);


--
-- Name: team_invitations team_invitations_invited_by_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_invitations
    ADD CONSTRAINT team_invitations_invited_by_users_id_fk FOREIGN KEY (invited_by) REFERENCES public.users(id);


--
-- Name: team_invitations team_invitations_team_id_teams_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_invitations
    ADD CONSTRAINT team_invitations_team_id_teams_id_fk FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: team_join_links team_join_links_created_by_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_join_links
    ADD CONSTRAINT team_join_links_created_by_users_id_fk FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: team_join_links team_join_links_team_id_teams_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_join_links
    ADD CONSTRAINT team_join_links_team_id_teams_id_fk FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: team_members team_members_team_id_teams_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_members
    ADD CONSTRAINT team_members_team_id_teams_id_fk FOREIGN KEY (team_id) REFERENCES public.teams(id) ON DELETE CASCADE;


--
-- Name: team_members team_members_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_members
    ADD CONSTRAINT team_members_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: teams teams_created_by_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams
    ADD CONSTRAINT teams_created_by_users_id_fk FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: watched_locations watched_locations_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watched_locations
    ADD CONSTRAINT watched_locations_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: positions positions_asset_id_assets_id_fk; Type: FK CONSTRAINT; Schema: tracking; Owner: -
--

ALTER TABLE ONLY tracking.positions
    ADD CONSTRAINT positions_asset_id_assets_id_fk FOREIGN KEY (asset_id) REFERENCES tracking.assets(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--


