-- One-shot re-key of geo.geometry from the OBSERVATION key onto the ENTITY key.
--
-- Run by hand, once, in the same deploy that ships the `geometry_key_for` change:
--
--   & "C:\Program Files\PostgreSQL\16\bin\psql.exe" -v ON_ERROR_STOP=1 \
--       -f scripts/rekey-geometry-to-entity.sql "$env:DATABASE_URL"
--
-- To rehearse, change the final COMMIT to ROLLBACK and read the NOTICE output.
-- The script is idempotent: a second run collapses 0 chains and repoints 0 features.
-- Rationale lives in services/agri-data-service/src/agri_data_service/ingest/AGENTS.md,
-- section "geometry.py: the dimension is keyed by the place, not by the observation".
--
-- WHY. `scripts/backfill-geometry.sql` seeded every row as `producer || ':' || (properties->>'id')`,
-- which for `usgs-nwis` is `siteNo:updatedAt` and for `open-meteo` is `lat:lon:observedAt` -- a
-- different key on every reading. `identity.FeatureIdentity.entity_key` is the key the dimension was
-- meant to carry ("the enduring place this observation was taken at, not the observation"), and
-- `geometry_key_for` now returns it. Without this script the table would hold BOTH key shapes for the
-- same producer, which is the interleaving hazard wearing a new costume.
--
-- Measured on production 2026-08-04, read-only, before writing this script:
--   producer     rows    keys    entities  closed  entities_with_more_than_one_shape
--   firms        6297    6297    6297      0       0
--   open-meteo   2787    2787    116       0       0
--   usgs-nwis   14494   14494    899       0       0
--   wfigs         112     112    112       0       0
-- Not one chain in the whole dimension has ever reached a second version, and no entity holds more
-- than one distinct shape -- so collapsing a group to its earliest row loses no history that exists.
-- The guard below re-measures that rather than trusting these numbers, which go stale hourly.
\set ON_ERROR_STOP on

-- Same locking discipline, and for the same reason, as scripts/backfill-geometry.sql: REPEATABLE READ
-- alone would let a concurrent cron INSERT rows that are simply invisible to this snapshot, so the
-- census, the collapse and the closing assertion would all agree with each other and all be wrong.
-- LOCK TABLE takes no snapshot, so it must come before the first real statement.
BEGIN ISOLATION LEVEL REPEATABLE READ;
SET LOCAL lock_timeout = '30s';
LOCK TABLE geo.features, geo.geometry IN SHARE MODE;

-- 1. The entity key per existing row, derived by splitting the seeded key on the producer's own
--    separator count. Never a regexp and never a LIKE: the two producers that need collapsing put a
--    fixed number of fields ahead of the observation, so `split_part` names them exactly.
--      usgs-nwis:<siteNo>:<updatedAt>            -> fields 1..2
--      open-meteo:<lat>:<lon>:<observedAt>       -> fields 1..3
--    Every other producer is already entity-keyed (entity_local_id is None for firms/wfigs/mtbs/usdm,
--    so entity_key == natural_key by construction) and is carried through unchanged.
CREATE TEMP TABLE rekey_row ON COMMIT DROP AS
SELECT g.geometry_id,
       g.natural_key,
       g.producer,
       g.version_valid_from,
       g.version_valid_to,
       CASE g.producer
         WHEN 'usgs-nwis'  THEN split_part(g.natural_key, ':', 1) || ':' ||
                                split_part(g.natural_key, ':', 2)
         WHEN 'open-meteo' THEN split_part(g.natural_key, ':', 1) || ':' ||
                                split_part(g.natural_key, ':', 2) || ':' ||
                                split_part(g.natural_key, ':', 3)
         ELSE g.natural_key
       END AS entity_key
FROM geo.geometry g;

CREATE INDEX ON rekey_row (entity_key);

-- 2. The survivor per entity: the EARLIEST observation, because that is when the dimension first knew
--    this place to hold this shape. Taking the latest instead would silently restate every place's
--    history as beginning today. geometry_id breaks a tie deterministically so a rerun picks the same
--    row. `version_valid_from` is carried across untouched -- this script never rewrites an instant.
CREATE TEMP TABLE rekey_survivor ON COMMIT DROP AS
SELECT DISTINCT ON (entity_key)
       entity_key,
       geometry_id AS survivor_id,
       natural_key AS survivor_natural_key,
       version_valid_from
FROM rekey_row
ORDER BY entity_key, version_valid_from, geometry_id;

