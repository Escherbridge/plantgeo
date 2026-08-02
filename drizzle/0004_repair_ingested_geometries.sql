-- Upstream polygons (e.g. WFIGS fire perimeters) routinely self-intersect.
-- Repair invalid geometries with ST_MakeValid instead of rejecting the row,
-- but never accept a geometry that remains invalid or empty after repair.
CREATE OR REPLACE FUNCTION geo.sync_feature_geom_from_properties()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  parsed geometry;
  repaired geometry;
BEGIN
  IF jsonb_typeof(NEW.properties -> 'geometry') <> 'object' THEN
    NEW.geom := NULL;
    RETURN NEW;
  END IF;

  BEGIN
    parsed := ST_GeomFromGeoJSON((NEW.properties -> 'geometry')::text);
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'geo.features.properties.geometry must be valid GeoJSON: %', SQLERRM
      USING ERRCODE = '22023';
  END;

  IF ST_SRID(parsed) = 0 THEN
    parsed := ST_SetSRID(parsed, 4326);
  END IF;

  IF ST_SRID(parsed) <> 4326 THEN
    RAISE EXCEPTION 'geo.features.properties.geometry must be a valid EPSG:4326 geometry'
      USING ERRCODE = '22023';
  END IF;

  IF NOT ST_IsValid(parsed) THEN
    repaired := ST_MakeValid(parsed);
    IF GeometryType(repaired) = 'GEOMETRYCOLLECTION' THEN
      -- Keep components matching the original topological dimension
      -- (1=point, 2=line, 3=polygon for ST_CollectionExtract).
      repaired := ST_CollectionExtract(repaired, ST_Dimension(parsed) + 1);
    END IF;
    IF repaired IS NULL OR ST_IsEmpty(repaired) OR NOT ST_IsValid(repaired) THEN
      RAISE EXCEPTION 'geo.features.properties.geometry must be a valid EPSG:4326 geometry'
        USING ERRCODE = '22023';
    END IF;
    parsed := repaired;
    -- Web read paths serve properties, not geom: write the repaired geometry
    -- back and flag the repair so callers know the shape changed.
    NEW.properties := jsonb_set(
      jsonb_set(NEW.properties, '{geometry}', ST_AsGeoJSON(parsed)::jsonb),
      '{geometry_repaired}',
      'true'::jsonb
    );
  END IF;

  NEW.geom := parsed;
  RETURN NEW;
END;
$$;
