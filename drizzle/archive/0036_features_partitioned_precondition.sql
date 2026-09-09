-- The precondition-assert migration for the partitioned `geo.features`. Runbook §0.13 item 3
-- named this file "not written" and "in no slice's scope"; this is it.
--
-- WHY THIS FILE EXISTS, AND WHY IT IS DORMANT ON PURPOSE. `scripts/partition-features.mjs` runs
-- the LIST(layer_id) swap out of band, by hand, in phases (plan/create/copy/index/trigger/verify/
-- swap/analyze/adopt/rollback per its own header) -- nothing in `preDeployCommand` performs it.
-- This migration asserts the swap's four load-bearing postconditions and does nothing else: no
-- DDL, no data touched, no index built. It exists to be registered in `drizzle/meta/_journal.json`
-- IN THE SAME COMMIT AS THE SWAP is declared complete, exactly the way `0030`'s header describes
-- for `ix_features_layer_geom` and `0032`'s header describes for the observation-day axis. Until
-- that commit, this file stays dormant and unregistered, alongside `0030`-`0033` (§0.13 item 1).
--
-- WHAT HAPPENS IF THIS IS SKIPPED. `node scripts/migrate.mjs` replays every registered migration,
-- in order, against whatever database it is pointed at. Without this assertion, a fresh database,
-- a rolled-back environment, or a deploy that races ahead of the manual swap procedure would
-- silently replay every earlier migration against an UNPARTITIONED `geo.features` and come up
-- looking healthy: the six tile functions rewritten by `0033` read `geo.features` by name, not by
-- structure, so they run -- just slowly, back on the un-partitioned heap, with none of the
-- locality the swap exists to buy, and the runtime-created-layer 500 from §0.4 still live. This
-- file turns that silent regression into a loud, immediate migration failure, at deploy time,
-- before a single request is served.
--
-- FOUR ASSERTIONS, EACH A DISTINCT WAY THE SWAP CAN BE PARTIALLY DONE:
--
--   1. `geo.features` has `relkind = 'p'` -- the swap actually ran and the final
--      `ALTER TABLE geo.features_new RENAME TO features` (partition-features.mjs's swap phase)
--      landed. A plain heap (`relkind = 'r'`) means the swap never happened, or happened and was
--      rolled back (the script's own rollback phase renames `features` back to `features_legacy`
--      and restores the pre-swap heap under the `features` name).
--
--   2. `features_layer_external_id_unique` exists AND `indisvalid` -- the partial UNIQUE index
--      that backs every ingest writer's `ON CONFLICT (layer_id, (properties->>'id'))` was rebuilt
--      on the new partitioned parent and its concurrent build actually finished. This follows the
--      exact idiom `drizzle/0030:196-224` uses for `ix_features_layer_geom`: `to_regclass` alone
--      is not sufficient, because a concurrent build that died part-way leaves an index that
--      EXISTS, answers `to_regclass`, and is silently never used or maintained
--      (`indisvalid = false`). Skipping this check would let a deploy proceed on a database where
--      every future upsert either raises a duplicate-key error the writer does not expect, or
--      silently stops deduplicating -- see the established §0.1 finding that this is the readiness
--      probe's own index name.
--
--   3. The primary key on `geo.features` is the COMPOSITE `(id, layer_id)`, not a bare `(id)`
--      unique constraint. PostgreSQL requires every partition key column to appear in a
--      partitioned table's primary key, so a plain `(id)` PK is not merely a style regression
--      here -- it is a shape that native partitioning cannot express, and its presence means the
--      swap script's own PK build step did not complete as written
--      (`partition-features.mjs:885-899`).
--
--   4. `geo.features_default` exists AND is registered as `geo.features`'s DEFAULT partition
--      (not merely a same-named table sitting unattached). Runbook §0.4 makes this mandatory,
--      not optional: `layersRouter.create` mints `geo.layers` rows -- and therefore new
--      `layer_id` values -- at request time, with no partition-registry step in between. Without
--      a DEFAULT partition, the very first write into a runtime-created layer has no partition to
--      route into and raises `no partition of relation "features" found for row` (PostgreSQL
--      23514), a 500 on a path with no prior warning. `to_regclass` existence is deliberately not
--      the test, for the same reason assertion 2 rejects it: a table named `features_default`
--      that was created but never `ATTACH PARTITION ... DEFAULT`-ed into `geo.features` would
--      pass an existence check and still leave every runtime-created layer's first write with
--      nowhere to go.
--
-- ASSUMPTION, STATED BECAUSE IT IS NOT VERIFIABLE FROM READ-ONLY EVIDENCE TODAY: this file assumes
-- `scripts/partition-features.mjs`'s swap-day renames land exactly as its current source names
-- them -- the partitioned parent ends up owning the bare name `geo.features`, its primary key
-- constraint ends up named `features_pkey` (the BUILD_SUFFIX-suffixed name stripped back down on
-- swap, per the script's rename phase), and the default partition ends up named
-- `geo.features_default` with no `BUILD_SUFFIX`. If a future edit to that script changes any of
-- those three names, this assertion must be updated in the same commit or it will fail loudly
-- against a swap that actually succeeded -- the safer failure direction, but still worth a fresh
-- read of the script before trusting a red run of this file at cutover time.
--
-- No `CREATE`, `ALTER`, `DROP` or `REFRESH` anywhere below -- this file only reads the catalog, so
-- unlike `0030`'s and `0032`'s assertions it never blocks on a lock and never needs its own
-- `lock_timeout`. The drizzle statement-separator marker never appears inside a comment here, for
-- the reason `0029`'s and `0031`'s headers give.

DO $$
DECLARE
  features_ref regclass := to_regclass('geo.features');
  features_relkind "char";
BEGIN
  IF features_ref IS NULL THEN
    RAISE EXCEPTION
      'geo.features does not exist at all -- 0036 cannot assert against a table that is not '
      'there. Something upstream of this migration in the journal did not apply.';
  END IF;

  SELECT c.relkind INTO features_relkind FROM pg_class AS c WHERE c.oid = features_ref;

  IF features_relkind IS DISTINCT FROM 'p' THEN
    RAISE EXCEPTION
      'geo.features has relkind=% (expected ''p'', a partitioned parent). The LIST(layer_id) '
      'partitioning swap in scripts/partition-features.mjs has not run against this database, or '
      'was rolled back. Do not register/deploy 0036 until the swap''s swap phase has completed '
      'and been verified -- see conductor/RUNBOOK.md section 0 for the full procedure.',
      features_relkind;
  END IF;
END $$;
--> statement-breakpoint

DO $$
DECLARE
  unique_index_ref regclass := to_regclass('geo.features_layer_external_id_unique');
  index_is_valid boolean;
  index_is_ready boolean;
BEGIN
  IF unique_index_ref IS NULL THEN
    RAISE EXCEPTION
      'geo.features_layer_external_id_unique is missing on the partitioned geo.features. Every '
      'ingest writer''s ON CONFLICT (layer_id, (properties->>''id'')) depends on it, and it is '
      'also the readiness probe''s own index name (see established §0.1 findings) -- rebuild it '
      'per scripts/partition-features.mjs''s index phase before applying 0036.';
  END IF;

  SELECT i.indisvalid, i.indisready
    INTO index_is_valid, index_is_ready
    FROM pg_index AS i
   WHERE i.indexrelid = unique_index_ref;

  -- COALESCE, not a bare NOT: same reasoning as drizzle/0030:213-218. A NULL here means
  -- `geo.features_layer_external_id_unique` resolved to something that is not an index at all
  -- (a name collision), and a bare `NOT index_is_valid` would let that state through as if it
  -- passed, which is the one state this whole assertion exists to catch.
  IF NOT COALESCE(index_is_valid, false) OR NOT COALESCE(index_is_ready, false) THEN
    RAISE EXCEPTION
      'geo.features_layer_external_id_unique is not a usable index (indisvalid=%, indisready=%; '
      'NULL means the name resolves to something that is not an index). The usual cause is a '
      'concurrent build on the new partitioned parent that failed part-way and left the name '
      'occupied while the planner ignores it forever -- DROP INDEX CONCURRENTLY IF EXISTS and '
      'rebuild before applying 0036.',
      index_is_valid, index_is_ready;
  END IF;
END $$;
--> statement-breakpoint

DO $$
DECLARE
  primary_key_columns text[];
BEGIN
  SELECT array_agg(a.attname ORDER BY k.ord)
    INTO primary_key_columns
    FROM pg_constraint AS con
    CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
    JOIN pg_attribute AS a
      ON a.attrelid = con.conrelid AND a.attnum = k.attnum
   WHERE con.conrelid = 'geo.features'::regclass
     AND con.contype = 'p';

  IF primary_key_columns IS NULL THEN
    RAISE EXCEPTION
      'geo.features has no PRIMARY KEY at all. scripts/partition-features.mjs''s index phase '
      'builds ALTER TABLE geo.features_new ADD CONSTRAINT features_pkey PRIMARY KEY (id, '
      'layer_id) before the swap -- that step did not complete or did not land.';
  END IF;

  IF primary_key_columns IS DISTINCT FROM ARRAY['id', 'layer_id'] THEN
    RAISE EXCEPTION
      'geo.features PRIMARY KEY is (%), not the required composite (id, layer_id). PostgreSQL '
      'requires every partition key column to appear in a partitioned table''s primary key, so a '
      'bare (id) PK is not a style regression here -- it is a shape native partitioning cannot '
      'express, and its presence means the swap''s PRIMARY KEY build step '
      '(partition-features.mjs:885-899) did not complete as written.',
      array_to_string(primary_key_columns, ', ');
  END IF;
END $$;
--> statement-breakpoint

DO $$
DECLARE
  parent_ref regclass := 'geo.features'::regclass;
  default_partition_ref regclass := to_regclass('geo.features_default');
  registered_default_ref regclass;
BEGIN
  IF default_partition_ref IS NULL THEN
    RAISE EXCEPTION
      'geo.features_default does not exist. Runbook section 0.4 makes a DEFAULT partition '
      'mandatory, not optional: layersRouter.create mints geo.layers rows -- and therefore new '
      'layer_id values -- at request time with no partition-registry step in between, so without '
      'a DEFAULT partition the first write into a runtime-created layer raises ''no partition of '
      'relation "features" found for row'' (PostgreSQL 23514).';
  END IF;

  SELECT p.partdefid INTO registered_default_ref
    FROM pg_partitioned_table AS p
   WHERE p.partrelid = parent_ref;

  -- Existence of a same-named table is deliberately not the test, for the same reason assertion 2
  -- rejects a bare to_regclass check: a table named geo.features_default that was created but
  -- never ATTACH PARTITION ... DEFAULT-ed into geo.features would pass an existence check and
  -- still leave every runtime-created layer's first write with nowhere to route.
  IF registered_default_ref IS DISTINCT FROM default_partition_ref THEN
    RAISE EXCEPTION
      'geo.features_default exists as a table but is NOT geo.features'' registered DEFAULT '
      'partition (pg_partitioned_table.partdefid does not point at it). It must be attached with '
      'ALTER TABLE geo.features ATTACH PARTITION geo.features_default DEFAULT before applying '
      '0036, or every write into a layer_id with no dedicated partition -- which includes every '
      'layer created through layersRouter.create after the swap -- has no partition to land in.';
  END IF;
END $$;
--> statement-breakpoint

COMMENT ON TABLE geo.features IS
  'Partitioned parent (LIST on layer_id) as of the swap this migration''s precondition asserted. '
  'A geo.features_default DEFAULT partition is mandatory (runbook §0.4): layersRouter.create '
  'mints new layer_id values at request time with no partition-registry step, so removing the '
  'default partition 500s the first write into any layer created after the swap. See '
  'drizzle/0036_features_partitioned_precondition.sql and scripts/partition-features.mjs.';
