-- Manual SQL test for geo.burn_severity_tiles() (drizzle/0012_burn_severity_tiles.sql).
-- Same standalone-harness pattern as drizzle/tests/0009_evacuation_zone_tiles.test.sql:
-- no vitest/pg integration harness exists for direct-to-Postgres tile functions, so this
-- runs against the local podman postgis container and rolls back everything it does.
--
-- Run (PowerShell), after the migration above has been applied:
--   $env:PGPASSWORD = (podman exec plantgeo_postgis_1 printenv POSTGRES_PASSWORD)
--   & "$env:PGBIN\psql.exe" -h 127.0.0.1 -p 5434 -U geo -d plantgeo `
--       --set ON_ERROR_STOP=1 -f drizzle/tests/0012_burn_severity_tiles.test.sql
--
-- Every assertion raises on failure and the whole script rolls back at the end, so it
-- never leaves rows behind regardless of pass/fail. Reuses the 'burn-severity'
-- geo.layers row inserted by drizzle/0011_burn_severity_layer.sql rather than
-- inserting a second one, since geo.layers.name is unique -- note that unlike
-- 'evacuation-zones' and 'sensors' this row is NOT part of the 0001 seed, so a
-- database migrated only through 0010 will fail the first assertion below.

BEGIN;

DO $$
DECLARE
  burn_layer_id uuid;
  published_id uuid;
  pending_id uuid;
  malformed_id uuid;
  world_tile bytea;
  world_tile_without_published bytea;
BEGIN
  SELECT id INTO burn_layer_id FROM geo.layers WHERE name = 'burn-severity';
  IF burn_layer_id IS NULL THEN
    RAISE EXCEPTION 'expected the burn-severity geo.layers row from 0011 to exist';
  END IF;

  -- Published row, geometry inside the z0/x0/y0 tile (the whole world), carrying the
  -- property set mtbs.py build_mtbs_write actually writes. severityClass is null on
  -- purpose: that is what every one of the 478 published rows carries, because the MTBS
  -- polygon layer publishes no polygon-level severity class.
  INSERT INTO geo.features(layer_id, status, geom, properties)
  VALUES (
    burn_layer_id,
    'published',
    ST_Buffer(ST_SetSRID(ST_MakePoint(-121.5, 44.2), 4326), 0.05),
    jsonb_build_object(
      'fireId', 'OR4420112150020200817',
      'fireName', 'Test Burn',
      'fireYear', 2020,
      'ignitionDate', '2020-08-17',
      'fireType', 'Wildfire',
      'assessmentType', 'Extended',
      'acres', 11540.5,
      'severityClass', NULL,
      'observedAt', '2022-04-28T00:00:00+00:00'
    )
  )
  RETURNING id INTO published_id;

  -- Pending row: must never reach the tile regardless of geometry or properties.
  INSERT INTO geo.features(layer_id, status, geom, properties)
  VALUES (
    burn_layer_id,
    'pending_review',
    ST_Buffer(ST_SetSRID(ST_MakePoint(-121.6, 44.1), 4326), 0.05),
    jsonb_build_object('fireId', 'SHOULD-NEVER-SERVE', 'acres', 2000)
  )
  RETURNING id INTO pending_id;

  -- Malformed numerics: the jsonb_typeof guards on acres and fire_year exist so one bad
  -- upstream row costs two attributes on one feature rather than a 500 for the whole tile.
  -- A bare ::double precision cast makes this INSERT's presence fail the next assertion.
  INSERT INTO geo.features(layer_id, status, geom, properties)
  VALUES (
    burn_layer_id,
    'published',
    ST_Buffer(ST_SetSRID(ST_MakePoint(-121.4, 44.3), 4326), 0.05),
    jsonb_build_object(
      'fireId', 'MALFORMED-NUMERICS',
      'acres', 'not-a-number',
      'fireYear', 'not-a-year'
    )
  )
  RETURNING id INTO malformed_id;

  world_tile := geo.burn_severity_tiles(0, 0, 0);
  IF world_tile IS NULL OR octet_length(world_tile) = 0 THEN
    RAISE EXCEPTION 'expected a non-empty tile covering the published burn scar, got %', world_tile;
  END IF;
  RAISE NOTICE 'PASS: geo.burn_severity_tiles emits a non-empty tile for a published burn scar';
  RAISE NOTICE 'PASS: a non-numeric acres/fireYear does not fault the tile';

  -- The MVT layer name is embedded in the protobuf as a literal ASCII string, and
  -- src/lib/map/layers.ts burnSeverityLayer declares it as its source-layer. A mismatch
  -- renders nothing and reports no error anywhere, which is why it is asserted here.
  IF strpos(encode(world_tile, 'escape'), 'burn_severity') = 0 THEN
    RAISE EXCEPTION 'expected the MVT layer name burn_severity to be embedded in the tile';
  END IF;
  RAISE NOTICE 'PASS: the emitted MVT layer name is burn_severity';

  DELETE FROM geo.features WHERE id IN (published_id, malformed_id);

  world_tile_without_published := geo.burn_severity_tiles(0, 0, 0);
  IF world_tile_without_published IS NOT NULL AND octet_length(world_tile_without_published) > 0 THEN
    RAISE EXCEPTION 'expected an empty tile once only the pending_review scar remains, got %',
      world_tile_without_published;
  END IF;
  RAISE NOTICE 'PASS: geo.burn_severity_tiles never serves a pending_review scar';

  RAISE NOTICE 'ALL 0012 burn_severity_tiles tests passed';
END;
$$;

ROLLBACK;
