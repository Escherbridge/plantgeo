-- NOTE (2026-08-14): geo.sync_feature_geom_from_properties() (drizzle/0004) derives geom
-- from properties->'geometry' and DISCARDS any directly-supplied geom value. The geom
-- arguments in the inserts below are therefore dead; the properties 'geometry' member is
-- the value under test. Keep both in sync if you edit either.
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
      'geometry', '{"type":"Polygon","coordinates":[[[-121.45,44.2],[-121.450960736,44.190245484],[-121.453806023,44.180865828],[-121.458426519,44.172221488],[-121.464644661,44.164644661],[-121.472221488,44.158426519],[-121.480865828,44.153806023],[-121.490245484,44.150960736],[-121.5,44.15],[-121.509754516,44.150960736],[-121.519134172,44.153806023],[-121.527778512,44.158426519],[-121.535355339,44.164644661],[-121.541573481,44.172221488],[-121.546193977,44.180865828],[-121.549039264,44.190245484],[-121.55,44.2],[-121.549039264,44.209754516],[-121.546193977,44.219134172],[-121.541573481,44.227778512],[-121.535355339,44.235355339],[-121.527778512,44.241573481],[-121.519134172,44.246193977],[-121.509754516,44.249039264],[-121.5,44.25],[-121.490245484,44.249039264],[-121.480865828,44.246193977],[-121.472221488,44.241573481],[-121.464644661,44.235355339],[-121.458426519,44.227778512],[-121.453806023,44.219134172],[-121.450960736,44.209754516],[-121.45,44.2]]]}'::jsonb,
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
    jsonb_build_object(
      'geometry', '{"type":"Polygon","coordinates":[[[-121.55,44.1],[-121.550960736,44.090245484],[-121.553806023,44.080865828],[-121.558426519,44.072221488],[-121.564644661,44.064644661],[-121.572221488,44.058426519],[-121.580865828,44.053806023],[-121.590245484,44.050960736],[-121.6,44.05],[-121.609754516,44.050960736],[-121.619134172,44.053806023],[-121.627778512,44.058426519],[-121.635355339,44.064644661],[-121.641573481,44.072221488],[-121.646193977,44.080865828],[-121.649039264,44.090245484],[-121.65,44.1],[-121.649039264,44.109754516],[-121.646193977,44.119134172],[-121.641573481,44.127778512],[-121.635355339,44.135355339],[-121.627778512,44.141573481],[-121.619134172,44.146193977],[-121.609754516,44.149039264],[-121.6,44.15],[-121.590245484,44.149039264],[-121.580865828,44.146193977],[-121.572221488,44.141573481],[-121.564644661,44.135355339],[-121.558426519,44.127778512],[-121.553806023,44.119134172],[-121.550960736,44.109754516],[-121.55,44.1]]]}'::jsonb,
      'fireId', 'SHOULD-NEVER-SERVE',
      'acres', 2000
    )
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
      'geometry', '{"type":"Polygon","coordinates":[[[-121.35,44.3],[-121.350960736,44.290245484],[-121.353806023,44.280865828],[-121.358426519,44.272221488],[-121.364644661,44.264644661],[-121.372221488,44.258426519],[-121.380865828,44.253806023],[-121.390245484,44.250960736],[-121.4,44.25],[-121.409754516,44.250960736],[-121.419134172,44.253806023],[-121.427778512,44.258426519],[-121.435355339,44.264644661],[-121.441573481,44.272221488],[-121.446193977,44.280865828],[-121.449039264,44.290245484],[-121.45,44.3],[-121.449039264,44.309754516],[-121.446193977,44.319134172],[-121.441573481,44.327778512],[-121.435355339,44.335355339],[-121.427778512,44.341573481],[-121.419134172,44.346193977],[-121.409754516,44.349039264],[-121.4,44.35],[-121.390245484,44.349039264],[-121.380865828,44.346193977],[-121.372221488,44.341573481],[-121.364644661,44.335355339],[-121.358426519,44.327778512],[-121.353806023,44.319134172],[-121.350960736,44.309754516],[-121.35,44.3]]]}'::jsonb,
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
