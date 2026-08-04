-- One-shot backfill of geo.features into the Type-2 geometry dimension.
--
-- Run by hand, once, after drizzle/0008_geometry_dimension.sql has been applied:
--
--   & "C:\Program Files\PostgreSQL\16\bin\psql.exe" -v ON_ERROR_STOP=1 \
--       -f scripts/backfill-geometry.sql "$env:DATABASE_URL"
--
-- To rehearse, change the final COMMIT to ROLLBACK and read the NOTICE output.
-- The script is idempotent: a second run inserts 0 rows and repoints 0 features.
-- Rationale lives in src/lib/server/db/AGENTS.md, section geometry-dimension.
--
-- Requires PostgreSQL 16 or newer for pg_input_is_valid(); production is 18.4 and
-- the local rehearsal container is 16.
\set ON_ERROR_STOP on

-- REPEATABLE READ *and* an explicit SHARE lock. The isolation level alone is not enough:
-- it only turns a write-write collision into a serialization failure, while rows another
-- transaction INSERTs after this snapshot are simply invisible, with no error at all. The
-- hourly ingest could therefore slip a batch past the census, the insert and the closing
-- assertion alike, and this script would print success over features left holding a null
-- geometry_id. SHARE blocks writers while still allowing readers, so the snapshot is
-- genuinely quiescent and a colliding cron run waits instead of vanishing.
--
-- Order matters, and it is the lock that has to come first. SET and LOCK TABLE are the two
-- utility statements that take no snapshot, so the REPEATABLE READ snapshot is not taken
-- until the first statement below them -- by which point the lock is already held.
-- lock_timeout bounds the wait; a timeout aborts the transaction, which is safe, and the
-- script is idempotent, so the remedy is always just to run it again.
BEGIN ISOLATION LEVEL REPEATABLE READ;
SET LOCAL lock_timeout = '30s';
LOCK TABLE geo.features, geo.geometry IN SHARE MODE;

-- Layer name -> producer token, as a literal list so it is reviewable rather than a
-- string transform. The tokens are lifted verbatim from PRODUCER_BY_LAYER_NAME in
-- services/agri-data-service/src/agri_data_service/ingest/identity.py, which is the only
-- place allowed to mint an identity. A layer holding rows but missing from this list
-- aborts the transaction below; it never falls back to the layer name.
CREATE TEMP TABLE backfill_producer (
  layer_name text PRIMARY KEY,
  producer   text NOT NULL
) ON COMMIT DROP;
INSERT INTO backfill_producer (layer_name, producer) VALUES
  ('fire-detections',      'firms'),
  ('water-gauges',         'usgs-nwis'),
  ('weather-observations', 'open-meteo'),
  ('fire-perimeters',      'wfigs');

-- 1. Census FIRST, inside this transaction. It is the assertion's left-hand side, not a
--    preflight to run and eyeball. No row count is ever written as a literal: ingestion
--    runs hourly, so any number recorded in a file is stale within hours.
CREATE TEMP TABLE backfill_census ON COMMIT DROP AS
SELECT feature_counts.*,
       (SELECT count(*) FROM geo.geometry) AS geometry_rows_before,
       (
         SELECT count(*)
         FROM geo.features f
         JOIN geo.layers l ON l.id = f.layer_id
         JOIN backfill_producer p ON p.layer_name = l.name
         JOIN geo.geometry g
           ON g.natural_key = p.producer || ':' || (f.properties ->> 'id')
          AND g.version_valid_to IS NULL
         WHERE f.properties ? 'id' AND f.geom IS NOT NULL
       ) AS already_versioned
FROM (
  SELECT count(*) FILTER (WHERE properties ? 'id' AND geom IS NOT NULL) AS eligible,
         count(*) FILTER (WHERE NOT (properties ? 'id'))                AS no_natural_key,
         count(*) FILTER (WHERE geom IS NULL)                           AS no_geometry,
         count(*)                                                       AS total
  FROM geo.features
) AS feature_counts;

