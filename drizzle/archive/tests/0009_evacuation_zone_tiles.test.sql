-- NOTE (2026-08-14): geo.sync_feature_geom_from_properties() (drizzle/0004) derives geom
-- from properties->'geometry' and DISCARDS any directly-supplied geom value. The geom
-- arguments in the inserts below are therefore dead; the properties 'geometry' member is
-- the value under test. Keep both in sync if you edit either.
-- Manual SQL test for geo.evacuation_zone_tiles() (drizzle/0009_evacuation_zone_tiles.sql).
-- Same standalone-harness pattern as drizzle/tests/0004_repair_ingested_geometries.test.sql:
-- no vitest/pg integration harness exists for direct-to-Postgres tile functions, so this
-- runs against the local podman postgis container and rolls back everything it does.
--
-- Run (PowerShell), after the migration above has been applied:
--   $env:PGPASSWORD = (podman exec plantgeo_postgis_1 printenv POSTGRES_PASSWORD)
--   & "$env:PGBIN\psql.exe" -h 127.0.0.1 -p 5434 -U geo -d plantgeo `
--       --set ON_ERROR_STOP=1 -f drizzle/tests/0009_evacuation_zone_tiles.test.sql
--
-- Every assertion raises on failure and the whole script rolls back at the end, so it
-- never leaves rows behind regardless of pass/fail. Reuses the seeded 'evacuation-zones'
-- geo.layers row (drizzle/0001_handy_riptide.sql:313) rather than inserting a second one,
-- since geo.layers.name is unique.

BEGIN;

DO $$
DECLARE
  zones_layer_id uuid;
  published_id uuid;
  pending_id uuid;
  world_tile bytea;
  world_tile_without_published bytea;
BEGIN
  SELECT id INTO zones_layer_id FROM geo.layers WHERE name = 'evacuation-zones';
  IF zones_layer_id IS NULL THEN
    RAISE EXCEPTION 'expected the seeded evacuation-zones geo.layers row to exist';
  END IF;

  -- Published row, geometry inside the z0/x0/y0 tile (the whole world), with the
  -- property set the migration's SELECT list actually reads.
  INSERT INTO geo.features(layer_id, status, geom, properties)
  VALUES (
    zones_layer_id,
    'published',
    ST_SetSRID(ST_MakePoint(-123.1, 44.9), 4326),
    jsonb_build_object(
      'geometry', '{"type":"Point","coordinates":[-123.1,44.9]}'::jsonb,
      'evacuationAreaName', 'Test Evacuation Area',
      'fireName', 'Test Fire',
      'county', 'Test County',
      'severity', 'high',
      'evacuationLevelLabel', 'Be Set',
      'structuresWithin', 12,
      'populationWithin', 34
    )
  )
  RETURNING id INTO published_id;

  -- Pending row: must never reach the tile regardless of geometry or properties.
  INSERT INTO geo.features(layer_id, status, geom, properties)
  VALUES (
    zones_layer_id,
    'pending_review',
    ST_SetSRID(ST_MakePoint(-123.2, 44.8), 4326),
    jsonb_build_object(
      'geometry', '{"type":"Point","coordinates":[-123.2,44.8]}'::jsonb,
      'evacuationAreaName', 'Should Never Serve',
      'severity', 'critical'
    )
  )
  RETURNING id INTO pending_id;

  world_tile := geo.evacuation_zone_tiles(0, 0, 0);
  IF world_tile IS NULL OR octet_length(world_tile) = 0 THEN
    RAISE EXCEPTION 'expected a non-empty tile covering the published zone, got %', world_tile;
  END IF;
  RAISE NOTICE 'PASS: geo.evacuation_zone_tiles emits a non-empty tile for a published zone';

  DELETE FROM geo.features WHERE id = published_id;

  world_tile_without_published := geo.evacuation_zone_tiles(0, 0, 0);
  IF world_tile_without_published IS NOT NULL AND octet_length(world_tile_without_published) > 0 THEN
    RAISE EXCEPTION 'expected an empty tile once only the pending_review zone remains, got %',
      world_tile_without_published;
  END IF;
  RAISE NOTICE 'PASS: geo.evacuation_zone_tiles never serves a pending_review zone';

  RAISE NOTICE 'ALL 0009 evacuation_zone_tiles tests passed';
END;
$$;

ROLLBACK;
