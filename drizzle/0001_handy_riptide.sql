-- Database schema is owned by Drizzle migrations. Container initialization only
-- installs extensions; it must not create tables, functions, or seed records.
CREATE SCHEMA IF NOT EXISTS geo;
--> statement-breakpoint
CREATE SCHEMA IF NOT EXISTS tracking;
--> statement-breakpoint

ALTER TABLE public.users
  ADD COLUMN IF NOT EXISTS email_verified timestamptz,
  ADD COLUMN IF NOT EXISTS image text;
--> statement-breakpoint

CREATE TABLE IF NOT EXISTS public.agricultural_solutions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name varchar(100) NOT NULL UNIQUE,
  description text,
  suitability_rules jsonb DEFAULT '{}'::jsonb,
  created_at timestamptz DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS public.open_plant_data (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scientific_name varchar(200) NOT NULL,
  common_name varchar(200),
  solution_id uuid,
  climate_requirements jsonb DEFAULT '{}'::jsonb,
  water_requirements jsonb DEFAULT '{}'::jsonb,
  soil_requirements jsonb DEFAULT '{}'::jsonb,
  metadata jsonb DEFAULT '{}'::jsonb,
  created_at timestamptz DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS public.open_tooling_data (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name varchar(200) NOT NULL,
  solution_id uuid,
  category varchar(100),
  specifications jsonb DEFAULT '{}'::jsonb,
  metadata jsonb DEFAULT '{}'::jsonb,
  created_at timestamptz DEFAULT now()
);
--> statement-breakpoint
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'open_plant_data_solution_id_agricultural_solutions_id_fk'
      AND conrelid = 'public.open_plant_data'::regclass
  ) THEN
    ALTER TABLE public.open_plant_data
      ADD CONSTRAINT open_plant_data_solution_id_agricultural_solutions_id_fk
      FOREIGN KEY (solution_id) REFERENCES public.agricultural_solutions(id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'open_tooling_data_solution_id_agricultural_solutions_id_fk'
      AND conrelid = 'public.open_tooling_data'::regclass
  ) THEN
    ALTER TABLE public.open_tooling_data
      ADD CONSTRAINT open_tooling_data_solution_id_agricultural_solutions_id_fk
      FOREIGN KEY (solution_id) REFERENCES public.agricultural_solutions(id);
  END IF;
END $$;
--> statement-breakpoint

