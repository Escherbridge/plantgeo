-- Resolve the layer NAME to a `layer_id` constant inside each tile function, so the scan of
-- `geo.features` carries a direct `layer_id` restriction instead of reaching it through a join to
-- `geo.layers`. Six of the seven Martin-registered function sources are rewritten. Nothing else
-- changes: same names, same signatures, same argument names, same return type, same volatility,
-- same parallel safety, same search_path settings, same emitted MVT attributes, same predicates,
-- same tile bytes.
--
--
-- WHY. THE ONE FACT THE WHOLE FILE TURNS ON.
--
-- Every one of the six selects its layer by name, through a join:
--
--   FROM geo.features f JOIN geo.layers l ON f.layer_id = l.id
--   WHERE l.name = 'burn-severity' AND l.is_public IS TRUE ...
--
-- The only constant in that query is `l.name`. There is no constant `f.layer_id` anywhere in it and
-- the planner does not derive one: equivalence classes propagate the join equality
-- `f.layer_id = l.id`, and never the unrelated restriction on `l.name`. `drizzle/0030`'s header
-- records this same fact as the reason eleven per-layer partial GiST indexes would have sat unused.
--
-- On the LIST-partitioned `geo.features` this project is moving to -- one partition per layer plus a
-- mandatory DEFAULT partition -- that missing constant is the difference between touching one
-- partition and touching all twelve. Partition pruning needs a restriction on the partition key; a
-- join equality is not one. A tile request that should read 541 burn-severity rows would instead
-- open every partition, including the 3.0M-row fire-detections one that shares the Pacific Northwest
-- bounding box with every other layer.
--
-- After this file the same query reads:
--
--   FROM geo.features f
--   WHERE f.layer_id = target_layer_id AND ...
--
-- and `target_layer_id` is a plpgsql variable holding a uuid, which is exactly the shape the
-- partition pruner requires.
--
--
-- NO HARDCODED UUIDS -- THE ALTERNATIVE 0030 REJECTED, AND STILL REJECTS.
--
-- `drizzle/0030`'s header rejected "inlining eleven hardcoded uuids into eleven tile function
-- bodies" because it bakes environment-specific ids into schema DDL that is replayed against every
-- database. That objection is respected here. The name-to-id resolution runs at call time against
-- `geo.layers`, which is an eleven-row table with a unique index on `name`, so the lookup is a
-- single index probe and the DDL stays environment-independent. A twelfth layer created tomorrow
-- needs no change to this file.
--
--
-- WHY A VARIABLE IS ENOUGH -- THE PLAN-CACHE STEP THAT IS EASY TO GET WRONG.
--
-- A plpgsql variable reaches the planner as a parameter, not a literal, so it is worth stating
-- exactly when pruning happens:
--
--   * Under a CUSTOM plan the parameter's value is supplied at planning time and folded to a
--     constant, so partitions are pruned at PLAN time -- the guarantee this change is for.
--   * Under a GENERIC plan the value is unknown at planning time, so pruning moves to Append
--     initialisation. Still one partition scanned, but the plan is built over all of them.
--
-- Which one the plan cache picks is not left to luck. A generic plan cannot prune, so its cost
-- estimate includes every partition's scan; the custom plan's includes one. The custom plan is
-- therefore cheaper by roughly the partition count and keeps winning that comparison for as long as
-- the table stays partitioned on `layer_id`. If that is ever measured to be false on production
-- -- EXPLAIN (ANALYZE, BUFFERS) on a live tile call, looking for "Subplans Removed" rather than a
-- single-partition Append -- the one-line escalation is `SET plan_cache_mode = force_custom_plan`
-- in each function's SET clause, at the price of re-planning every tile request. It is deliberately
-- NOT applied pre-emptively: it is a real cost, and the reasoning above says it is not needed.
--
--
-- BEHAVIOURAL EQUIVALENCE, INCLUDING THE EDGE CASES.
--
-- `SELECT l.id INTO target_layer_id FROM geo.layers l WHERE l.name = ... AND l.is_public IS TRUE`
-- replaces the join, and the two agree row-for-row because:
--
--   * `geo.layers.name` carries `layers_name_unique` (`drizzle/0000:146`), so at most one row can
--     match and plpgsql's non-STRICT SELECT INTO cannot silently pick one of several. The
--     precondition below ASSERTS that unique constraint rather than trusting it, because it is the
--     single invariant that makes this rewrite equivalent instead of merely similar.
--   * `l.is_public IS TRUE` moves into the resolver unchanged, so a layer flipped non-public still
--     empties its tile on the very next request -- the lookup runs per call, it is not cached.
--   * When nothing matches, `target_layer_id` stays NULL, `f.layer_id = NULL` is NULL for every row,
--     the subquery returns nothing, ST_AsMVT returns NULL and the existing
--     `COALESCE(..., ''::bytea)` returns the same empty tile the join returned. No new failure mode,
--     and specifically not a NULL return, which would 404 the whole composite.
--
--
-- SAFE TO SHIP BEFORE THE PARTITION SWAP. THAT IS THE POINT OF SHIPPING IT SEPARATELY.
--
-- On today's unpartitioned `geo.features` these rewrites are correct and harmless, and marginally
-- better: the join to `geo.layers` was already only a way to obtain a parameterised `layer_id` for
-- the inner side of a nested loop, and `ix_features_layer_geom` (GiST (layer_id, geom) WHERE
-- published AND geom IS NOT NULL, live in production per runbook 0.3) is probed with the identical
-- `(layer_id = $1 AND geom && $2)` index condition either way. One join node disappears; no access
-- path does. So this file can land, be verified against live tiles, and be left alone -- the swap
-- then inherits six functions that already prune, instead of six that must be rewritten inside the
-- maintenance window.
--
--
-- MARTIN MUST BE RESTARTED AFTER THIS MIGRATION. NOTHING IN THE PIPELINE DOES IT.
--
-- Not because the source catalogue changed -- it did not. `infra/martin/martin.yaml:43-67` runs
-- `auto_publish: false` and names all seven functions explicitly; every name and signature here is
-- unchanged, so that file needs no edit and Martin's startup enumeration finds exactly what it found
-- before. The restart is because Martin's pool holds long-lived sessions, and a tile function is the
-- one object in this system whose failure is not local: a missing or broken function 404s the WHOLE
-- composite tile request and hides EVERY layer on the map, not just its own. Restarting is the cheap
-- way to guarantee no session is executing a plan built before this file ran. Restart it, then fetch
-- one tile per rewritten source with an `Origin` header (runbook section 5) before calling this done.
--
--
-- WHAT IS DELIBERATELY NOT TOUCHED.
--
--   * `geo.building_tiles` reads `geo.osm_buildings`, not `geo.features`. Nothing to prune.
--   * `geo.watershed_tiles`'s COARSE branches (`target_level` < 12) read `geo.watershed_rollup` and
--     are reproduced here character-for-character. Only the detail branch reads `geo.features`.
--   * `geo.strategy_recommendations_tiles` exists in the database but is absent from `martin.yaml`,
--     so it is never served. Out of scope for a file whose whole risk is the tile path.
--   * `geo.intervention_tiles` has NO `SET search_path` clause. That is how `drizzle/0005` left it
--     and it is preserved exactly, because "same attributes" includes the ones that look like
--     oversights. Adding one is a separate decision with its own review, not a drive-by.
--   * `geo.feature_observation_day` is unchanged; five of the six call it and the precondition below
--     asserts it is present, since a plpgsql body is not resolved at CREATE time and a missing
--     callee surfaces as a blanked composite at the first tile request, not as a failed migration.
--
-- Every statement below is a plain CREATE OR REPLACE FUNCTION plus one DO assertion. No CONCURRENTLY
-- appears anywhere, because a drizzle .sql file runs inside ONE transaction -- stated at
-- `drizzle/0031:27-28` -- and the whole file is cheap enough that this costs nothing.