CREATE UNIQUE INDEX ON rekey_survivor (entity_key);
CREATE INDEX ON rekey_survivor (survivor_id);

CREATE TEMP TABLE rekey_result (step text PRIMARY KEY, measured_rows bigint NOT NULL) ON COMMIT DROP;

-- 3. Guards. Each aborts rather than degrading, because a wrong collapse fabricates a version chain
--    that renders correctly and is far harder to detect afterwards than a duplicate row.
DO $guard$
DECLARE
  entities_many_shapes bigint;
  closed_versions      bigint;
  entities_two_makers  bigint;
  namespace_violations bigint;
  overlong_keys        bigint;
BEGIN
  -- 3a. Collapsing is only lossless where every row of an entity holds the SAME shape. A group with
  --     two shapes is a genuine relocation that needs a real supersession chain, which is a data
  --     decision, not a re-key; refuse and let a human build it.
  SELECT count(*) INTO entities_many_shapes FROM (
    SELECT r.entity_key
    FROM rekey_row r
    JOIN geo.geometry g ON g.geometry_id = r.geometry_id
    GROUP BY r.entity_key
    HAVING count(DISTINCT ST_AsBinary(g.geom)) > 1
  ) AS moved;
  IF entities_many_shapes > 0 THEN
    RAISE EXCEPTION 're-key: % entity/entities hold more than one distinct shape', entities_many_shapes
      USING HINT = 'Those places genuinely moved. Build their supersession chain deliberately; do not collapse them here.';
  END IF;

  -- 3b. This script only ever deletes rows it proves are redundant. A closed version carries real
  --     Type-2 history and a `superseded_by` edge, so its presence means the assumption this script
  --     is built on (nothing has ever been superseded) no longer holds.
  SELECT count(*) INTO closed_versions FROM geo.geometry WHERE version_valid_to IS NOT NULL;
  IF closed_versions > 0 THEN
    RAISE EXCEPTION 're-key: % version(s) are already closed', closed_versions
      USING HINT = 'This collapse assumes a v1-only dimension. Reconcile the existing chains by hand first.';
  END IF;

  -- 3c. Two producers folding onto one entity key IS the interleaving hazard. It cannot happen while
  --     the key stays producer-namespaced, so assert it rather than assume it.
  SELECT count(*) INTO entities_two_makers FROM (
    SELECT entity_key FROM rekey_row GROUP BY entity_key HAVING count(DISTINCT producer) > 1
  ) AS interleaved;
  IF entities_two_makers > 0 THEN
    RAISE EXCEPTION 're-key: % entity key(s) are claimed by more than one producer', entities_two_makers
      USING HINT = 'Two producers sharing a key interleave into one version chain. Resolve before re-keying.';
  END IF;

  SELECT count(*) INTO namespace_violations
  FROM rekey_row WHERE entity_key NOT LIKE producer || ':%';
  IF namespace_violations > 0 THEN
    RAISE EXCEPTION 're-key: % entity key(s) are not namespaced by their producer', namespace_violations
      USING HINT = 'ck_geometry_natural_key_namespaced would reject these. Fix the derivation, not the constraint.';
  END IF;

  SELECT count(*) INTO overlong_keys FROM rekey_row WHERE length(entity_key) > 255;
  IF overlong_keys > 0 THEN
    RAISE EXCEPTION 're-key: % entity key(s) exceed the 255-character ceiling', overlong_keys;
  END IF;
END
$guard$;

-- 4. Census, inside this transaction, as the assertion's left-hand side. No count is ever a literal.
CREATE TEMP TABLE rekey_census ON COMMIT DROP AS
SELECT (SELECT count(*) FROM geo.geometry)                                        AS geometry_rows_before,
       (SELECT count(*) FROM rekey_survivor)                                      AS entities,
       (SELECT count(*) FROM rekey_row r
          WHERE r.geometry_id NOT IN (SELECT survivor_id FROM rekey_survivor))     AS redundant_rows,
       (SELECT count(*) FROM geo.features WHERE geometry_id IS NOT NULL)          AS linked_features_before,
       (SELECT count(*) FROM geo.features WHERE geometry_id IS NULL)              AS unlinked_features_before;

-- 5. Repoint geo.features FIRST, while the rows it points at still exist. Doing this after the delete
--    would either trip features_geometry_id_fkey or, worse under a nullifying rule, blank the column.
WITH repointed AS (
  UPDATE geo.features f
  SET    geometry_id = s.survivor_id
  FROM   rekey_row r
  JOIN   rekey_survivor s ON s.entity_key = r.entity_key
  WHERE  f.geometry_id = r.geometry_id
    AND  f.geometry_id IS DISTINCT FROM s.survivor_id
  RETURNING f.id
)
INSERT INTO rekey_result (step, measured_rows)
SELECT 'features_repointed', count(*) FROM repointed;

