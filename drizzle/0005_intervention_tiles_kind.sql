-- Project `kind` out of `geo.intervention_tiles()` so a published strategy request paints as a
-- request on the Martin-served tile source, not merely as a land-category feature.
--
-- Why: `INTERVENTION_STATUS_COLOR` in src/lib/map/layers.ts leads with
-- `["==", ["get", "kind"], "request"]`, deliberately ahead of the status/category arms, because a
-- request is always `published` and would otherwise be indistinguishable from a recommendation.
-- That attribute reaches the client from the signed-in drafts GeoJSON overlay, which is fed
-- client-side from the properties bag -- but the PUBLISHED tile source is this function, and it
-- did not project `kind`. A request served through Martin therefore fell through to the category
-- arm: degraded, never wrong, and exactly the gap layers.ts's own comment says this migration
-- closes.
--
-- This is the same one-column widening `0002_intervention_tiles_category.sql` performed for
-- `category`; the WHERE clause, the `published` pin, the `LIMIT` and every other projected
-- attribute are byte-identical to that migration's definition. Nothing else about the function
-- changes.
--
-- AFTER APPLYING THIS: RESTART MARTIN -- the SAME caveat 0002 carries, and it applies again here.
-- Martin caches each tile function's column set at startup, so a correct migration with no restart
-- keeps serving the old attribute set and the request colour silently never appears. See
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
      f.properties ->> 'kind' AS kind,
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
