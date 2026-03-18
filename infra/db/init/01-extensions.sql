-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create schemas
CREATE SCHEMA IF NOT EXISTS geo;
CREATE SCHEMA IF NOT EXISTS tracking;

-- Core layer catalog
CREATE TABLE geo.layers (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(100) NOT NULL UNIQUE,
    type        VARCHAR(50) NOT NULL DEFAULT 'vector',
    description TEXT,
    style       JSONB DEFAULT '{}',
    is_public   BOOLEAN DEFAULT false,
    min_zoom    INTEGER DEFAULT 0,
    max_zoom    INTEGER DEFAULT 22,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Unified geospatial features table
CREATE TABLE geo.features (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    layer_id    UUID NOT NULL REFERENCES geo.layers(id) ON DELETE CASCADE,
    geom        GEOMETRY(GEOMETRY, 4326) NOT NULL,
    properties  JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Spatial indexes
CREATE INDEX idx_features_geom ON geo.features USING GIST (geom);
CREATE INDEX idx_features_layer ON geo.features (layer_id);
CREATE INDEX idx_features_properties ON geo.features USING GIN (properties);

-- Asset/vehicle tracking (TimescaleDB hypertable)
CREATE TABLE tracking.positions (
    time        TIMESTAMPTZ NOT NULL,
    asset_id    UUID NOT NULL,
    geom        GEOGRAPHY(Point, 4326) NOT NULL,
    heading     FLOAT,
    speed       FLOAT,
    altitude    FLOAT,
    metadata    JSONB DEFAULT '{}'
);

SELECT create_hypertable('tracking.positions', 'time',
    chunk_time_interval => INTERVAL '1 day');

CREATE INDEX idx_positions_asset ON tracking.positions (asset_id, time DESC);
CREATE INDEX idx_positions_geom ON tracking.positions USING GIST (geom);

-- Seed default layers
INSERT INTO geo.layers (name, type, description, is_public) VALUES
    ('fire-perimeters', 'vector', 'Active wildfire perimeters', true),
    ('evacuation-zones', 'vector', 'Evacuation zone boundaries', true),
    ('sensors', 'vector', 'Environmental sensor locations', true),
    ('vegetation', 'vector', 'Vegetation coverage areas', true),
    ('interventions', 'vector', 'Ecosystem intervention sites', true);