CREATE TABLE IF NOT EXISTS geo.historical_fire_data (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  date_bucket timestamptz NOT NULL,
  lat double precision NOT NULL,
  lon double precision NOT NULL,
  geom geometry(POINT, 4326),
  fire_risk_score double precision,
  detected_anomalies integer DEFAULT 0,
  metadata jsonb DEFAULT '{}'::jsonb,
  created_at timestamptz DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS geo.historical_water_drought (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  date_bucket timestamptz NOT NULL,
  lat double precision NOT NULL,
  lon double precision NOT NULL,
  geom geometry(POINT, 4326),
  water_scarcity_index double precision,
  streamflow_cfs double precision,
  metadata jsonb DEFAULT '{}'::jsonb,
  created_at timestamptz DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS geo.historical_vegetation (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  date_bucket timestamptz NOT NULL,
  lat double precision NOT NULL,
  lon double precision NOT NULL,
  geom geometry(POINT, 4326),
  ndvi_value double precision,
  ecological_health_index double precision,
  metadata jsonb DEFAULT '{}'::jsonb,
  created_at timestamptz DEFAULT now()
);
--> statement-breakpoint

ALTER TABLE geo.features ADD COLUMN IF NOT EXISTS geom geometry(GEOMETRY, 4326);
--> statement-breakpoint
ALTER TABLE geo.fire_detections ADD COLUMN IF NOT EXISTS geom geometry(POINT, 4326);
--> statement-breakpoint
ALTER TABLE geo.poi ADD COLUMN IF NOT EXISTS geom geometry(POINT, 4326);
--> statement-breakpoint
ALTER TABLE tracking.positions ADD COLUMN IF NOT EXISTS geom geography(POINT, 4326);
--> statement-breakpoint
ALTER TABLE geo.historical_fire_data ADD COLUMN IF NOT EXISTS geom geometry(POINT, 4326);
--> statement-breakpoint
ALTER TABLE geo.historical_water_drought ADD COLUMN IF NOT EXISTS geom geometry(POINT, 4326);
--> statement-breakpoint
ALTER TABLE geo.historical_vegetation ADD COLUMN IF NOT EXISTS geom geometry(POINT, 4326);
--> statement-breakpoint

-- Invalid legacy GeoJSON is deliberately skipped so an upgrade remains atomic.
-- Repair it after migration with:
-- SELECT id FROM geo.features WHERE properties ? 'geometry' AND geom IS NULL;
DO $$
DECLARE
  feature_row record;
  parsed geometry;
BEGIN
  FOR feature_row IN
    SELECT id, properties -> 'geometry' AS geometry_json
    FROM geo.features
    WHERE geom IS NULL
      AND jsonb_typeof(properties -> 'geometry') = 'object'
  LOOP
    BEGIN
      parsed := ST_GeomFromGeoJSON(feature_row.geometry_json::text);
      IF ST_SRID(parsed) = 0 THEN
        parsed := ST_SetSRID(parsed, 4326);
      END IF;

      IF ST_SRID(parsed) = 4326 AND ST_IsValid(parsed) THEN
        UPDATE geo.features SET geom = parsed WHERE id = feature_row.id;
      ELSE
        RAISE WARNING 'Skipping invalid or non-4326 geometry for geo.features row %', feature_row.id;
      END IF;
    EXCEPTION WHEN OTHERS THEN
      RAISE WARNING 'Skipping unreadable GeoJSON for geo.features row %: %', feature_row.id, SQLERRM;
    END;
  END LOOP;
END $$;
--> statement-breakpoint

CREATE OR REPLACE FUNCTION geo.sync_feature_geom_from_properties()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  parsed geometry;
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

  IF ST_SRID(parsed) <> 4326 OR NOT ST_IsValid(parsed) THEN
    RAISE EXCEPTION 'geo.features.properties.geometry must be a valid EPSG:4326 geometry'
      USING ERRCODE = '22023';
  END IF;

  NEW.geom := parsed;
  RETURN NEW;
END;
$$;
--> statement-breakpoint
DROP TRIGGER IF EXISTS geo_features_sync_geom ON geo.features;
CREATE TRIGGER geo_features_sync_geom
BEFORE INSERT OR UPDATE OF properties ON geo.features
FOR EACH ROW EXECUTE FUNCTION geo.sync_feature_geom_from_properties();
--> statement-breakpoint

CREATE OR REPLACE FUNCTION geo.sync_historical_point_geom()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.lat BETWEEN -90 AND 90 AND NEW.lon BETWEEN -180 AND 180 THEN
    NEW.geom := ST_SetSRID(ST_MakePoint(NEW.lon, NEW.lat), 4326);
  ELSE
    NEW.geom := NULL;
  END IF;
  RETURN NEW;
END;
$$;
--> statement-breakpoint
UPDATE geo.historical_fire_data
SET geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326)
WHERE geom IS NULL AND lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180;
UPDATE geo.historical_water_drought
SET geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326)
WHERE geom IS NULL AND lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180;
UPDATE geo.historical_vegetation
SET geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326)
WHERE geom IS NULL AND lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180;
--> statement-breakpoint
DROP TRIGGER IF EXISTS geo_historical_fire_data_sync_geom ON geo.historical_fire_data;
CREATE TRIGGER geo_historical_fire_data_sync_geom
BEFORE INSERT OR UPDATE OF lat, lon ON geo.historical_fire_data
FOR EACH ROW EXECUTE FUNCTION geo.sync_historical_point_geom();
DROP TRIGGER IF EXISTS geo_historical_water_drought_sync_geom ON geo.historical_water_drought;
CREATE TRIGGER geo_historical_water_drought_sync_geom
BEFORE INSERT OR UPDATE OF lat, lon ON geo.historical_water_drought
FOR EACH ROW EXECUTE FUNCTION geo.sync_historical_point_geom();
DROP TRIGGER IF EXISTS geo_historical_vegetation_sync_geom ON geo.historical_vegetation;
CREATE TRIGGER geo_historical_vegetation_sync_geom
BEFORE INSERT OR UPDATE OF lat, lon ON geo.historical_vegetation
FOR EACH ROW EXECUTE FUNCTION geo.sync_historical_point_geom();
--> statement-breakpoint

CREATE OR REPLACE FUNCTION tracking.sync_position_geom_from_metadata()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  latitude double precision;
  longitude double precision;
BEGIN
  IF jsonb_typeof(NEW.metadata -> 'lat') = 'number'
    AND jsonb_typeof(NEW.metadata -> 'lon') = 'number' THEN
    latitude := (NEW.metadata ->> 'lat')::double precision;
    longitude := (NEW.metadata ->> 'lon')::double precision;
    IF latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180 THEN
      NEW.geom := ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography;
      RETURN NEW;
    END IF;
  END IF;

  NEW.geom := NULL;
  RETURN NEW;