CREATE TEMP TABLE backfill_result (step text PRIMARY KEY, measured_rows bigint NOT NULL)
  ON COMMIT DROP;

-- 2. Guards. Each one aborts rather than degrading: a silent fallback here mints a
--    fabricated version chain that looks entirely plausible afterwards.
DO $guard$
DECLARE
  unmapped_layers     text;
  unmapped_geom_kinds text;
  duplicate_keys      bigint;
BEGIN
  SELECT string_agg(DISTINCT l.name, ', ') INTO unmapped_layers
  FROM geo.features f
  JOIN geo.layers l ON l.id = f.layer_id
  LEFT JOIN backfill_producer p ON p.layer_name = l.name
  WHERE p.layer_name IS NULL
    AND f.properties ? 'id'
    AND f.geom IS NOT NULL;
  IF unmapped_layers IS NOT NULL THEN
    RAISE EXCEPTION 'backfill: layer(s) hold eligible rows but have no producer token: %',
      unmapped_layers
      USING HINT = 'Add the layer to identity.PRODUCER_BY_LAYER_NAME and to the VALUES list in this script. Never fall back to the layer name.';
  END IF;

  SELECT string_agg(DISTINCT GeometryType(f.geom), ', ') INTO unmapped_geom_kinds
  FROM geo.features f
  WHERE f.properties ? 'id'
    AND f.geom IS NOT NULL
    AND GeometryType(f.geom) NOT IN
        ('POINT','MULTIPOINT','POLYGON','MULTIPOLYGON','LINESTRING','MULTILINESTRING');
  IF unmapped_geom_kinds IS NOT NULL THEN
    RAISE EXCEPTION 'backfill: geometry type(s) with no geom_kind mapping: %',
      unmapped_geom_kinds
      USING HINT = 'Extend the CASE below and ck_geometry_kind together, in that order.';
  END IF;

  SELECT count(*) INTO duplicate_keys FROM (
    SELECT p.producer || ':' || (f.properties ->> 'id') AS natural_key
    FROM geo.features f
    JOIN geo.layers l ON l.id = f.layer_id
    JOIN backfill_producer p ON p.layer_name = l.name
    WHERE f.properties ? 'id' AND f.geom IS NOT NULL
    GROUP BY 1
    HAVING count(*) > 1
  ) AS collisions;
  IF duplicate_keys > 0 THEN
    RAISE EXCEPTION 'backfill: % natural_key value(s) are claimed by more than one feature',
      duplicate_keys
      USING HINT = 'Two producers sharing a key interleave into one version chain. Resolve before backfilling.';
  END IF;
END
$guard$;