-- ---------------------------------------------------------------------------------------------
-- 0. PRECONDITION -- refuse to run against a database that is missing any of the six
-- ---------------------------------------------------------------------------------------------
-- CREATE OR REPLACE FUNCTION would happily CREATE a function that ought to have existed, which is
-- the failure this guards: on a database where a tile function is absent for a reason -- a partial
-- restore, a hand-edited schema, a migration history replayed out of order -- silently minting it
-- here hides the real divergence and ships a body nobody verified against that database's contents.
-- `to_regprocedure` is used rather than a bare name lookup because it matches on the FULL signature:
-- a function whose argument types have drifted reads as absent, which is exactly right, since
-- martin.yaml binds to the signature and a drifted one is already unserved.
DO $$
DECLARE
  required_routine text;
  required_relation text;
  missing_objects text[] := ARRAY[]::text[];
  layer_name_is_unique boolean;
BEGIN
  FOREACH required_routine IN ARRAY ARRAY[
    'geo.burn_severity_tiles(integer,integer,integer)',
    'geo.evacuation_zone_tiles(integer,integer,integer)',
    'geo.fire_risk_tiles(integer,integer,integer)',
    'geo.sensor_tiles(integer,integer,integer)',
    'geo.intervention_tiles(integer,integer,integer)',
    'geo.watershed_tiles(integer,integer,integer)',
    'geo.feature_observation_day(jsonb)'
  ] LOOP
    IF to_regprocedure(required_routine) IS NULL THEN
      missing_objects := missing_objects || required_routine;
    END IF;
  END LOOP;

  FOREACH required_relation IN ARRAY ARRAY[
    'geo.features',
    'geo.layers',
    'geo.watershed_rollup'
  ] LOOP
    IF to_regclass(required_relation) IS NULL THEN
      missing_objects := missing_objects || required_relation;
    END IF;
  END LOOP;

  IF cardinality(missing_objects) > 0 THEN
    RAISE EXCEPTION
      'drizzle/0033 rewrites existing tile functions and refuses to create them: % is/are absent. '
      'Apply drizzle/0005, 0015 and 0023 first. Do NOT drop this assertion to get past it -- a tile '
      'function created here against an unverified schema 404s the whole composite tile request and '
      'hides every layer on the map.',
      array_to_string(missing_objects, ', ');
  END IF;

  -- The invariant that makes a SELECT INTO equivalent to the join it replaces. Without a
  -- single-column unique index on geo.layers(name), two layers could share a name, the join would
  -- emit the union of both layers' features and the resolver would pick one arbitrarily -- a silent
  -- per-tile wrong answer rather than an error. `indpred IS NULL` because a PARTIAL unique index
  -- does not establish uniqueness over the rows this resolver reads.
  SELECT EXISTS (
    SELECT 1
    FROM pg_index i
    JOIN pg_attribute a
      ON a.attrelid = i.indrelid
     AND a.attnum = i.indkey[0]
    WHERE i.indrelid = to_regclass('geo.layers')
      AND i.indisunique
      AND i.indisvalid
      AND i.indnkeyatts = 1
      AND i.indpred IS NULL
      AND a.attname = 'name'
  ) INTO layer_name_is_unique;

  IF NOT layer_name_is_unique THEN
    RAISE EXCEPTION
      'geo.layers has no valid single-column unique index on (name). The rewrites in drizzle/0033 '
      'resolve a layer name to one id with SELECT INTO, which is only equivalent to the join it '
      'replaces while that uniqueness holds. Restore layers_name_unique before applying.';
  END IF;