-- 6. Drop the now-unreferenced per-observation rows.
WITH deleted AS (
  DELETE FROM geo.geometry g
  WHERE g.geometry_id NOT IN (SELECT survivor_id FROM rekey_survivor)
  RETURNING g.geometry_id
)
INSERT INTO rekey_result (step, measured_rows)
SELECT 'redundant_versions_deleted', count(*) FROM deleted;

-- 7. Rename the survivors onto their entity key. uq_geometry_current is a partial unique index on
--    natural_key WHERE version_valid_to IS NULL, and guard 3b proved every surviving row is open, so
--    one survivor per entity is exactly what that index will accept.
WITH renamed AS (
  UPDATE geo.geometry g
  SET    natural_key = s.entity_key
  FROM   rekey_survivor s
  WHERE  g.geometry_id = s.survivor_id
    AND  g.natural_key IS DISTINCT FROM s.entity_key
  RETURNING g.geometry_id
)
INSERT INTO rekey_result (step, measured_rows)
SELECT 'survivors_renamed', count(*) FROM renamed;

-- 8. Assert against the census captured above, then commit. Every mismatch raises, which rolls the
--    whole transaction back.
DO $assert$
DECLARE
  census            rekey_census%ROWTYPE;
  repointed_rows    bigint;
  deleted_rows      bigint;
  renamed_rows      bigint;
  rows_after        bigint;
  orphaned_features bigint;
  duplicate_open    bigint;
  fanout            record;
BEGIN
  SELECT * INTO census FROM rekey_census;
  SELECT measured_rows INTO repointed_rows FROM rekey_result WHERE step = 'features_repointed';
  SELECT measured_rows INTO deleted_rows   FROM rekey_result WHERE step = 'redundant_versions_deleted';
  SELECT measured_rows INTO renamed_rows   FROM rekey_result WHERE step = 'survivors_renamed';

  RAISE NOTICE 'census: geometry_rows_before=% entities=% redundant_rows=% linked_features=% unlinked_features=%',
    census.geometry_rows_before, census.entities, census.redundant_rows,
    census.linked_features_before, census.unlinked_features_before;
  RAISE NOTICE 'result: features_repointed=% versions_deleted=% survivors_renamed=%',
    repointed_rows, deleted_rows, renamed_rows;

  IF deleted_rows <> census.redundant_rows THEN
    RAISE EXCEPTION 're-key: deleted % row(s), expected % redundant', deleted_rows, census.redundant_rows;
  END IF;

  SELECT count(*) INTO rows_after FROM geo.geometry;
  IF rows_after <> census.entities THEN
    RAISE EXCEPTION 're-key: % row(s) remain, expected one per entity (%)', rows_after, census.entities;
  END IF;

  -- Every feature that had a link still has one, and it resolves. The re-key must not orphan a fact.
  SELECT count(*) INTO orphaned_features
  FROM geo.features f
  LEFT JOIN geo.geometry g ON g.geometry_id = f.geometry_id
  WHERE f.geometry_id IS NOT NULL AND g.geometry_id IS NULL;
  IF orphaned_features <> 0 THEN
    RAISE EXCEPTION 're-key: % feature(s) point at a geometry row that no longer exists', orphaned_features;
  END IF;

  IF (SELECT count(*) FROM geo.features WHERE geometry_id IS NOT NULL) <> census.linked_features_before THEN
    RAISE EXCEPTION 're-key: the number of linked features changed; a repoint dropped a link';
  END IF;

  SELECT count(*) INTO duplicate_open FROM (
    SELECT natural_key FROM geo.geometry WHERE version_valid_to IS NULL
    GROUP BY natural_key HAVING count(*) > 1
  ) AS collisions;
  IF duplicate_open <> 0 THEN
    RAISE EXCEPTION 're-key: % natural_key(s) hold more than one open version', duplicate_open;
  END IF;

  -- Fan-out report, a NOTICE and never an assertion, so the ratio the dimension now carries stays in
  -- front of whoever next reads the growth story. After this script the two collapsing producers
  -- should read keys == distinct_places; firms and wfigs are unchanged by construction.
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

  RAISE NOTICE 're-key: all assertions passed';
END
$assert$;

COMMIT;