END;
$$;
--> statement-breakpoint
UPDATE tracking.positions
SET geom = ST_SetSRID(
  ST_MakePoint((metadata ->> 'lon')::double precision, (metadata ->> 'lat')::double precision),
  4326
)::geography
WHERE geom IS NULL
  AND jsonb_typeof(metadata -> 'lat') = 'number'
  AND jsonb_typeof(metadata -> 'lon') = 'number'
  AND (metadata ->> 'lat')::double precision BETWEEN -90 AND 90
  AND (metadata ->> 'lon')::double precision BETWEEN -180 AND 180;
--> statement-breakpoint
DROP TRIGGER IF EXISTS tracking_positions_sync_geom ON tracking.positions;
CREATE TRIGGER tracking_positions_sync_geom
BEFORE INSERT OR UPDATE OF metadata ON tracking.positions
FOR EACH ROW EXECUTE FUNCTION tracking.sync_position_geom_from_metadata();
--> statement-breakpoint

CREATE INDEX IF NOT EXISTS idx_features_geom ON geo.features USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_features_layer ON geo.features (layer_id);
CREATE INDEX IF NOT EXISTS idx_features_layer_status ON geo.features (layer_id, status);
CREATE INDEX IF NOT EXISTS idx_features_properties ON geo.features USING GIN (properties);
CREATE INDEX IF NOT EXISTS idx_features_layer_external_id_lookup
  ON geo.features (layer_id, (properties ->> 'id'))
  WHERE properties ? 'id';
CREATE UNIQUE INDEX IF NOT EXISTS features_layer_external_id_unique
  ON geo.features (layer_id, (properties ->> 'id'))
  WHERE properties ? 'id';
