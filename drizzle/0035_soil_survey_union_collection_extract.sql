-- Fix `geo.mv_soil_survey_union`, which has NEVER ONCE produced a row in production (4 consecutive
-- failed refresh attempts recorded in `agri.matview_refresh_state`, `relispopulated = false` since
-- the relation was created by 0029). This migration replaces the matview's definition; it does not
-- touch `geo.mv_soil_survey_grid`, which refreshes and populates correctly today.
--
--
-- THE BUG, diagnosed against the shipped 0029 definition, not guessed.
--
-- `delineation` (0029:910-924) computes `ST_MakeValid(ST_SnapToGrid(f.geom, 0.000001)) AS geom` and
-- stops there -- no `ST_CollectionExtract`. `ST_MakeValid` on an invalid polygon is free to hand
-- back a GEOMETRYCOLLECTION mixing the repaired polygonal area with stray LINESTRING/POINT slivers
-- left over from the repair. That collection then flows, untouched, through `repaired` (0029:930-935,
-- which only filters NULL/empty, not geometry TYPE) into `tiled` (0029:943-966), where it is fed
-- straight into `ST_SimplifyPreserveTopology` and then `ST_Union`. 0029's own header (:897-901)
-- already names the failure mode this causes -- "unable to assign free hole to a shell" -- and
-- already carries the fix for OTHER callers of this pattern (0023's watershed rollup). It simply
-- never reached this CTE.
--
-- 0029 DOES call `ST_CollectionExtract(..., 3)` twice (:950-957 and :971) -- but both calls wrap the
-- OUTPUT of `ST_Union`, after the union has already been asked to reduce a GeometryCollection. By
-- then the damage is done: `ST_Union` either throws on the mixed-type input or, where it tolerates
-- it, produces geometry the two outer extract calls can only clean up post hoc for a union that may
-- already have computed the wrong thing. Extracting before the union stops non-polygonal slivers
-- from ever reaching `ST_SimplifyPreserveTopology` or `ST_Union` in the first place, which is the
-- actual mechanism 0023 and 0029's own header describe.
--
-- This is NOT an SFCGAL / backend problem, and a backend swap is explicitly the wrong lever here --
-- the bug is a missing repair-chain step in one CTE, present and correct everywhere else in this
-- file (`mv_soil_survey_grid`'s `delineation` CTE, two paragraphs above the broken one, has no
-- union in its path at all and was never exposed to this bug).
--
--
-- THE FIX. Move `ST_CollectionExtract(..., 3)` INTO the `delineation` CTE, applied directly to the
-- `ST_MakeValid` result, before `ST_SnapToGrid`'s repaired output ever reaches `repaired`, `tiled`,
-- `ST_SimplifyPreserveTopology` or either `ST_Union` call. The two existing outer extract calls in
-- `tiled` and in the final SELECT are left in place unchanged -- they are cheap, correct, and now
-- genuinely redundant defense-in-depth rather than the only repair step -- so this migration is a
-- one-line change to the `delineation` CTE and an otherwise byte-identical copy of 0029's
-- `mv_soil_survey_union` definition, its two indexes, and its comment.
--
-- Everything else about the relation is unchanged: same two zoom tiers (`regional-average` 0.0015,
-- `coarse-average` 0.005), same hierarchical per-one-degree-tile union that keeps the refresh's peak
-- allocation bounded (0029:891-895), same grain, same indexes, same "NOT YET READ BY ANYTHING"
-- status -- `readAggregatedFeatures` still unions the requesting bbox itself; repointing it is the
-- separate redesign 0029's comment already deferred, not part of this fix.
--
--
-- WHY THIS SHIPS DORMANT, like 0030-0033 before it.
--
-- This file is a DDL-only replacement: it drops and recreates `geo.mv_soil_survey_union` `WITH NO
-- DATA`, exactly as 0029 left it. It does not `REFRESH` the relation -- a first refresh over roughly
-- 239,000 delineations, hierarchically unioned, is real CPU and I/O on a 2 GB-capped box under live
-- ingest, and does not belong inside a schema migration's transaction. Refreshing is the job of the
-- existing `matview-refresh` jobs-pulse lane, which already carries `geo.mv_soil_survey_union` as a
-- registered spec (`services/agri-data-service/.../jobs/matview_refresh.py`) and will pick the fixed
-- definition up on its own schedule once this migration is registered and applied -- no code change
-- needed there. Nothing reads this relation yet (0029's own comment, still true), so there is no OID
-- trap of the kind `drizzle/0034` exists to record for `geo.features`-dependent matviews: no other
-- object's rewrite rule points at this one by OID, and dropping it breaks no dependent.
--
-- Left dormant (unregistered in `drizzle/meta/_journal.json`) so this can be reviewed and applied on
-- its own, independent of the partitioning work 0030-0033 are staged for. It is safe to register any
-- time; it has no ordering dependency on 0030, 0031, 0032, 0033 or 0034.
--
-- BEFORE REGISTERING THIS FILE:
--   1. Confirm 0029 has actually been applied to the target database -- the DO $$ block below
--      asserts this and refuses to proceed otherwise, but check it ahead of a live run regardless.
--   2. Confirm nobody has refreshed `geo.mv_soil_survey_union` out of band since the last check --
--      the DO $$ block also refuses to drop a relation that already holds data, since a populated
--      relation contradicts the "never once produced a row" premise this fix is written against and
--      dropping it here would destroy real rows rather than fix a bug.
--   3. After applying, do not assume the matview is fixed until the next `matview-refresh` tick
--      actually reports `outcome = 'refreshed'` (or `refreshed_concurrently`) in
--      `agri.matview_refresh_state` for `geo.mv_soil_survey_union` -- a first successful refresh is
--      the only real proof; a clean `apply` only proves the DDL parsed.

-- The assertion. Existence alone is not enough: a relation that exists but already holds data would
-- make this migration silently destroy rows instead of fixing an empty one. Both conditions are
-- checked before anything is dropped.
DO $$
DECLARE
  view_ref regclass := to_regclass('geo.mv_soil_survey_union');
  is_populated boolean;
BEGIN
  IF view_ref IS NULL THEN
    RAISE EXCEPTION
      'geo.mv_soil_survey_union does not exist. This migration assumes drizzle/0029_pre_aggregation_layer.sql has already been applied and created the broken relation this file replaces -- apply 0029 first, or if it was applied and the relation was since dropped by hand, investigate before reapplying blind.';
  END IF;

  SELECT c.relispopulated INTO is_populated
    FROM pg_class AS c
   WHERE c.oid = view_ref;

  IF COALESCE(is_populated, false) THEN
    RAISE EXCEPTION
      'geo.mv_soil_survey_union already holds data (relispopulated=true). This migration is written against the documented, measured fact that it has NEVER successfully refreshed in production. A populated relation means either the bug already stopped reproducing for some other reason or someone refreshed it out of band -- dropping it here would destroy real rows. Investigate before applying; do not proceed on this assumption alone.';
  END IF;
END $$;
--> statement-breakpoint

DROP MATERIALIZED VIEW geo.mv_soil_survey_union;
--> statement-breakpoint

-- Byte-identical to 0029's definition except for the one line inside `delineation` marked below.
CREATE MATERIALIZED VIEW geo.mv_soil_survey_union AS
WITH delineation AS (
  SELECT
    COALESCE(f.properties ->> 'drainageClass', 'unknown') AS drainage_class,
    CASE
      WHEN jsonb_typeof(f.properties -> 'hydric') = 'boolean'
        THEN (f.properties ->> 'hydric')::boolean
    END AS hydric,
    -- THE FIX: extract only the polygonal part of MakeValid's repair result HERE, before it can
    -- reach ST_SimplifyPreserveTopology or either ST_Union call below. 0029 omitted this and let a
    -- GeometryCollection (repaired area plus stray line/point slivers) flow all the way to the
    -- union, which is why this relation has never once produced a row.
    ST_CollectionExtract(ST_MakeValid(ST_SnapToGrid(f.geom, 0.000001)), 3) AS geom
  FROM geo.features AS f
  JOIN geo.layers AS l ON l.id = f.layer_id
  WHERE l.name = 'soil-survey'
    AND f.status = 'published'
    AND f.geom IS NOT NULL
),
-- Unchanged from 0029. ST_CollectionExtract of a MakeValid result with no polygonal part at all
-- returns an EMPTY geometry (never NULL), so this filter -- already written to catch exactly that --
-- covers the fixed CTE's output with no change needed.
repaired AS (
  SELECT drainage_class, hydric, geom
  FROM delineation
  WHERE geom IS NOT NULL
    AND NOT ST_IsEmpty(geom)
),
tier AS (
  SELECT * FROM (
    VALUES
      ('regional-average'::text, 0.0015::double precision),
      ('coarse-average',         0.005)
  ) AS t(zoom_tier, simplify_tolerance_degrees)
),
tiled AS (
  SELECT
    tier.zoom_tier,
    tier.simplify_tolerance_degrees,
    repaired.drainage_class,
    floor(ST_X(ST_Centroid(repaired.geom)))::integer AS tile_x,
    floor(ST_Y(ST_Centroid(repaired.geom)))::integer AS tile_y,
    -- Unchanged: now genuinely redundant defense-in-depth, since `repaired.geom` is already
    -- polygon-only by the time it reaches ST_SimplifyPreserveTopology and ST_Union above it.
    ST_CollectionExtract(
      ST_MakeValid(
        ST_Union(
          ST_SimplifyPreserveTopology(repaired.geom, tier.simplify_tolerance_degrees)
        )
      ),
      3
    ) AS geom,
    COUNT(*)::bigint AS map_unit_count,
    COUNT(*) FILTER (WHERE repaired.hydric)::bigint AS hydric_count,
    COUNT(*) FILTER (WHERE repaired.hydric IS NOT NULL)::bigint AS rated_count
  FROM repaired
  CROSS JOIN tier
  GROUP BY tier.zoom_tier, tier.simplify_tolerance_degrees, repaired.drainage_class,
           floor(ST_X(ST_Centroid(repaired.geom))),
           floor(ST_Y(ST_Centroid(repaired.geom)))
)
SELECT
  zoom_tier,
  simplify_tolerance_degrees,
  drainage_class,
  ST_CollectionExtract(ST_MakeValid(ST_Union(geom)), 3) AS geom,
  SUM(map_unit_count)::bigint AS map_unit_count,
  SUM(hydric_count)::bigint AS hydric_count,
  SUM(rated_count)::bigint AS rated_count
FROM tiled
GROUP BY zoom_tier, simplify_tolerance_degrees, drainage_class
ORDER BY zoom_tier, drainage_class
WITH NO DATA;
--> statement-breakpoint

CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_soil_survey_union
  ON geo.mv_soil_survey_union (zoom_tier, drainage_class);
--> statement-breakpoint

CREATE INDEX IF NOT EXISTS ix_mv_soil_survey_union_geom
  ON geo.mv_soil_survey_union USING GIST (geom);
--> statement-breakpoint

COMMENT ON MATERIALIZED VIEW geo.mv_soil_survey_union IS
  'Dissolved SSURGO drainage-class shapes at the two averaged zoom tiers. FIXED in '
  'drizzle/0035_soil_survey_union_collection_extract.sql: 0029''s delineation CTE ran ST_MakeValid '
  'with no ST_CollectionExtract, so a repaired GeometryCollection (real area plus stray line/point '
  'slivers) could reach ST_Union unfiltered -- the relation never once produced a row as a result. '
  'The extract now runs immediately after ST_MakeValid, before simplify or union. NOT YET READ BY '
  'ANYTHING: readAggregatedFeatures unions the requesting bbox at a caller-chosen tolerance, and a '
  'global dissolve would have to be ST_Intersection''d back -- strictly worse -- so repointing it is '
  'a redesign, not an edit. Built hierarchically -- per one-degree tile, then across tiles -- so the '
  'refresh peak stays inside a 2 GB cgroup. Each row is one large TOASTed geometry: clip with && '
  'before ST_Intersection, never project geom for an unbounded viewport.';
