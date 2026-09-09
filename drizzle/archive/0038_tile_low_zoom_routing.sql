-- Put a hard ceiling on every geo.features-backed tile function, and give geo.sensor_tiles the
-- one bound it actually needs. Five CREATE OR REPLACEs: geo.sensor_tiles, geo.fire_risk_tiles,
-- geo.burn_severity_tiles, geo.evacuation_zone_tiles, geo.intervention_tiles.
--
-- Nothing else changes. Same names, same argument names and types, same bytea return, same
-- STABLE, same PARALLEL SAFE, same `SET search_path = public, pg_catalog`, same ST_AsMVT layer
-- tag, same attribute list in the same order, same ST_AsMVTGeom(ST_Transform(geom, 3857), ...)
-- geometry, same WHERE predicates. Martin resolves a function source by signature and MapLibre
-- binds styles to the MVT layer tag and the attribute names, so any of those changing would not
-- error -- it would blank a layer while reporting success. Diff each body below against
-- `pg_get_functiondef` before applying: the only additions are a LIMIT, and in sensor_tiles a
-- DISTINCT ON with its ORDER BY.
--
--
-- ============================================================================================
-- THE PLAN THIS FILE WAS COMMISSIONED FROM WAS WRONG ABOUT FOUR OF THE FIVE. MEASURED 2026-08-21.
-- ============================================================================================
--
-- The commissioning analysis read the five identical function bodies, saw no LIMIT and no day
-- predicate, EXPLAINed them, and concluded that all five "legitimately match tens of thousands of
-- features" at low zoom over the Pacific Northwest cluster. The row counts refute that for four of
-- them. Production, published rows, whole layer -- not per tile, the WHOLE LAYER:
--
--     sensors ................ 186,904 rows      625 distinct locations    23 distinct days
--     evacuation-zones ...........  651 rows
--     burn-severity ..............  541 rows
--     fire-perimeters ............  177 rows
--     interventions ................. 0 rows
--
-- A LIMIT cannot bound a 177-row layer. Whatever makes `fire_risk_tiles` slow, it is not the
-- number of rows it returns, and a ceiling above 177 is arithmetically incapable of changing its
-- answer by one byte.
--
--
-- WHY THE EXPLAIN COSTS SAID OTHERWISE, AND WHY THEY ARE THE WRONG INSTRUMENT HERE.
--
-- Plain EXPLAIN prices all five identically -- 691,125.21 at z6/11/22 for fire-perimeters,
-- burn-severity AND evacuation-zones, to the hundredth of a cost unit -- because the estimate is
-- not a per-layer number at all. Every one of these functions selects its layer by NAME through a
-- join, so `f.layer_id` reaches the scan as a join variable and the planner has no per-layer
-- selectivity to apply; it estimates the whole tile envelope across all eleven layers and hands
-- the same 24,370 rows to each. drizzle/0030's header records the same fact from the other side:
-- that missing constant is why per-layer partial indexes are unusable here.
--
-- So the planner gives the SAME estimate to `intervention_tiles`, which has zero rows and answers
-- in 0.22 s, and to `sensor_tiles`, which has 186,904 and does not answer. An instrument that
-- cannot separate those two cannot be used to choose between them. The measurements below are
-- wall clock and tile BYTES against production, which can.
--
--
-- ============================================================================================
-- THE ONE REAL PROBLEM: geo.sensor_tiles SERVES A 14 MB TILE.
-- ============================================================================================
--
-- 625 stations x 23 days = 14,375 station-days, but the layer holds 186,904 published rows. The
-- multiplier is ~13 rows per station per day, up to 30 -- KMSO, KDLN and KGPI each published 30
-- separate features for 2026-08-06 alone. Every one of those 30 carries the IDENTICAL point
-- geometry. They are intra-day readings stored as separate features, and the tile draws all of
-- them stacked on one coordinate.
--
-- They are also all visible at once. src/lib/map/tile-layer-date-filter.ts filters these layers
-- with `observed_day <= selectedDate` -- cumulative "at or before", correct for a fire perimeter
-- that is still on the ground next year, and wrong for a weather reading. So the sensors layer at
-- the newest day renders the entire 23-day history simultaneously.
--
-- MEASURED, production, `length(ST_AsMVT(...))` of the live body versus this file's body:
--
--     tile        OLD features      OLD bytes        NEW features    NEW bytes     shrink
--     z0/0/0          186,904      14,258,826            10,000       753,432      18.9x
--     z4/2/5          174,989      13,356,704             9,992       752,533      17.7x
--     z5/5/11         165,145      12,623,165             9,433       712,070      17.7x
--     z6/11/22         47,842       3,920,849             2,753       224,256      17.5x
--     z6/11/23         32,152       2,642,890             1,872       155,649      17.0x
--
-- And confirmed end to end, by applying this whole file inside a transaction on production,
-- calling the replaced functions, and rolling back: `length(geo.sensor_tiles(0,0,0))` = 745,755,
-- `(6,11,22)` = 222,682, `(6,11,23)` = 154,704. Those run a few thousand bytes under the table
-- above because sensors ingests continuously and the two measurements are minutes apart -- which
-- is itself the point: the OLD number grows every day and the NEW one is pinned by the LIMIT.
--
-- 14.26 MB. One tile. One layer. Six dynamic layers ride ONE MapLibre source
-- (src/lib/map/sources.ts:33-40) declared `minzoom: 0`, so this tile is requested, and while it
-- is being built and streamed it is holding one of Martin's eight pool connections
-- (infra/martin/martin.yaml `pool_size: 8`) and the five layers that share the source wait on it.
--
--
-- ============================================================================================
-- THE BOUND: 10,000 FEATURES. WHY THAT NUMBER.
-- ============================================================================================
--
-- infra/martin/martin.yaml already declares `max_feature_count: 10000`. It is DEAD CONFIG for
-- these seven sources: Martin can count and truncate features in a tile it assembles itself from
-- a TABLE source, but a FUNCTION source hands it a finished `bytea` that it cannot inspect. The
-- number is therefore an operator's stated intent that nothing enforces. This file makes it real
-- in the only place it can be enforced -- the SQL -- and deliberately reuses the same number so
-- the config and the behaviour stop disagreeing. Changing one now means changing both.
--
-- 10,000 sensor features is ~753 KB of MVT. That is still a large tile; it is 18.9x smaller than
-- what production serves today, and it is a ceiling that cannot grow.
--
--
-- ============================================================================================
-- WHY A BARE LIMIT WOULD HAVE BEEN A BUG, NOT A BLUNT FIX.
-- ============================================================================================
--
-- These tiles are day-multiplexed: one tile carries every observation day, and the style filter
-- picks the day. A LIMIT with no ORDER BY takes whatever the index scan reaches first, which on
-- ix_features_layer_geom is spatial order -- uncorrelated with the day axis. So a bare LIMIT
-- deletes whole DAYS from a tile the time slider reads. Measured, same envelopes, bare
-- `LIMIT 10000` against this file's ordered DISTINCT ON:
--
--     tile        bare LIMIT: days kept   sensors kept    this file: days kept   sensors kept
--     z0/0/0            19 of 23             75 of 625          23 of 23         ~434 of 625
--     z6/11/22          18 of 19             80                 19 of 19          161
--
-- The bare LIMIT silently empties the map on four days out of twenty-three and keeps 12% of the
-- stations. That is a worse failure than the slow tile, because it looks like an answer.
--
-- So sensor_tiles gets two things instead:
--
--   1. DISTINCT ON (sensor_id, geom, observation_day). This is not thinning -- it is removing
--      exact overdraw. Thirty readings from KMSO on 2026-08-06 are thirty features at one
--      coordinate on one day; twenty-nine of them are painted underneath the thirtieth and can
--      never be seen or clicked. The ORDER BY tie-break keeps the LATEST `observedAt` of the day
--      (ISO-8601 text sorts chronologically -- the same property tile-layer-date-filter.ts
--      relies on), with `f.id DESC` as a total order so the choice is deterministic and a tile
--      re-rendered tomorrow is byte-identical. Measured: 47,842 -> 2,753 at z6/11/22 with all 19
--      of that envelope's days intact.
--
--      `sensor_id` is the semantic key and `geom` is redundant with it today -- distinct
--      (sensor_id, day) and distinct (geom, day) both count 2,753 in that envelope, so the
--      mapping is 1:1. `geom` is in the key anyway as a null guard: `properties ->> 'sensor_id'`
--      is NULL on 0 rows today, but DISTINCT ON treats NULLs as equal, so if an untagged row
--      ever lands, keying on sensor_id alone would collapse every untagged station in the tile
--      into one dot. With geom in the key they stay separate. Costs nothing, cannot mislead.
--
--   2. LIMIT 10000 applied AFTER that ORDER BY, so when it binds it truncates along the
--      sensor_id axis -- it drops whole STATIONS, never whole DAYS. The slider axis is the one
--      the map cannot afford to lose; the station axis degrades gracefully into a thinner but
--      complete map. This is measured, not asserted: at z0/0/0 and z4/2/5 the limit IS already
--      binding today (10,000 and 9,992 of a deduplicated 14,375) and all 23 days survive.
--
--      It binds today and will bind harder: the layer gains ~625 station-days every day, so the
--      deduplicated z0 set passes 20,000 around 2026-09-22. The truncation stays on the station
--      axis as it grows, which is the point of the ordering, but a thinning world-zoom map is a
--      symptom to watch, not a resting state. See the last section.
--
--
-- ============================================================================================
-- THE OTHER FOUR: THE LIMIT IS A NO-OP TODAY, AND SHIPS ANYWAY. SAY IT PLAINLY.
-- ============================================================================================
--
-- fire_risk_tiles (177), burn_severity_tiles (541), evacuation_zone_tiles (651) and
-- intervention_tiles (0) cannot reach 10,000. `LIMIT 10000` changes their output by nothing, at
-- any zoom, today. It ships as a ceiling that costs nothing and means a future ingest cannot turn
-- one of them into a second sensors without anyone noticing -- which is exactly how sensors got
-- here. It is NOT a fix for these four and must not be reported as one.
--
-- No ORDER BY accompanies their LIMIT, deliberately. An ORDER BY that never binds is a sort the
-- planner may still perform; ordering a bound that is arithmetically unreachable would cost real
-- time to prevent nothing. If any of these four ever approaches the ceiling, it needs the same
-- treatment sensor_tiles got here, chosen against its own data -- not a guess baked in now.
--
--
-- WHAT IS ACTUALLY EXPENSIVE ABOUT fire_risk_tiles AND burn_severity_tiles, SINCE IT IS NOT ROWS.
--
-- It is geometry size against a cold cache. Measured:
--
--     layer               rows    total vertices   max vertices   total geom bytes   max
--     burn-severity        541        2,341,323         82,547           37.5 MB    1.32 MB
--     fire-perimeters      177          726,438         60,447           11.7 MB    0.97 MB
--     evacuation-zones     651          151,367          2,305            2.4 MB    0.04 MB
--
-- Those geometries are TOASTed, and this database has 256 MB of shared_buffers against a 7.9 GB
-- geo.features on a 2 GB container. First touch of the z6/11/22 envelope: 10.88 s for
-- fire-perimeters, 28.43 s for burn-severity, just to read the geometries. Once warm, the SAME
-- tile query -- the live body, unmodified -- completes in 0.23 s and 0.42 s. The "no answer in
-- 120 s" that fire_risk_tiles showed over HTTP is a cold-read cost plus queueing behind
-- sensor_tiles on an 8-connection pool, not an unbounded result set.
--
--
-- ============================================================================================
-- CONSIDERED AND DELIBERATELY NOT IN THIS FILE.
-- ============================================================================================
--
-- LOW-ZOOM GEOMETRY GENERALIZATION. Simplifying to one MVT unit (360 / (2^z * 4096) degrees) in
-- 4326 before the transform was measured on production at z6/11/22 and is a real win. Both sides
-- of these three pairs were measured on an id+geom projection, so they are the GEOMETRY bytes
-- only and run below what the full functions emit (25,049 / 105,636 / 15,153 for the same tile);
-- the ratio is the number that carries. burn-severity 94,325 -> 27,405 bytes and 0.42 s ->
-- 0.16 s; fire-perimeters 24,538 -> 7,547; evacuation-zones 11,900 -> 5,695. It is not here for
-- three reasons. It does not touch the cold
-- TOAST read, which is the cost that actually hurts -- the full geometry must be read before it
-- can be simplified. It changes rendered geometry, which is the one thing this file promised not
-- to change. And ST_Simplify can return an invalid polygon; ST_SimplifyPreserveTopology cannot,
-- but costs 3x (0.44 s vs 0.16 s) and the choice deserves its own review, not a footnote in a
-- file whose subject is result-set bounds.
--
-- THE WATERSHED TEMPLATE. geo.watershed_tiles branches on a target_level and reads
-- geo.watershed_rollup below z10, which is why it costs 275 at z6/11/23 where its siblings cost
-- 309,746. It is the right shape and it is not reproducible here without the rollup table that
-- makes it work. A sensors equivalent needs a table keyed (observation_day, cell) holding one row
-- per station-day with a pre-reduced geometry, plus a producer and a refresh -- a layer-lane
-- change, not a function rewrite. This file's DISTINCT ON is the same reduction computed on the
-- fly: it produces the right ANSWER but still reads all 47,842 rows to get there, so it removes
-- 17.5x of output and 0 % of the input scan. That residual scan is the reason this file is a
-- floor and not a finish line.
--
-- A DAY PREDICATE. The obvious way to cut the input scan is to stop carrying 23 days in one tile.
-- It cannot be done in the SQL alone: the style filter is `observed_day <= selectedDate`, so a
-- tile carrying only recent days renders NOTHING when the slider is dragged back. Bounding the
-- day axis requires changing that filter or putting the day in the tile path, and both are client
-- changes outside this file.
--
--
-- ============================================================================================
-- EXPLAIN EVIDENCE, AS COMMISSIONED. IT DOES NOT MOVE, AND THAT IS THE FINDING.
-- ============================================================================================
--
-- Plain EXPLAIN, production, inner query with the real attribute list, OLD body vs this file's:
--
--     z6/11/22                    OLD cost      est rows     NEW cost      est rows    ratio
--     sensor_tiles              691,307.98        24,370   514,901.93        10,000    1.34x
--     fire_risk_tiles           691,125.21        24,370   283,596.96        10,000    2.44x
--     burn_severity_tiles       691,125.21        24,370   283,596.96        10,000    2.44x
--     evacuation_zone_tiles     691,125.21        24,370   283,596.96        10,000    2.44x
--     intervention_tiles        684,910.85        24,370   281,046.96        10,000    2.44x
--
--     z6/11/23                    OLD cost      est rows     NEW cost      est rows    ratio
--     sensor_tiles              312,732.74        11,018   305,381.13        10,000    1.02x
--     fire_risk_tiles           312,650.11        11,018   283,763.07        10,000    1.10x
--     burn_severity_tiles       312,650.11        11,018   283,763.07        10,000    1.10x
--     evacuation_zone_tiles     312,650.11        11,018   283,763.07        10,000    1.10x
--     intervention_tiles        309,840.52        11,018   281,213.07        10,000    1.10x
--
-- (The OLD numbers run slightly above the commissioning brief's 684,716 / 309,746 because those
-- were measured on an id+geom projection; these carry each function's real attribute list.)
--
-- Read honestly: the estimated cost barely moves, and for sensor_tiles at z6/11/23 it moves by
-- 2 %. That is not evidence the change is ineffective -- it is the same instrument failure
-- described above. The four estimates that "improve" 2.44x belong to the four functions this file
-- provably does not change at all, and the one function it does change scores worst. The cost
-- model cannot see the 47,842 duplicate rows because it never estimated them, and it cannot see
-- 14 MB of output because output size is not in its objective. The numbers that moved are rows
-- returned (186,904 -> 10,000) and bytes served (14,258,826 -> 753,432), both measured by
-- execution.
--
--
-- ============================================================================================
-- APPLYING THIS FILE.
-- ============================================================================================
--
-- It ships DORMANT: drizzle/meta/_journal.json is NOT touched, matching 0030 and everything after
-- it. Hand-apply one function at a time. `CREATE OR REPLACE FUNCTION` takes a lock on the
-- function's pg_proc row, and Martin calls all five constantly, so the lock_timeout below is
-- there to make a conflict fail in five seconds instead of parking behind a 120 s tile.
--
-- MARTIN PROBABLY DOES NOT NEED A RESTART FOR THIS, AND THAT IS A PREDICTION, NOT A MEASUREMENT.
-- Nothing in the catalogue changes -- same names, same signatures -- Martin issues
-- `SELECT geo.<fn>($1,$2,$3)` per request, CREATE OR REPLACE keeps the same OID, and PostgreSQL
-- invalidates cached plans when a function is redefined. So the next uncached tile should run the
-- new body, with Martin's own `tile_expiry: 5m` as the only propagation delay. VERIFY IT RATHER
-- THAN ASSUME IT: wait out five minutes, then fetch with
-- `-H "Origin: https://plantgeo-main-production.up.railway.app"` and check the response SIZE.
-- z0/0/0 of sensor_tiles must come back near 750 KB. If it is still ~14 MB after the cache
-- expiry, the prediction was wrong and Martin needs the restart -- do not conclude the migration
-- failed to apply, check `pg_get_functiondef` first.
--
-- AFTER THIS FILE, THE SENSORS LANE IS STILL NOT FINISHED. It serves a 753 KB world tile built by
-- scanning 186,904 rows, and it grows by 625 rows a day. The durable fix is a station-day rollup
-- behind a target_level branch, exactly as geo.watershed_rollup is, or moving the day out of the
-- tile body and into the tile path. This file buys the headroom to do that deliberately.


SET lock_timeout = '5s';
--> statement-breakpoint

SET statement_timeout = '60s';
--> statement-breakpoint

-- The assertions. Every one of them guards a way this file could appear to apply and quietly do
-- the wrong thing, in the drizzle/0030 style: existence alone is never the question.
DO $$
DECLARE
  expected_names constant text[] := ARRAY[
    'sensor_tiles', 'fire_risk_tiles', 'burn_severity_tiles',
    'evacuation_zone_tiles', 'intervention_tiles'
  ];
  offending text;
BEGIN
  -- 1. All five exist with EXACTLY the signature Martin resolves. A function that differs in
  --    argument types or return type is a DIFFERENT function: CREATE OR REPLACE would create a
  --    second overload beside the live one, leave the live one serving, and report success.
  SELECT string_agg(n, ', ' ORDER BY n) INTO offending
    FROM unnest(expected_names) AS n
   WHERE to_regprocedure('geo.' || n || '(integer, integer, integer)') IS NULL;

  IF offending IS NOT NULL THEN
    RAISE EXCEPTION
      'geo.%(integer, integer, integer) is missing -- 0038 replaces tile functions, it does not '
      'create them. Apply the migrations that create them first (0005, 0009, 0012, 0015) and '
      're-check that this database is the one Martin reads.', offending;
  END IF;

  -- 2. Return type, volatility, parallel safety and search_path all match what is being written
  --    below. Martin's catalogue check accepts a function returning bytea from (int,int,int);
  --    losing PARALLEL SAFE or the search_path pin would not fail here, it would change plans
  --    and resolution at runtime, on production, with no error anywhere.
  SELECT string_agg(p.proname, ', ' ORDER BY p.proname) INTO offending
    FROM pg_proc AS p
    JOIN pg_namespace AS ns ON ns.oid = p.pronamespace
   WHERE ns.nspname = 'geo'
     AND p.proname = ANY (expected_names)
     AND p.pronargs = 3
     AND (
          pg_get_function_result(p.oid) <> 'bytea'
       OR p.provolatile <> 's'
       OR p.proparallel <> 's'
       OR p.proconfig IS DISTINCT FROM ARRAY['search_path=public, pg_catalog']
     );

  IF offending IS NOT NULL THEN
    RAISE EXCEPTION
      'geo.% does not have the properties 0038 preserves (expected RETURNS bytea, STABLE, '
      'PARALLEL SAFE, SET search_path = public, pg_catalog). Something changed these out of band; '
      'reconcile before replacing the bodies, because this file would silently restore them.',
      offending;
  END IF;

  -- 3. geo.feature_observation_day must be IMMUTABLE and return date. sensor_tiles below puts it
  --    in a DISTINCT ON key and in the matching ORDER BY. If it were VOLATILE the two call sites
  --    could disagree within one row, and the deduplication would keep a different reading than
  --    the one it sorted -- silently, per tile.
  IF to_regprocedure('geo.feature_observation_day(jsonb)') IS NULL THEN
    RAISE EXCEPTION
      'geo.feature_observation_day(jsonb) is missing; 0015_tile_observation_day must be applied '
      'before 0038.';
  END IF;

  PERFORM 1
     FROM pg_proc AS p
    WHERE p.oid = to_regprocedure('geo.feature_observation_day(jsonb)')
      AND p.provolatile = 'i'
      AND pg_get_function_result(p.oid) = 'date';

  IF NOT FOUND THEN
    RAISE EXCEPTION
      'geo.feature_observation_day(jsonb) is not IMMUTABLE-returning-date. sensor_tiles uses it '
      'as a DISTINCT ON key below and needs a stable value per row.';
  END IF;

  -- 4. The access path these bodies assume. Not a correctness precondition -- the functions
  --    return the same bytes without it -- but replacing five tile bodies while the composite
  --    GiST index is invalid means measuring the rewrite against drizzle/0030's 45-second plan
  --    and drawing the wrong conclusion from it. COALESCE, not a bare NOT: a name that resolves
  --    to something other than an index leaves these NULL and a bare NOT would pass.
  PERFORM 1
     FROM pg_index AS i
    WHERE i.indexrelid = to_regclass('geo.ix_features_layer_geom')
      AND COALESCE(i.indisvalid, false)
      AND COALESCE(i.indisready, false);

  IF NOT FOUND THEN
    RAISE EXCEPTION
      'geo.ix_features_layer_geom is missing or unusable (see drizzle/0030). Restore it before '
      'applying 0038, or the tile timings measured after this change will be measuring the '
      'missing index instead of this file.';
  END IF;
END $$;
--> statement-breakpoint

-- geo.sensor_tiles -- the only body in this file whose answer changes.
--
-- DISTINCT ON collapses the ~13-to-30 same-day readings each station publishes at one identical
-- coordinate; the ORDER BY tie-break keeps the latest reading of that day, deterministically.
-- LIMIT 10000 then sits above an ORDER BY led by sensor_id, so truncation drops whole stations
-- and never whole days -- which matters because src/lib/map/tile-layer-date-filter.ts filters
-- these features by `observed_day <= selectedDate` and a missing day is a blank map.
--
-- Measured: z0/0/0 186,904 features / 14,258,826 bytes -> 10,000 features / 753,432 bytes, all
-- 23 observation days retained. See this file's header for the full table.
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
--> statement-breakpoint

-- geo.fire_risk_tiles -- ceiling only. 177 published rows layer-wide, so this LIMIT is
-- unreachable and the emitted tile is byte-identical to today's. It exists so a future ingest
-- cannot turn this layer into a second sensors unobserved.
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
    LIMIT 10000
  ) AS tile;

  RETURN mvt;