CREATE INDEX IF NOT EXISTS idx_fire_detections_geom ON geo.fire_detections USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_poi_geom ON geo.poi USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_positions_asset ON tracking.positions (asset_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_positions_geom ON tracking.positions USING GIST (geom);
CREATE UNIQUE INDEX IF NOT EXISTS api_keys_key_hash_unique ON public.api_keys (key_hash);
CREATE INDEX IF NOT EXISTS idx_historical_fire_data_geom ON geo.historical_fire_data USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_historical_fire_data_location ON geo.historical_fire_data (lat, lon, date_bucket DESC);
CREATE INDEX IF NOT EXISTS idx_historical_water_drought_geom ON geo.historical_water_drought USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_historical_water_drought_location ON geo.historical_water_drought (lat, lon, date_bucket DESC);
CREATE INDEX IF NOT EXISTS idx_historical_vegetation_geom ON geo.historical_vegetation USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_historical_vegetation_location ON geo.historical_vegetation (lat, lon, date_bucket DESC);
--> statement-breakpoint

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
    PERFORM create_hypertable(
      'tracking.positions',
      'time',
      chunk_time_interval => INTERVAL '1 day',
      if_not_exists => TRUE,
      migrate_data => TRUE
    );
  ELSE
    RAISE NOTICE 'timescaledb extension is unavailable; tracking.positions remains a standard table';
  END IF;
END $$;
--> statement-breakpoint

INSERT INTO geo.layers (name, type, description, is_public)
VALUES
  ('fire-perimeters', 'vector', 'Active wildfire perimeters', true),
  ('fire-detections', 'vector', 'Near-real-time fire detections', true),
  ('water-gauges', 'vector', 'Current persisted USGS streamflow observations', true),
  ('weather-observations', 'vector', 'Current persisted weather observations', true),
  ('evacuation-zones', 'vector', 'Evacuation zone boundaries', true),
  ('sensors', 'vector', 'Environmental sensor locations', true),
  ('vegetation', 'vector', 'Vegetation coverage areas', true),
  ('interventions', 'vector', 'Ecosystem intervention sites', true)
ON CONFLICT (name) DO NOTHING;
--> statement-breakpoint

CREATE TABLE IF NOT EXISTS geo.osm_buildings (
  id bigint PRIMARY KEY,
  geom geometry(GEOMETRY, 4326) NOT NULL,
  name text,
  building_type text,
  height real,
  levels integer,
  tags jsonb DEFAULT '{}'::jsonb,
  is_public boolean NOT NULL DEFAULT true
);
CREATE TABLE IF NOT EXISTS geo.osm_roads (
  id bigint PRIMARY KEY,
  geom geometry(LINESTRING, 4326) NOT NULL,
  name text,
  highway text NOT NULL,
  surface text,
  oneway boolean DEFAULT false,
  lanes integer,
  maxspeed integer,
  tags jsonb DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS geo.osm_waterways (
  id bigint PRIMARY KEY,
  geom geometry(GEOMETRY, 4326) NOT NULL,
  name text,
  waterway text NOT NULL,
  tags jsonb DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS geo.osm_landuse (
  id bigint PRIMARY KEY,
  geom geometry(GEOMETRY, 4326) NOT NULL,
  name text,
  landuse text,
  leisure text,
  "natural" text,
  tags jsonb DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS geo.osm_pois (
  id bigint PRIMARY KEY,
  geom geometry(POINT, 4326) NOT NULL,
  name text,
  amenity text,
  shop text,
  tourism text,
  tags jsonb DEFAULT '{}'::jsonb
);
ALTER TABLE geo.osm_buildings ADD COLUMN IF NOT EXISTS is_public boolean NOT NULL DEFAULT true;
CREATE INDEX IF NOT EXISTS idx_osm_buildings_geom ON geo.osm_buildings USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_osm_roads_geom ON geo.osm_roads USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_osm_roads_highway ON geo.osm_roads (highway);
CREATE INDEX IF NOT EXISTS idx_osm_waterways_geom ON geo.osm_waterways USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_osm_landuse_geom ON geo.osm_landuse USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_osm_pois_geom ON geo.osm_pois USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_osm_pois_amenity ON geo.osm_pois (amenity);
--> statement-breakpoint

CREATE OR REPLACE FUNCTION geo.fire_risk_tiles(z integer, x integer, y integer)
RETURNS bytea
LANGUAGE plpgsql
STABLE
PARALLEL SAFE
AS $$
DECLARE
  bounds_3857 geometry;
  bounds_4326 geometry;
  mvt bytea;
BEGIN
  bounds_3857 := ST_TileEnvelope(z, x, y);
  bounds_4326 := ST_Transform(bounds_3857, 4326);

  SELECT COALESCE(ST_AsMVT(tile, 'fire_risk', 4096, 'geom'), ''::bytea) INTO mvt
  FROM (
    SELECT
      f.id,
      ST_AsMVTGeom(ST_Transform(f.geom, 3857), bounds_3857, 4096, 64, true) AS geom,
      f.properties ->> 'risk_level' AS risk_level,
      f.properties ->> 'severity' AS severity,
      f.properties ->> 'name' AS name
    FROM geo.features f
    JOIN geo.layers l ON f.layer_id = l.id
    WHERE l.name = 'fire-perimeters'
      AND l.is_public IS TRUE
      AND f.status = 'published'
      AND f.geom IS NOT NULL
      AND f.geom && bounds_4326
      AND ST_Intersects(f.geom, bounds_4326)
  ) AS tile;

  RETURN mvt;
END;
$$;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION geo.sensor_tiles(z integer, x integer, y integer)
RETURNS bytea
LANGUAGE plpgsql
STABLE
PARALLEL SAFE
AS $$
DECLARE
  bounds_3857 geometry;
  bounds_4326 geometry;
  mvt bytea;
BEGIN
  bounds_3857 := ST_TileEnvelope(z, x, y);
  bounds_4326 := ST_Transform(bounds_3857, 4326);

  SELECT COALESCE(ST_AsMVT(tile, 'sensors', 4096, 'geom'), ''::bytea) INTO mvt
  FROM (
    SELECT
      f.id,
      ST_AsMVTGeom(ST_Transform(f.geom, 3857), bounds_3857, 4096, 64, true) AS geom,
      f.properties ->> 'sensor_type' AS sensor_type,
      f.properties ->> 'status' AS status,
      f.properties ->> 'name' AS name
    FROM geo.features f
    JOIN geo.layers l ON f.layer_id = l.id
    WHERE l.name = 'sensors'
      AND l.is_public IS TRUE
      AND f.status = 'published'
      AND f.geom IS NOT NULL
      AND f.geom && bounds_4326
      AND ST_Intersects(f.geom, bounds_4326)
  ) AS tile;

  RETURN mvt;
END;
$$;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION geo.intervention_tiles(z integer, x integer, y integer)
RETURNS bytea
LANGUAGE plpgsql
STABLE
PARALLEL SAFE
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
  ) AS tile;

  RETURN mvt;
END;
$$;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION geo.building_tiles(z integer, x integer, y integer)
RETURNS bytea
LANGUAGE plpgsql
STABLE
PARALLEL SAFE
AS $$
DECLARE
  bounds_3857 geometry;
  bounds_4326 geometry;
  mvt bytea;
BEGIN
  bounds_3857 := ST_TileEnvelope(z, x, y);
  bounds_4326 := ST_Transform(bounds_3857, 4326);

  SELECT COALESCE(ST_AsMVT(tile, 'buildings', 4096, 'geom'), ''::bytea) INTO mvt
  FROM (
    SELECT
      b.id,
      ST_AsMVTGeom(ST_Transform(b.geom, 3857), bounds_3857, 4096, 64, true) AS geom,
      b.height,
      b.levels,
      b.building_type,
      b.name
    FROM geo.osm_buildings b
    WHERE b.is_public IS TRUE
      AND b.geom && bounds_4326
      AND ST_Intersects(b.geom, bounds_4326)
  ) AS tile;

  RETURN mvt;
END;
$$;
