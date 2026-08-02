-- Manual SQL test for the geo.sync_feature_geom_from_properties() repair trigger
-- (drizzle/0004_repair_ingested_geometries.sql). No vitest/pg integration harness
-- exists yet for direct-to-Postgres trigger behavior, so this runs standalone
-- against the local podman postgis container and rolls back everything it does.
--
-- Run (PowerShell), after the migration above has been applied:
--   $env:PGPASSWORD = (podman exec plantgeo_postgis_1 printenv POSTGRES_PASSWORD)
--   & "$env:PGBIN\psql.exe" -h 127.0.0.1 -p 5434 -U geo -d plantgeo `
--       --set ON_ERROR_STOP=1 -f drizzle/tests/0004_repair_ingested_geometries.test.sql
--
-- Every assertion raises on failure and the whole script rolls back at the end,
-- so it never leaves rows behind regardless of pass/fail.

BEGIN;

DO $$
DECLARE
  test_layer_id uuid;
  repairable_id uuid;
  valid_id uuid;
  stored_properties jsonb;
  stored_geom geometry;
BEGIN
  INSERT INTO geo.layers(name, type)
  VALUES ('drizzle-0004-test-layer', 'vector')
  RETURNING id INTO test_layer_id;

  -- 1. Repairable self-intersecting ("bowtie") polygon: accepted, repaired, and
  --    flagged. The web read paths serve properties, not geom, so the repaired
  --    shape and the flag must both land in properties.
  INSERT INTO geo.features(layer_id, properties)
  VALUES (
    test_layer_id,
    jsonb_build_object(
      'geometry',
      '{"type":"Polygon","coordinates":[[[0,0],[2,2],[2,0],[0,2],[0,0]]]}'::jsonb
    )
  )
  RETURNING id INTO repairable_id;

  SELECT properties, geom INTO stored_properties, stored_geom
    FROM geo.features WHERE id = repairable_id;

  IF stored_geom IS NULL OR NOT ST_IsValid(stored_geom) THEN
    RAISE EXCEPTION 'repairable polygon: expected a valid repaired geom, got %', stored_geom;
  END IF;
  IF (stored_properties ->> 'geometry_repaired') IS DISTINCT FROM 'true' THEN
    RAISE EXCEPTION 'repairable polygon: expected properties.geometry_repaired = true, got %',
      stored_properties -> 'geometry_repaired';
  END IF;
  IF NOT ST_Equals(
    ST_GeomFromGeoJSON((stored_properties -> 'geometry')::text),
    stored_geom
  ) THEN
    RAISE EXCEPTION 'repairable polygon: properties.geometry must match the repaired geom, got % vs %',
      stored_properties -> 'geometry', ST_AsGeoJSON(stored_geom);
  END IF;
  RAISE NOTICE 'PASS: repairable self-intersecting polygon is accepted, repaired, and flagged';

  -- 2. Unrepairable geometry: a MultiPolygon whose two parts are both
  --    degenerate (one collapses to a line, the other to a point).
  --    ST_MakeValid returns a GEOMETRYCOLLECTION with no polygonal member at
  --    all, so ST_CollectionExtract(..., 3) yields an empty MultiPolygon and
  --    the insert must be rejected rather than silently accepted as a line
  --    or point where a polygon was declared.
  BEGIN
    INSERT INTO geo.features(layer_id, properties)
    VALUES (
      test_layer_id,
      jsonb_build_object(
        'geometry',
        '{"type":"MultiPolygon","coordinates":[[[[0,0],[10,0],[5,5],[10,0],[0,0]]],[[[50,50],[50,50],[50,50],[50,50]]]]}'::jsonb
      )
    );
    RAISE EXCEPTION 'unrepairable multipolygon: expected the insert to be rejected, but it succeeded';
  EXCEPTION WHEN SQLSTATE '22023' THEN
    RAISE NOTICE 'PASS: unrepairable multipolygon (no polygonal part survives repair) is rejected (%)', SQLERRM;
  END;

  -- 3. Already-valid geometry: accepted untouched, with no repair flag at all
  --    (not merely `false`).
  INSERT INTO geo.features(layer_id, properties)
  VALUES (
    test_layer_id,
    jsonb_build_object(
      'geometry',
      '{"type":"Polygon","coordinates":[[[0,0],[0,1],[1,1],[1,0],[0,0]]]}'::jsonb
    )
  )
  RETURNING id INTO valid_id;

  SELECT properties, geom INTO stored_properties, stored_geom
    FROM geo.features WHERE id = valid_id;

  IF stored_geom IS NULL OR NOT ST_IsValid(stored_geom) THEN
    RAISE EXCEPTION 'already-valid polygon: expected a valid geom, got %', stored_geom;
  END IF;
  IF stored_properties ? 'geometry_repaired' THEN
    RAISE EXCEPTION 'already-valid polygon: expected no geometry_repaired key, got %',
      stored_properties -> 'geometry_repaired';
  END IF;
  RAISE NOTICE 'PASS: already-valid polygon is stored untouched with no repair flag';

  RAISE NOTICE 'ALL 0004 geometry-repair trigger tests passed';
END;
$$;

ROLLBACK;
