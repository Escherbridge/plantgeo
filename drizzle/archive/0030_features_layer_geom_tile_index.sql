-- The composite layer+geometry index for the tile path: one GiST index that lets every
-- `geo.*_tiles()` function descend by LAYER FIRST and search spatially only inside that layer,
-- instead of BitmapAnd-ing a whole-table geometry scan against a whole-layer id scan.
--
-- MEASURED EVIDENCE, PRODUCTION, 2026-08-16. Recorded here so a future reader does not "clean
-- this index up". `geo.burn_severity_tiles(6, 10, 22)`, EXPLAIN (ANALYZE, BUFFERS):
--
--   Bitmap Heap Scan on features f  (actual time=45570.827..45573.438 rows=137)
--     -> BitmapAnd
--        -> Bitmap Index Scan on idx_features_layer  (actual 0.813 ms, rows=541)
--        -> Bitmap Index Scan on idx_features_geom   (actual 45568.129 ms, rows=1318892)
--   Execution Time: 45574.179 ms
--
-- Read the two legs against each other. The layer leg finds all 541 burn-severity features in
-- 0.8 ms. The geometry leg spends 45.6 SECONDS walking 1,318,892 index entries -- almost all of
-- them fire-detections points, the densest layer in the table, which shares a bounding box with
-- every other layer in the Pacific Northwest -- to contribute to an answer of 137 rows. There is
-- no index pairing `layer_id` with `geom`, so a BitmapAnd is the ONLY plan shape available:
-- PostgreSQL must materialise both bitmaps in full before it can intersect them. It cannot
-- short-circuit, because the cheap leg has no way to restrict the expensive one. The planner also
-- estimated 499,986 rows where 541 were returned -- a ~924x error -- which is why it priced this
-- plan as reasonable in the first place.
--
-- `ANALYZE geo.features` and `ANALYZE geo.layers` were both run on production before this
-- migration was written, and the plan DID NOT RECOVER (still > 300 s). Stale statistics were a
-- contributing factor to the estimate error; they were not the cause of the 45 s. The cause is
-- that the only spatial access path on this table is global, and this table now holds ~5,002,853
-- live tuples across 7,234 MB with eleven layers sharing one geographic extent. The missing index
-- is genuine and required.
--
--
-- WHY A COMPOSITE GiST AND NOT ELEVEN PER-LAYER PARTIAL GiST INDEXES.
--
-- The per-layer alternative is superficially attractive: no extension, and each index is a
-- single-column geometry build that can take PostGIS's Hilbert-curve sorted-build fast path, so
-- each finishes in seconds to a couple of minutes rather than in one large unsorted build. It is
-- rejected on a correctness ground, not a cost one: THE PLANNER COULD NEVER USE IT HERE.
--
-- A per-layer partial index needs the predicate `WHERE layer_id = '<uuid>'::uuid`, and PostgreSQL
-- only chooses a partial index when it can prove the query's own quals IMPLY that predicate. Every
-- tile function in this repo selects its layer BY NAME, through a join:
--
--   FROM geo.features f JOIN geo.layers l ON f.layer_id = l.id
--   WHERE l.name = 'burn-severity' AND l.is_public IS TRUE ...
--
-- The constant in that query is `l.name`. There is no constant `f.layer_id` anywhere in it, and
-- the planner does not derive one: equivalence classes propagate the join equality
-- `f.layer_id = l.id`, never the unrelated restriction on `l.name`. So the partial predicate is
-- unprovable, and eleven correctly-built indexes would sit on disk unused while the 45 s plan
-- stayed exactly as it is. Making them usable means inlining eleven hardcoded uuids into eleven
-- tile function bodies -- which forces a Martin restart that nothing in this pipeline performs,
-- and bakes environment-specific uuids into schema DDL that is replayed against every database.
--
-- The composite index has no such problem. `geo.layers` is 11 rows, so the planner drives the
-- nested loop from `l` and `f.layer_id` arrives at the inner side as a PARAMETERISED equality --
-- exactly the form `(layer_id = $1 AND geom && $2)` that a multicolumn GiST probes with both
-- columns as index conditions. That is already how `idx_features_layer` is reached today; this
-- index simply gives that same probe a spatial second column to descend on. It also generalises:
-- a twelfth layer added tomorrow is fast on the day it is created, with no DDL.
--
--
-- WHY `btree_gist` IS ACCEPTABLE. GiST has no native operator class for `uuid`, so a uuid cannot
-- lead a GiST index without it. It is a standard contrib extension; it is marked TRUSTED as of
-- PostgreSQL 13, so the database owner can install it without superuser; it ships in the same
-- contrib package as the PostGIS build already in use; and it adds one small opclass, changing no
-- existing behaviour and creating no dependency for anything else in this schema. Production is
-- PostgreSQL 18.4 / PostGIS 3.6.4, where the uuid GiST opclass is long-settled. The cost of the
-- extension is an ordinary schema dependency. The cost of avoiding it is an index the planner
-- cannot use.
--
--
-- WHY THE PREDICATE IS `status = 'published' AND geom IS NOT NULL`.
--
-- Both conjuncts are written LITERALLY in every `geo.*_tiles()` function's WHERE clause (0015's
-- four functions, plus 0012, 0009, 0023 and 0028), so implication is direct and needs no
-- inference. Both earn their place:
--
--   * `status = 'published'` matches the precedent set by `ix_features_layer_observation_day` and
--     keeps unpublished rows out of the tree. No tile function has ever served a non-published
--     row, so nothing is lost.
--   * `geom IS NOT NULL` is not redundant. GiST DOES index null keys, and a null-geometry entry
--     can never satisfy the `&&` operator -- it is dead weight in every page it occupies.
--     Excluding it is strictly a win, and it cannot cost the index to a caller who omits the
--     explicit test: `&&` is STRICT, and PostgreSQL's predicate prover already treats a strict
--     operator on a column as implying that column IS NOT NULL.
--
-- `INCLUDE` is deliberately absent. Every tile function projects `properties` and `geom` from the
-- heap anyway, so there is no index-only scan to reach for; a covering column would only make the
-- tree larger. This is the opposite call from `ix_features_layer_observation_day`, for the
-- opposite reason: that index exists precisely to AVOID the heap touch that detoasts `properties`.
--
--
-- WHY THIS FILE DOES NOT CREATE THE INDEX -- the transaction trap, restated.
--
-- `node scripts/migrate.mjs` uses drizzle-orm/postgres-js's migrator, which wraps each migration
-- FILE in one transaction; the statement separator splits statements but they still share it.
-- (That separator is spelled out nowhere in this comment on purpose: drizzle-orm/migrator.js:16
-- splits the file on the literal string, so writing it here -- even inside a comment -- cuts the
-- header in half and the migration dies with a 42601 syntax error. This exact bug was caught in
-- 0029.) A concurrent index build raises 25001 inside that transaction, and `IF NOT EXISTS` does
-- not save you, because PostgreSQL raises 25001 BEFORE it checks existence. A NON-concurrent build
-- is not an option either: it holds ACCESS EXCLUSIVE on a 7,234 MB table for the whole build,
-- inside `preDeployCommand`, which is an outage.
--
-- So the index is built OUT OF BAND, by hand, BEFORE this migration is applied. This file installs
-- the extension the index depends on, ASSERTS that the index exists AND IS VALID, and records why
-- it exists as a durable catalog comment. A deploy that skipped the manual step fails loudly here
-- instead of silently regressing to the 45 s plan.
--
-- That assertion also means a FRESH database cannot replay this tree from 0000 unattended -- the
-- same trade 0029 already makes for `ix_features_layer_observation_day`. The escape hatch is two
-- statements, and on an empty table neither is slow nor needs to be concurrent:
--   CREATE EXTENSION IF NOT EXISTS btree_gist;
--   CREATE INDEX ix_features_layer_geom ON geo.features USING gist (layer_id, geom)
--     WHERE status = 'published' AND geom IS NOT NULL;
-- Run those against the new database after 0001 has created `geo.features`, then replay onward.
--
--
-- APPLY PROCEDURE. In this order, against production, by hand.
--
--   1. Install the extension (idempotent; step 3 cannot compile its opclass without it):
--        CREATE EXTENSION IF NOT EXISTS btree_gist;
--
--   2. Clear the retry trap FIRST. A concurrent build that failed part-way leaves an INVALID index
--      that still OCCUPIES THE NAME, so `IF NOT EXISTS` on the retry returns success while the
--      query keeps its old plan. Check, and drop concurrently if it is there:
--        SELECT c.relname, i.indisvalid, i.indisready
--          FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
--         WHERE i.indexrelid = to_regclass('geo.ix_features_layer_geom');
--        DROP INDEX CONCURRENTLY IF EXISTS geo.ix_features_layer_geom;   -- only if indisvalid = f
--
--   3. Build it, in a session with its OWN timeouts, outside any transaction:
--        SET lock_timeout = '20min';
--        SET statement_timeout = 0;
--        SET maintenance_work_mem = '256MB';
--        CREATE INDEX CONCURRENTLY ix_features_layer_geom
--          ON geo.features USING gist (layer_id, geom)
--          WHERE status = 'published' AND geom IS NOT NULL;
--
--      `lock_timeout = '20min'` IS NOT A TYPO, AND MUST NOT BE COPIED FROM
--      `scripts/apply-pre-aggregation.mjs`, WHICH HARDCODES '5s'. Five seconds is too short for
--      this table: a prior concurrent build on `geo.features` died on 55P03 (lock_not_available)
--      and left exactly the INVALID index step 2 exists to clean up; it had to be dropped and
--      rebuilt at a 20-minute lock timeout. The lock a concurrent build takes is
--      SHARE UPDATE EXCLUSIVE, which does NOT conflict with the ROW EXCLUSIVE that ingest INSERTs
--      hold -- what it collides with is autovacuum/ANALYZE on this same 7 GB table, which easily
--      runs longer than five seconds. Waiting twenty minutes for that therefore does not block a
--      single writer; it queues only behind, and ahead of, other maintenance.
--
--   4. VERIFY VALIDITY. Not optional -- step 2 says what skipping it costs:
--        SELECT i.indisvalid, i.indisready,
--               pg_size_pretty(pg_relation_size('geo.ix_features_layer_geom')) AS size
--          FROM pg_index i
--         WHERE i.indexrelid = to_regclass('geo.ix_features_layer_geom');
--      Both flags must be `t`. If `indisvalid` is `f`, go back to step 2. Expect roughly
--      400-600 MB: the existing single-column `idx_features_geom` is 310 MB over the same rows,
--      and this one carries an additional 16-byte uuid per entry.
--
--   5. `ANALYZE geo.features;`. Not for this index's own statistics -- it indexes two plain
--      columns and introduces no new expression, so it needs none -- but because the ~924x
--      estimate error above is what made the old plan look cheap, and fresh statistics are what
--      let the planner price the new access path against it.
--
--   6. Re-EXPLAIN `geo.burn_severity_tiles(6, 10, 22)`. The success criterion is a SHAPE change,
--      not just a smaller number: the BitmapAnd must be GONE, replaced by a single Index Scan (or
--      Bitmap Index Scan) on `ix_features_layer_geom` carrying BOTH the `layer_id` equality and
--      the `&&` as Index Cond. If the BitmapAnd is still there, the index is not being used and a
--      fast timing is cache luck.
--
--   7. Only then apply this migration file (`node scripts/migrate.mjs`), which will pass its
--      assertion. Only AFTER that lands green do the journal entry and the
--      `src/lib/server/db/migration-contract.ts` re-pin get committed, together, in one commit --
--      committing that pair before the migration is live in production makes `/api/ready` return
--      503 and fails the Railway healthcheck.
--
--
-- BUILD COST AND RISK, stated honestly. The leading column is a uuid, and btree_gist's uuid
-- opclass provides no GiST sortsupport routine, so this build cannot take the Hilbert-curve
-- sorted-build fast path a single-column geometry index gets -- expect the ordinary per-tuple
-- insert build, plus the second table scan and the validation pass that CONCURRENTLY adds. On a
-- 3 GB-capped box under live ingest, budget 25-60 minutes and do not be alarmed by 90. The build
-- is interruptible (step 2 cleans up after it), it never blocks a writer, and its only standing
-- cost while it runs is I/O contention and one autovacuum it may postpone.
--
-- NO MARTIN RESTART IS REQUIRED BY THIS MIGRATION. Martin enumerates its function sources from the
-- catalog at startup, and this file creates no function and changes no function's name, signature,
-- argument list or return type -- the rule that a tile-function change demands a restart (and that
-- a missing tile function 404s the whole composite and hides EVERY layer) does not apply here.
-- Index selection happens per-execution inside PostgreSQL, so the new plan is picked up by the
-- very next tile request, with no restart, no redeploy and no cache purge.