-- 3. Every eligible feature becomes v1: current, open-ended.
--
--    version_valid_from reads the producer's own observation property. Never created_at
--    (it is "last touched", and every row reads as written today), and never now() (it
--    would date all of history to backfill day, so scrubbing the slider one day back
--    would blank the entire map at once). An absent or unparseable value falls back to
--    '-infinity', which states "true for as long as we know" and matches every past
--    as-of position instead of aborting the transaction. That backstop is a genuine last
--    resort and fires on 0 of 19,113 rows today: it claims a row was already true at every
--    past slider position, which is a real answer only where no better one exists in the
--    payload. Reach for a second property from the same blob before reaching for it.
WITH eligible AS (
  SELECT p.producer || ':' || (f.properties ->> 'id') AS natural_key,
         CASE
           WHEN observed.observed_text IS NOT NULL
            AND pg_input_is_valid(observed.observed_text, 'timestamptz')
             THEN observed.observed_text::timestamptz
           ELSE '-infinity'::timestamptz
         END AS version_valid_from,
         -- Explicit CASE, never replace(GeometryType(...),'MULTI',''): that yields
         -- 'linestring', which ck_geometry_kind rejects.
         CASE GeometryType(f.geom)
           WHEN 'POINT'           THEN 'point'
           WHEN 'MULTIPOINT'      THEN 'point'
           WHEN 'POLYGON'         THEN 'polygon'
           WHEN 'MULTIPOLYGON'    THEN 'polygon'
           WHEN 'LINESTRING'      THEN 'line'
           WHEN 'MULTILINESTRING' THEN 'line'
         END AS geom_kind,
         f.geom              AS geom,
         ST_Centroid(f.geom) AS centroid,
         p.producer          AS producer
  FROM geo.features f
  JOIN geo.layers l ON l.id = f.layer_id
  JOIN backfill_producer p ON p.layer_name = l.name
  CROSS JOIN LATERAL (
    SELECT CASE l.name
             WHEN 'fire-detections'      THEN f.properties ->> 'observedAt'
             WHEN 'water-gauges'         THEN f.properties ->> 'updatedAt'
             WHEN 'weather-observations' THEN f.properties ->> 'observedAt'
             -- 14 of 112 perimeters carry a JSON null polygonDateTime. A perimeter cannot
             -- predate the discovery of its own fire, so fireDiscoveryDateTime is a
             -- defensible lower bound and all 14 carry a parseable one; without it those
             -- rows would render as active at every past slider position, permanently,
             -- because version_valid_from is never rewritten once a fact cites the row.
             WHEN 'fire-perimeters'      THEN coalesce(f.properties ->> 'polygonDateTime',
                                                       f.properties ->> 'fireDiscoveryDateTime')
           END AS observed_text
  ) AS observed
  WHERE f.properties ? 'id'
    AND f.geom IS NOT NULL
),
inserted AS (
  INSERT INTO geo.geometry (natural_key, version_valid_from, version_valid_to,
                            geom_kind, geom, centroid, producer)
  SELECT natural_key, version_valid_from, NULL, geom_kind, geom, centroid, producer
  FROM eligible
  ON CONFLICT (natural_key) WHERE version_valid_to IS NULL DO NOTHING
  RETURNING version_valid_to
),
tally AS MATERIALIZED (
  SELECT count(*)                                             AS inserted_rows,
         count(*) FILTER (WHERE version_valid_to IS NOT NULL) AS not_current_rows
  FROM inserted
)
INSERT INTO backfill_result (step, measured_rows)
SELECT 'geometry_v1_inserted', inserted_rows FROM tally
UNION ALL
SELECT 'geometry_v1_not_current', not_current_rows FROM tally;

-- 4. Point geo.features at the CURRENT version of its place. geo.features is current
--    state, refreshed in place, so this column is repointed whenever a version closes --
--    that repoint is the ingest path's job, not this script's.
WITH repointed AS (
  UPDATE geo.features f
  SET    geometry_id = g.geometry_id
  FROM   geo.layers l, backfill_producer p, geo.geometry g
  WHERE  l.id = f.layer_id
    AND  p.layer_name = l.name
    AND  g.natural_key = p.producer || ':' || (f.properties ->> 'id')
    AND  g.version_valid_to IS NULL
    AND  f.properties ? 'id'
    AND  f.geom IS NOT NULL
    AND  f.geometry_id IS DISTINCT FROM g.geometry_id
  RETURNING f.id
)
INSERT INTO backfill_result (step, measured_rows)
SELECT 'features_geometry_id_repointed', count(*) FROM repointed;

-- 5. Assert against the census captured in step 1, then commit. Every mismatch raises,
--    which rolls the whole transaction back.
DO $assert$
DECLARE
  census               backfill_census%ROWTYPE;
  inserted_rows        bigint;
  not_current_rows     bigint;
  repointed_rows       bigint;
  expected_inserts     bigint;
  unresolved_eligible  bigint;
  features_without_id  bigint;
  namespace_violations bigint;
  chains_two_producers bigint;
  fanout               record;