END $$;
--> statement-breakpoint

-- ---------------------------------------------------------------------------------------------
-- 1. geo.burn_severity_tiles
-- ---------------------------------------------------------------------------------------------
-- Body reproduced from drizzle/0015:63-113. The only edits are the declared `target_layer_id`, the
-- resolver, and the FROM/WHERE of the features scan.
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
  target_layer_id uuid;
  mvt bytea;
BEGIN
  bounds_3857 := ST_TileEnvelope(z, x, y);
  bounds_4326 := ST_Transform(bounds_3857, 4326);

  -- The layer id as a constant, so the features scan below prunes. See this file's header.
  SELECT l.id INTO target_layer_id
  FROM geo.layers l
  WHERE l.name = 'burn-severity'
    AND l.is_public IS TRUE;

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
    WHERE f.layer_id = target_layer_id
      AND f.status = 'published'
      AND f.geom IS NOT NULL
      AND f.geom && bounds_4326
      AND ST_Intersects(f.geom, bounds_4326)
  ) AS tile;

  RETURN mvt;
END;
$$;
--> statement-breakpoint

-- ---------------------------------------------------------------------------------------------
-- 2. geo.evacuation_zone_tiles
-- ---------------------------------------------------------------------------------------------
-- Body reproduced from drizzle/0015:116-155.
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
  target_layer_id uuid;
  mvt bytea;
BEGIN
  bounds_3857 := ST_TileEnvelope(z, x, y);
  bounds_4326 := ST_Transform(bounds_3857, 4326);

  -- The layer id as a constant, so the features scan below prunes. See this file's header.
  SELECT l.id INTO target_layer_id
  FROM geo.layers l
  WHERE l.name = 'evacuation-zones'
    AND l.is_public IS TRUE;

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
    WHERE f.layer_id = target_layer_id
      AND f.status = 'published'
      AND f.geom IS NOT NULL
      AND f.geom && bounds_4326
      AND ST_Intersects(f.geom, bounds_4326)
  ) AS tile;

  RETURN mvt;
END;
$$;
--> statement-breakpoint

-- ---------------------------------------------------------------------------------------------
-- 3. geo.fire_risk_tiles
-- ---------------------------------------------------------------------------------------------
-- Body reproduced from drizzle/0015:159-197. The MVT tag is 'fire_risk' while the layer is
-- 'fire-perimeters' -- they have never matched, and this file is not the place to change either.
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
  target_layer_id uuid;
  mvt bytea;
