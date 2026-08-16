-- PARKED AND UNAPPLIED. DESTRUCTIVE. REQUIRES EXPLICIT OWNER APPROVAL.
--
-- Nothing runs this file automatically. It is not in `drizzle/`, it has no journal entry, it is
-- not referenced by `scripts/apply-pre-aggregation.mjs`, and it must not be added to any of them.
-- It is `psql -f`'d by hand, one statement at a time, by someone who has read the gate recorded
-- above each statement and satisfied it.
--
-- WHY IT IS SEPARATE FROM 0029. Two independent reasons, either of which is sufficient:
--
--   1. `DROP INDEX CONCURRENTLY` raises 25001 inside a transaction, and the drizzle migrator wraps
--      every migration file in one. It is the same trap that keeps `CREATE INDEX CONCURRENTLY` out
--      of 0029 (see that file's header).
--   2. These drops are the only IRREVERSIBLE step in the whole pre-aggregation exercise. Rebuilding
--      `idx_features_properties` is a 4,372 MB GIN build on a box with a 3 GB memory cap -- an
--      outage, not an undo. Adding a matview is reversible in seconds; this is not.
--
-- WHAT IT IS WORTH. About 5,392 MB of never-read index, against roughly 360 MB of new index the
-- design adds. It is the largest single on-disk reclamation in the design and it costs nothing in
-- functionality IF the gates below hold. Every size and every scan count is measured against
-- production on 2026-08-15 by `pg_stat_user_indexes` / `pg_relation_size`.
--
-- BEFORE RUNNING ANY OF THIS, re-measure. A lifetime scan count is cumulative since the last
-- `pg_stat_reset()`, and "0 scans" measured on a box that was restarted last week means nothing:
--
--   SELECT s.schemaname, s.relname, s.indexrelname, s.idx_scan,
--          pg_size_pretty(pg_relation_size(s.indexrelid)) AS size,
--          st.stats_reset
--     FROM pg_stat_user_indexes s
--     CROSS JOIN (SELECT stats_reset FROM pg_stat_database WHERE datname = current_database()) st
--    WHERE s.indexrelname IN (
--            'idx_features_properties', 'idx_features_layer_external_id_lookup',
--            'ix_geometry_centroid', 'ix_geometry_geom', 'idx_features_layer_updated_at')
--    ORDER BY pg_relation_size(s.indexrelid) DESC;
--
-- Run each `DROP INDEX CONCURRENTLY` in its OWN session with `SET lock_timeout = '5s'`. A
-- concurrent drop still needs a brief lock at the start and end; a five-second ceiling makes it
-- give up rather than queue in front of every writer.


-- =============================================================================================
-- GROUP 1 -- ungated. Measured never-read, and nothing in the tree can reach them.
-- =============================================================================================

-- 4,372 MB, 11 lifetime scans. A GIN index over the whole of `geo.features.properties`, created by
-- 0001 before the layer set existed. It is the single largest relation in the `geo` schema after
-- the features heap itself, and eleven scans across the life of the database is not a workload --
-- every jsonb read in `src/` is scoped by `layer_id` first and answered from a btree.
--
-- Gate: none beyond re-measuring the scan count above.
DROP INDEX CONCURRENTLY IF EXISTS geo.idx_features_properties;

-- 678 MB, 0 lifetime scans. Created by 0001 for an external-id lookup path that no reader in `src/`
-- or `services/**/sql/` issues; ingest dedupes on its own natural keys, not on this.
--
-- Gate: none beyond re-measuring.
DROP INDEX CONCURRENTLY IF EXISTS geo.idx_features_layer_external_id_lookup;

-- 159 MB, 0 lifetime scans. A GiST index on `geo.geometry.centroid`. Verified 2026-08-15 by
-- grepping `src/` and `services/agri-data-service/src/agri_data_service/sql/`: every reference to
-- `geo.geometry.centroid` is a PROJECTION -- `ST_X(COALESCE(g.centroid, ST_Centroid(f.geom)))` in
-- `usda-soil.ts:1176`, and the same expression in `geo.mv_soil_survey_grid`'s DDL. A projection is
-- reached through the `geometry_id` join, never through a spatial operator, so no plan can use a
-- GiST index on that column.
--
-- Gate: none beyond re-measuring.
DROP INDEX CONCURRENTLY IF EXISTS geo.ix_geometry_centroid;


-- =============================================================================================
-- GROUP 2 -- gated. The gate was checked on 2026-08-15 and PASSED; re-check before running.
-- =============================================================================================

-- 183 MB, 1 lifetime scan. A GiST index on `geo.geometry.geom`.
--
-- GATE: no spatial predicate anywhere may reference `geo.geometry.geom`. Checked 2026-08-15 across
-- `src/`, `services/agri-data-service/src/agri_data_service/sql/` and `drizzle/*.sql` for `&&`,
-- `ST_Intersects`, `ST_DWithin` and `<->` against that column: the only four references are
-- projections inside a CASE that simplifies the geometry for output
-- (`environmental-read-model.ts:1343-1344` and `:4032-4033`). Every spatial predicate in the tree
-- runs against `geo.features.geom` (`idx_features_geom`), `geo.drought_areas.geom` or
-- `agri.spatial_cell.geometry`/`.centroid` -- never against the geometry dimension.
--
-- RE-CHECK before running, because a slice landing after this file was written could add one:
--   grep -rnE '(&&|ST_Intersects|ST_DWithin|<->)' src/ services/*/src/**/sql/ drizzle/*.sql \
--     | grep -E 'g\.geom\b|geometry\.geom\b'
-- Any hit at all means KEEP the index and delete this statement.
DROP INDEX CONCURRENTLY IF EXISTS geo.ix_geometry_geom;

-- 72 MB, 9,595 lifetime scans, and 18,949,770,956 tuples read to return 31,035 fetched -- a ratio
-- of about 610,000:1, which is what an index being used for the wrong shape looks like. It leads
-- with `layer_id`, so `max(updated_at)` and every unscoped `updated_at` range fall through it.
--
-- GATE, both halves required:
--   (a) `geo.ix_features_updated_at` must already exist AND be valid:
--         SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass('geo.ix_features_updated_at');
--       It is built by `scripts/apply-pre-aggregation.mjs --phase=a`.
--   (b) EXPLAIN every remaining `updated_at` consumer and confirm each now plans on
--       `ix_features_updated_at` and not on this one. This index has real scans, unlike group 1 --
--       9,595 of them -- so "nobody uses it" is NOT the argument here. The argument is that the new
--       index serves the same asks better, and an EXPLAIN is what proves it rather than assumes it.
--
-- Until both halves hold, leave this statement commented out. 72 MB is the smallest prize in this
-- file and the only one attached to a live access path.
-- DROP INDEX CONCURRENTLY IF EXISTS geo.idx_features_layer_updated_at;


-- =============================================================================================
-- GROUP 3 -- DO NOT DROP. Recorded here so the question is not re-opened.
-- =============================================================================================

-- `geo.uq_geometry_version` (400 MB, 0 lifetime scans) appears in the design's drop table with the
-- gate "do not drop until `SELECT conname FROM pg_constraint WHERE conindid =
-- 'geo.uq_geometry_version'::regclass` returns nothing". That gate is now RESOLVED, and it resolves
-- to NO:
--
--   * `drizzle/0008_geometry_dimension.sql:56` declares it as
--     `CONSTRAINT "uq_geometry_version" UNIQUE ("natural_key","version_valid_from")`, and
--   * `src/lib/server/db/schema.ts:203` declares the same constraint in the Drizzle schema.
--
-- It is a UNIQUE CONSTRAINT, not a bare index. `DROP INDEX` cannot remove it (PostgreSQL refuses
-- with "cannot drop index ... because constraint ... requires it"), and dropping the constraint
-- instead would delete the Type-2 geometry dimension's uniqueness guarantee -- the thing that stops
-- two rows claiming the same natural key over the same validity window. Zero scans is expected for
-- a constraint index: it is enforced on write, not read. The 400 MB is the price of the guarantee.
--
-- No statement here. Deliberately.
