-- Project `category` out of `geo.intervention_tiles()` so the published tile layers can paint
-- by the classification every submission actually carries.
--
-- Why: the three published intervention style layers painted off `properties ->> 'priority'`,
-- which `interventions.submitIntervention` has never written -- so essentially every real row
-- fell to the neutral fallback colour. `category` ('land' | 'air') IS written on every
-- submission, and as of 2026-09-13 the six merged intervention style layers share one
-- status/category paint expression (INTERVENTION_STATUS_COLOR in src/lib/map/layers.ts). The
-- draft overlay already had `category` because it is fed client-side; the published tiles did
-- not, so a published site would have drawn grey until this column existed.
--
-- `status` stays projected and stays pinned to 'published' in the WHERE clause below: this
-- function is the PUBLISHED source, and the merged toggle's `pending_review` half arrives from
-- the client-side GeoJSON overlay, never from here. The attribute is projected anyway so the
-- shared expression evaluates identically against both sources.
--
-- `priority` is kept in the projection. Nothing paints it any more, but the ingested zone rows
-- that do carry one keep their attribute available to a tile consumer, and dropping a column is
-- the kind of change that breaks a reader nobody remembered.
--
-- AFTER APPLYING THIS: RESTART MARTIN. Martin caches the function's column set at startup, so a
-- correct migration with no restart silently keeps serving the old attributes -- see
-- "Restart Martin after a tile migration" and infra/martin/AGENTS.md.

CREATE OR REPLACE FUNCTION geo.intervention_tiles(z integer, x integer, y integer) RETURNS bytea
    LANGUAGE plpgsql STABLE PARALLEL SAFE
    SET search_path TO 'public', 'pg_catalog'
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
      f.properties ->> 'category' AS category,
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
