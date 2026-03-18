CREATE TABLE IF NOT EXISTS geo.osm_buildings (
    id          BIGINT PRIMARY KEY,
    geom        GEOMETRY(GEOMETRY, 4326) NOT NULL,
    name        TEXT,
    building_type TEXT,
    height      REAL,
    levels      INTEGER,
    tags        JSONB DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS geo.osm_roads (
    id          BIGINT PRIMARY KEY,
    geom        GEOMETRY(LINESTRING, 4326) NOT NULL,
    name        TEXT,
    highway     TEXT NOT NULL,
    surface     TEXT,
    oneway      BOOLEAN DEFAULT false,
    lanes       INTEGER,
    maxspeed    INTEGER,
    tags        JSONB DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS geo.osm_waterways (
    id          BIGINT PRIMARY KEY,
    geom        GEOMETRY(GEOMETRY, 4326) NOT NULL,
    name        TEXT,
    waterway    TEXT NOT NULL,
    tags        JSONB DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS geo.osm_landuse (
    id          BIGINT PRIMARY KEY,
    geom        GEOMETRY(GEOMETRY, 4326) NOT NULL,
    name        TEXT,
    landuse     TEXT,
    leisure     TEXT,
    "natural"   TEXT,
    tags        JSONB DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS geo.osm_pois (
    id          BIGINT PRIMARY KEY,
    geom        GEOMETRY(POINT, 4326) NOT NULL,
    name        TEXT,
    amenity     TEXT,
    shop        TEXT,
    tourism     TEXT,
    tags        JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_osm_buildings_geom ON geo.osm_buildings USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_osm_roads_geom ON geo.osm_roads USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_osm_roads_highway ON geo.osm_roads (highway);
CREATE INDEX IF NOT EXISTS idx_osm_waterways_geom ON geo.osm_waterways USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_osm_landuse_geom ON geo.osm_landuse USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_osm_pois_geom ON geo.osm_pois USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_osm_pois_amenity ON geo.osm_pois (amenity);