BEGIN
  bounds_3857 := ST_TileEnvelope(z, x, y);
  bounds_4326 := ST_Transform(bounds_3857, 4326);

  -- The layer id as a constant, so the features scan below prunes. See this file's header.
  SELECT l.id INTO target_layer_id
  FROM geo.layers l
  WHERE l.name = 'fire-perimeters'
    AND l.is_public IS TRUE;

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
    WHERE f.layer_id = target_layer_id
      AND f.status = 'published'
      AND f.geom IS NOT NULL
      AND f.geom && bounds_4326
      AND ST_Intersects(f.geom, bounds_4326)
  ) AS tile;

  RETURN mvt;
END;
$$;
--> statement-breakpoint

-- ---------------------------------------------------------------------------------------------
-- 4. geo.sensor_tiles
-- ---------------------------------------------------------------------------------------------
-- Body reproduced from drizzle/0015:198-237.
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
  target_layer_id uuid;
  mvt bytea;
BEGIN
  bounds_3857 := ST_TileEnvelope(z, x, y);
  bounds_4326 := ST_Transform(bounds_3857, 4326);

  -- The layer id as a constant, so the features scan below prunes. See this file's header.
  SELECT l.id INTO target_layer_id
  FROM geo.layers l
  WHERE l.name = 'sensors'
    AND l.is_public IS TRUE;

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
    WHERE f.layer_id = target_layer_id
      AND f.status = 'published'
      AND f.geom IS NOT NULL
      AND f.geom && bounds_4326
      AND ST_Intersects(f.geom, bounds_4326)
  ) AS tile;

  RETURN mvt;
END;
$$;
--> statement-breakpoint

-- ---------------------------------------------------------------------------------------------
-- 5. geo.intervention_tiles
-- ---------------------------------------------------------------------------------------------
-- Body reproduced from drizzle/0005:6-38, which is the live definition and supersedes
-- drizzle/0001:448-484. It predates `geo.feature_observation_day`, so it emits no `observed_day` and
-- none is added here -- putting this layer on the time slider is a product change, not a pruning
-- change. It also carries no SET search_path clause; see this file's header.
CREATE OR REPLACE FUNCTION geo.intervention_tiles(z integer, x integer, y integer)
RETURNS bytea
LANGUAGE plpgsql
STABLE
PARALLEL SAFE
AS $$
DECLARE
  bounds_3857 geometry;
  bounds_4326 geometry;
  target_layer_id uuid;
  mvt bytea;
BEGIN
  bounds_3857 := ST_TileEnvelope(z, x, y);
  bounds_4326 := ST_Transform(bounds_3857, 4326);

  -- The layer id as a constant, so the features scan below prunes. See this file's header.
  SELECT l.id INTO target_layer_id
  FROM geo.layers l
  WHERE l.name = 'interventions'
    AND l.is_public IS TRUE;

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
    WHERE f.layer_id = target_layer_id
      AND f.status = 'published'
      AND f.geom IS NOT NULL
      AND f.geom && bounds_4326
      AND ST_Intersects(f.geom, bounds_4326)
  ) AS tile;

  RETURN mvt;
END;
$$;
--> statement-breakpoint

-- ---------------------------------------------------------------------------------------------
-- 6. geo.watershed_tiles -- DETAIL BRANCH ONLY
-- ---------------------------------------------------------------------------------------------
-- Body reproduced from drizzle/0023:131-207. Only the `target_level = 12` branch reads
-- `geo.features`; the ELSE branch reads `geo.watershed_rollup` and is reproduced unchanged,
-- including its NULL-typed placeholder columns. The resolver sits INSIDE the detail branch rather
-- than beside the bounds, so a coarse-zoom tile -- the common case at low zoom -- does not pay a
-- lookup whose result it would never read.
CREATE OR REPLACE FUNCTION geo.watershed_tiles(z integer, x integer, y integer)
RETURNS bytea
LANGUAGE plpgsql
STABLE
PARALLEL SAFE
SET search_path = public, pg_catalog
AS $$
DECLARE
  bounds_3857 geometry;
  bounds_4326 geometry;
  target_level integer;
  target_layer_id uuid;
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
    -- The layer id as a constant, so the features scan below prunes. See this file's header.
    SELECT l.id INTO target_layer_id
    FROM geo.layers l
    WHERE l.name = 'watersheds'
      AND l.is_public IS TRUE;

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
      WHERE f.layer_id = target_layer_id
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