CREATE EXTENSION IF NOT EXISTS btree_gist;
--> statement-breakpoint

-- The assertion. `to_regclass` alone is NOT sufficient, and asking only for existence is the
-- single most likely way for this whole exercise to appear applied and do nothing: a failed
-- concurrent build leaves an index that EXISTS, is never used by the planner, is never maintained
-- by the writer, and still answers `to_regclass`. Both catalog flags are checked.
DO $$
DECLARE
  index_ref regclass := to_regclass('geo.ix_features_layer_geom');
  index_is_valid boolean;
  index_is_ready boolean;
BEGIN
  IF index_ref IS NULL THEN
    RAISE EXCEPTION
      'ix_features_layer_geom is missing: build it out of band before applying 0030 -- CREATE INDEX CONCURRENTLY ix_features_layer_geom ON geo.features USING gist (layer_id, geom) WHERE status = ''published'' AND geom IS NOT NULL (this file''s header carries the lock_timeout and the invalid-index retry trap)';
  END IF;

  SELECT i.indisvalid, i.indisready
    INTO index_is_valid, index_is_ready
    FROM pg_index AS i
   WHERE i.indexrelid = index_ref;

  -- COALESCE, not a bare NOT. A three-valued NULL here would make `NOT index_is_valid` evaluate to
  -- NULL, the IF would not fire, and the assertion would pass on exactly the state it exists to
  -- catch. NULL happens if `geo.ix_features_layer_geom` resolves to a relation that is not an
  -- index at all -- a name collision -- in which case pg_index has no row for it and the SELECT
  -- INTO leaves both variables unset.
  IF NOT COALESCE(index_is_valid, false) OR NOT COALESCE(index_is_ready, false) THEN
    RAISE EXCEPTION
      'ix_features_layer_geom is not a usable index (indisvalid=%, indisready=%; NULL means the name resolves to something that is not an index). The usual cause is a concurrent build that failed part-way and left the name occupied, which the planner then ignores forever. DROP INDEX CONCURRENTLY IF EXISTS geo.ix_features_layer_geom and rebuild at lock_timeout = 20min before applying 0030',
      index_is_valid, index_is_ready;
  END IF;
END $$;
--> statement-breakpoint

COMMENT ON INDEX geo.ix_features_layer_geom IS
  'The tile path''s access method: GiST (layer_id, geom) over published, located features, via '
  'btree_gist. Every geo.*_tiles() function filters one layer and one tile envelope; without this '
  'index those two filters can only be BitmapAnd-ed, which on 2026-08-16 made burn-severity tile '
  '6/10/22 walk 1,318,892 geometry index entries in 45.6 s to return 137 rows. Per-layer partial '
  'GiST indexes cannot replace it: the tile functions select their layer by NAME through a join, '
  'so no constant layer_id exists for the planner to prove a partial predicate against. DO NOT '
  'DROP -- see drizzle/0030_features_layer_geom_tile_index.sql.';
