-- Make watershed boundaries drawable at every zoom, instead of only from z7 in.
--
-- 0017 gave the layer a tile function that reads geo.features directly. That is correct and
-- stays correct close in, but it is why src/lib/map/layers.ts had to floor both watershed
-- style layers at minzoom 7: below that the whole 9,396-basin PNW set falls inside a handful
-- of tiles, so one request transforms, clips and encodes every basin in the region. The floor
-- traded a measured tile weight for a layer that silently vanished when you zoomed out --
-- which reads exactly like a layer with no data, the confusion this platform keeps having to
-- correct.
--
-- The fix is cartographic rather than a cap: draw the SAME hydrography at the resolution the
-- zoom can show. HUC codes are hierarchical by construction -- a HUC12's first 8 digits ARE
-- its HUC8, its first 6 its HUC6 -- so rolling members up by code prefix is not an invented
-- grouping, it is the classification USGS already published. Nothing is dropped at low zoom
-- and nothing is approximated into existence: a HUC6 polygon is exactly the union of the
-- HUC12s that compose it.
--
-- Levels are built hierarchically (12 -> 10 -> 8 -> 6 -> 4) rather than each from the base.
-- Unioning ten neighbours four times costs far less than unioning nine thousand four times,
-- and each stage simplifies before the next, so vertex count falls as it climbs.
CREATE MATERIALIZED VIEW IF NOT EXISTS geo.watershed_rollup AS
WITH detail AS (
  SELECT
    f.properties ->> 'huc12' AS huc,
    -- Published WBD outlines are not all OGC-valid: unioning the coastal basins raw fails
    -- with "unable to assign free hole to a shell" near -124.73, 48.38. Snapping to a ~0.1 m
    -- grid closes the near-coincident vertices that produce those slivers, MakeValid repairs
    -- what remains, and CollectionExtract keeps only the polygonal part -- a repair can hand
    -- back a GeometryCollection with stray lines, which ST_Union then refuses.
    ST_CollectionExtract(ST_MakeValid(ST_SnapToGrid(f.geom, 0.000001)), 3) AS geom,
    CASE
      WHEN jsonb_typeof(f.properties -> 'areasqkm') = 'number'
        THEN (f.properties ->> 'areasqkm')::double precision
    END AS areasqkm,
    geo.feature_observation_day(f.properties) AS observed_day
  FROM geo.features f
  JOIN geo.layers l ON f.layer_id = l.id
  WHERE l.name = 'watersheds'
    AND l.is_public IS TRUE
    AND f.status = 'published'
    AND f.geom IS NOT NULL
    -- A code that is not twelve digits cannot be truncated to a parent basin, so it takes no
    -- part in the rollup rather than forming a bogus group under a ragged prefix.
    AND f.properties ->> 'huc12' ~ '^[0-9]{12}$'
),
level_10 AS (
  SELECT
    10 AS huc_level,
    left(huc, 10) AS huc,
    ST_MakeValid(ST_SimplifyPreserveTopology(ST_Union(geom), 0.0015)) AS geom,
    sum(areasqkm) AS areasqkm,
    count(*)::integer AS basin_count,
    max(observed_day) AS observed_day
  FROM detail
  GROUP BY left(huc, 10)
),
level_8 AS (
  SELECT
    8 AS huc_level,
    left(huc, 8) AS huc,
    ST_MakeValid(ST_SimplifyPreserveTopology(ST_Union(geom), 0.005)) AS geom,
    sum(areasqkm) AS areasqkm,
    sum(basin_count)::integer AS basin_count,
    max(observed_day) AS observed_day
  FROM level_10
  GROUP BY left(huc, 8)
),
level_6 AS (
  SELECT
    6 AS huc_level,
    left(huc, 6) AS huc,
    ST_MakeValid(ST_SimplifyPreserveTopology(ST_Union(geom), 0.015)) AS geom,
    sum(areasqkm) AS areasqkm,
    sum(basin_count)::integer AS basin_count,
    max(observed_day) AS observed_day
  FROM level_8
  GROUP BY left(huc, 6)
),
level_4 AS (
  SELECT
    4 AS huc_level,
    left(huc, 4) AS huc,
    ST_MakeValid(ST_SimplifyPreserveTopology(ST_Union(geom), 0.04)) AS geom,
    sum(areasqkm) AS areasqkm,
    sum(basin_count)::integer AS basin_count,
    max(observed_day) AS observed_day
  FROM level_6
  GROUP BY left(huc, 4)
)
SELECT * FROM level_10
UNION ALL SELECT * FROM level_8
UNION ALL SELECT * FROM level_6
UNION ALL SELECT * FROM level_4;
--> statement-breakpoint

-- The tile function filters on both, and REFRESH MATERIALIZED VIEW CONCURRENTLY needs a
-- unique index to exist at all.
CREATE UNIQUE INDEX IF NOT EXISTS watershed_rollup_level_huc_idx
  ON geo.watershed_rollup (huc_level, huc);
--> statement-breakpoint

CREATE INDEX IF NOT EXISTS watershed_rollup_geom_idx
  ON geo.watershed_rollup USING GIST (geom);
--> statement-breakpoint

-- Boundaries change on the WBD's own schedule, which is rare, so this is called by whatever
-- reloads them -- `agri-cli ingest-watersheds` -- and never on a timer. CONCURRENTLY so a
-- refresh cannot blank the layer for the duration: readers keep the previous rollup until the
-- new one is complete.
CREATE OR REPLACE FUNCTION geo.refresh_watershed_rollup()
RETURNS void
LANGUAGE plpgsql
SET search_path = public, pg_catalog
AS $$
BEGIN
  REFRESH MATERIALIZED VIEW CONCURRENTLY geo.watershed_rollup;
END;
$$;
--> statement-breakpoint

-- Same MVT tag ('watersheds') and the same attribute names as before, because
-- src/lib/map/layers.ts declares that exact source-layer string and a mismatch renders
-- nothing while reporting no error -- the lesson 0012_burn_severity_tiles.sql records.
--
-- Two new attributes travel with every feature: `huc` (the code at whatever level was drawn)
-- and `huc_level`. They exist so the hover card can never label a HUC6 code as a HUC12 --
-- reusing the `huc12` field for a rollup would have been a mislabel of exactly the kind the
-- rest of this codebase spends its comments preventing. `huc12`, `name`, `tohuc`, `hutype`
-- stay NULL above the detail tier: a rolled-up basin has no single one of any of them, and a
-- member's value would be a lie about the whole.
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