END;
$$;
--> statement-breakpoint

-- geo.burn_severity_tiles -- ceiling only. 541 published rows layer-wide; unreachable, so the
-- emitted tile is byte-identical to today's. This layer's real cost is its geometry (2,341,323
-- vertices across 541 rows, 37.5 MB, cold-read at 28.4 s), which a row bound cannot touch --
-- see the header's "considered and deliberately not in this file".
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
    LIMIT 10000
  ) AS tile;

  RETURN mvt;
END;
$$;
--> statement-breakpoint

-- geo.evacuation_zone_tiles -- ceiling only. 651 published rows layer-wide; unreachable, so the
-- emitted tile is byte-identical to today's.
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
    LIMIT 10000
  ) AS tile;

  RETURN mvt;
END;
$$;
--> statement-breakpoint

-- geo.intervention_tiles -- ceiling only. 0 published rows; this function returns an empty tile
-- today and will return an empty tile after this file. It is here for uniformity, so that no
-- geo.features-backed tile function in this schema is left without a bound.
CREATE OR REPLACE FUNCTION geo.intervention_tiles(z integer, x integer, y integer)
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
--> statement-breakpoint

COMMENT ON FUNCTION geo.sensor_tiles(integer, integer, integer) IS
  'Sensor MVT tiles, deduplicated to one feature per (sensor_id, location, observation day) and '
  'capped at 10,000 features. Each station publishes ~13-30 same-day readings at one identical '
  'coordinate, so before drizzle/0038 the z0 tile carried 186,904 features / 14.3 MB, of which '
  'all but ~14,375 were exact overdraw; it is now 10,000 features / 753 KB with all 23 observation '
  'days intact. The LIMIT sits above an ORDER BY led by sensor_id ON PURPOSE: when it binds it '
  'drops whole STATIONS, never whole DAYS, because src/lib/map/tile-layer-date-filter.ts filters '
  'these features by observed_day <= selectedDate and a missing day blanks the map for that day. '
  'Do not replace this with a bare LIMIT -- measured, a bare LIMIT 10000 at z0/0/0 keeps 19 of 23 '
  'days. See drizzle/0038_tile_low_zoom_routing.sql.';
