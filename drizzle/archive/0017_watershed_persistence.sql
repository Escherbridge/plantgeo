-- Persist HUC12 watershed boundaries, the last map layer with no warehouse presence at all.
--
-- Until now `watersheds` was served entirely by proxying USGS NHDPlus_HR live on every
-- viewport change (src/lib/server/services/hydrosheds.ts, one-hour Redis cache). That has
-- three consequences this migration ends: the layer cannot answer the time slider at all,
-- because an upstream that only knows "now" cannot be asked "as of 2023"; it goes blank
-- whenever the hydrography service is down; and no warehouse-side reader can see it. It is
-- the last layer not covered by the 2026-08-03 decision "we persist everything we serve".
--
-- Column set and shape match the original seed in 0001_handy_riptide.sql exactly -- name,
-- type, description, is_public explicit, everything else left to its column default -- the
-- same discipline 0011_burn_severity_layer.sql followed.
INSERT INTO geo.layers (name, type, description, is_public)
VALUES
  ('watersheds', 'vector', 'USGS WBD HUC12 watershed boundaries', true)
ON CONFLICT (name) DO NOTHING;
--> statement-breakpoint

-- The tile function, carrying `observed_day` from the start rather than acquiring it in a
-- later migration the way the four layers in 0015 had to.
--
-- HUC12 boundaries are a SNAPSHOT: one row per basin, dated by the WBD's own `loaddate` and
-- refreshed in place by the ingest job. The day still matters and is still emitted, because
-- the slider filter treats every style-baked layer alike -- a basin whose boundary USGS
-- loaded in 2013 correctly draws at every date from 2013 onward, and a basin added to the
-- WBD later correctly does not appear before it existed.
--
-- Field names are the layer's own lowercase GeoJSON spellings (huc12, name, areasqkm, tohuc,
-- states, hutype), NOT the title-case aliases the ArcGIS catalog displays. That is the
-- spelling services/agri-data-service/.../ingest/watersheds.py writes and the spelling
-- src/lib/map/hover-fields.ts already reads for the proxied collection, so the tile path and
-- the proxy path present identical attributes and the hover card needs no branch.
--
-- The MVT tag is 'watersheds' and src/lib/map/layers.ts must declare that exact string as its
-- source-layer. A mismatch renders nothing and reports no error, so the two only ever change
-- together -- the lesson 0012_burn_severity_tiles.sql records.
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
  mvt bytea;
BEGIN
  bounds_3857 := ST_TileEnvelope(z, x, y);
  bounds_4326 := ST_Transform(bounds_3857, 4326);

  SELECT COALESCE(ST_AsMVT(tile, 'watersheds', 4096, 'geom'), ''::bytea) INTO mvt
  FROM (
    SELECT
      f.id,
      ST_AsMVTGeom(ST_Transform(f.geom, 3857), bounds_3857, 4096, 64, true) AS geom,
      f.properties ->> 'huc12' AS huc12,
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

  RETURN mvt;
END;
$$;
