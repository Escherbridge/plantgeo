-- NOTE (2026-08-14): geo.sync_feature_geom_from_properties() (drizzle/0004) derives geom
-- from properties->'geometry' and DISCARDS any directly-supplied geom value. The geom
-- arguments in the inserts below are therefore dead; the properties 'geometry' member is
-- the value under test. Keep both in sync if you edit either.
-- Manual SQL test for geo.sensor_tiles() (drizzle/0010_sensor_tile_properties.sql).
-- Same standalone-harness pattern as drizzle/tests/0009_evacuation_zone_tiles.test.sql:
-- no vitest/pg integration harness exists for direct-to-Postgres tile functions, so this
-- runs against the local podman postgis container and rolls back everything it does.
--
-- Run (PowerShell), after the migration above has been applied:
--   $env:PGPASSWORD = (podman exec plantgeo_postgis_1 printenv POSTGRES_PASSWORD)
--   & "$env:PGBIN\psql.exe" -h 127.0.0.1 -p 5434 -U geo -d plantgeo `
--       --set ON_ERROR_STOP=1 -f drizzle/tests/0010_sensor_tile_properties.test.sql
--
-- Every assertion raises on failure and the whole script rolls back at the end, so it
-- never leaves rows behind regardless of pass/fail. Reuses the seeded 'sensors'
-- geo.layers row (drizzle/0001_handy_riptide.sql) rather than inserting a second one,
-- since geo.layers.name is unique.

BEGIN;

DO $$
DECLARE
  sensors_layer_id uuid;
  published_id uuid;
  pending_id uuid;
  world_tile bytea;
  world_tile_without_published bytea;
BEGIN
  SELECT id INTO sensors_layer_id FROM geo.layers WHERE name = 'sensors';
  IF sensors_layer_id IS NULL THEN
    RAISE EXCEPTION 'expected the seeded sensors geo.layers row to exist';
  END IF;

  -- Published row, geometry inside the z0/x0/y0 tile (the whole world), with the
  -- property set the producer actually writes (sensors.py build_sensor_reading_write):
  -- network, sensor_id, station_name and the camelCase observedAt.
  INSERT INTO geo.features(layer_id, status, geom, properties)
  VALUES (
    sensors_layer_id,
    'published',
    ST_SetSRID(ST_MakePoint(-116.2, 43.6), 4326),
    jsonb_build_object(
      'geometry', '{"type":"Point","coordinates":[-116.2,43.6]}'::jsonb,
      'network', 'ASOS',
      'sensor_id', 'KBOI',
      'station_name', 'Boise Air Terminal',
      'observedAt', '2026-08-04T12:00:00+00:00'
    )
  )
  RETURNING id INTO published_id;

  -- Pending row: must never reach the tile regardless of geometry or properties.
  INSERT INTO geo.features(layer_id, status, geom, properties)
  VALUES (
    sensors_layer_id,
    'pending_review',
    ST_SetSRID(ST_MakePoint(-116.3, 43.5), 4326),
    jsonb_build_object(
      'geometry', '{"type":"Point","coordinates":[-116.3,43.5]}'::jsonb,
      'network', 'RAWS',
      'sensor_id', 'SHOULDNOTSHOW'
    )
  )
  RETURNING id INTO pending_id;

  world_tile := geo.sensor_tiles(0, 0, 0);
  IF world_tile IS NULL OR octet_length(world_tile) = 0 THEN
    RAISE EXCEPTION 'expected a non-empty tile covering the published station, got %', world_tile;
  END IF;
  RAISE NOTICE 'PASS: geo.sensor_tiles emits a non-empty tile for a published station';

  DELETE FROM geo.features WHERE id = published_id;

  world_tile_without_published := geo.sensor_tiles(0, 0, 0);
  IF world_tile_without_published IS NOT NULL AND octet_length(world_tile_without_published) > 0 THEN
    RAISE EXCEPTION 'expected an empty tile once only the pending_review station remains, got %',
      world_tile_without_published;
  END IF;
  RAISE NOTICE 'PASS: geo.sensor_tiles never serves a pending_review station';

  RAISE NOTICE 'ALL 0010 sensor_tile_properties tests passed';
END;
$$;

ROLLBACK;