BEGIN
  SELECT * INTO census FROM backfill_census;
  SELECT measured_rows INTO inserted_rows
    FROM backfill_result WHERE step = 'geometry_v1_inserted';
  SELECT measured_rows INTO not_current_rows
    FROM backfill_result WHERE step = 'geometry_v1_not_current';
  SELECT measured_rows INTO repointed_rows
    FROM backfill_result WHERE step = 'features_geometry_id_repointed';

  RAISE NOTICE 'census: total=% eligible=% no_natural_key=% no_geometry=% geometry_rows_before=% already_versioned=%',
    census.total, census.eligible, census.no_natural_key, census.no_geometry,
    census.geometry_rows_before, census.already_versioned;
  RAISE NOTICE 'result: v1_inserted=% features_repointed=%', inserted_rows, repointed_rows;

  expected_inserts := census.eligible - census.already_versioned;
  IF inserted_rows <> expected_inserts THEN
    RAISE EXCEPTION 'backfill: inserted % v1 rows, expected % (eligible % minus already_versioned %)',
      inserted_rows, expected_inserts, census.eligible, census.already_versioned;
  END IF;

  IF not_current_rows <> 0 THEN
    RAISE EXCEPTION 'backfill: % inserted row(s) are not open-ended; every backfilled row is v1',
      not_current_rows;
  END IF;

  SELECT count(*) INTO unresolved_eligible
  FROM geo.features
  WHERE properties ? 'id' AND geom IS NOT NULL AND geometry_id IS NULL;
  IF unresolved_eligible <> 0 THEN
    RAISE EXCEPTION 'backfill: % eligible feature(s) resolved no geometry_id', unresolved_eligible;
  END IF;

  SELECT count(*) INTO features_without_id FROM geo.features WHERE geometry_id IS NULL;
  IF features_without_id <> census.total - census.eligible THEN
    RAISE EXCEPTION 'backfill: % feature(s) carry no geometry_id, expected % (total % minus eligible %)',
      features_without_id, census.total - census.eligible, census.total, census.eligible;
  END IF;

  SELECT count(*) INTO namespace_violations
  FROM geo.geometry WHERE natural_key NOT LIKE producer || ':%';
  IF namespace_violations <> 0 THEN
    RAISE EXCEPTION 'backfill: % natural_key(s) are not namespaced by their producer',
      namespace_violations;
  END IF;

  SELECT count(*) INTO chains_two_producers FROM (
    SELECT natural_key FROM geo.geometry
    GROUP BY natural_key HAVING count(DISTINCT producer) > 1
  ) AS interleaved;
  IF chains_two_producers <> 0 THEN
    RAISE EXCEPTION 'backfill: % version chain(s) mix more than one producer',
      chains_two_producers;
  END IF;

  -- Fan-out report, deliberately a NOTICE and never an assertion. Three producers put the
  -- observation itself inside the producer-local id, so their natural_key names one reading
  -- rather than a durable place and their chains are v1-only by contract -- usgs-nwis sits
  -- near 12 keys per place and open-meteo near 17. Aborting on that ratio would block a
  -- backfill for a condition the identity contract accepts; printing it keeps the number in
  -- front of whoever next reads the growth story. See db/AGENTS.md, section
  -- geometry-dimension, "three producers are v1-only".
  FOR fanout IN
    SELECT g.producer                          AS producer,
           count(DISTINCT g.natural_key)       AS keys,
           count(DISTINCT ST_AsBinary(g.geom)) AS places
    FROM geo.geometry g
    GROUP BY g.producer
    ORDER BY g.producer
  LOOP
    RAISE NOTICE 'fan-out: producer=% natural_keys=% distinct_places=%',
      fanout.producer, fanout.keys, fanout.places;
  END LOOP;

  RAISE NOTICE 'backfill: all assertions passed';
END
$assert$;

COMMIT;
