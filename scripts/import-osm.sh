#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DATA_DIR="${PROJECT_DIR}/data/osm"
PBF_URL="${OSM_PBF_URL:-https://download.geofabrik.de/north-america/us/california-latest.osm.pbf}"
PBF_FILE="${DATA_DIR}/region.osm.pbf"
FLEX_CONFIG="${PROJECT_DIR}/infra/db/import/osm-flex-config.lua"

DB_HOST="${PGHOST:-localhost}"
DB_PORT="${PGPORT:-5432}"
DB_NAME="${PGDATABASE:-plantgeo}"
DB_USER="${PGUSER:-geo}"
DB_PASSWORD="${PGPASSWORD:-geopass}"

mkdir -p "$DATA_DIR"

if [ ! -f "$PBF_FILE" ]; then
    echo "Downloading OSM PBF extract..."
    curl -L -o "$PBF_FILE" "$PBF_URL"
    echo "Download complete: $(du -h "$PBF_FILE" | cut -f1)"
else
    echo "PBF file already exists: $PBF_FILE"
fi

echo "Running osm2pgsql import..."
PGPASSWORD="$DB_PASSWORD" osm2pgsql \
    --create \
    --output=flex \
    --style="$FLEX_CONFIG" \
    --host="$DB_HOST" \
    --port="$DB_PORT" \
    --database="$DB_NAME" \
    --username="$DB_USER" \
    --slim \
    --drop \
    --number-processes="${OSM2PGSQL_JOBS:-4}" \
    --cache="${OSM2PGSQL_CACHE:-2000}" \
    "$PBF_FILE"

echo "Running ANALYZE on imported tables..."
PGPASSWORD="$DB_PASSWORD" psql \
    -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
    -c "ANALYZE geo.osm_buildings; ANALYZE geo.osm_roads; ANALYZE geo.osm_waterways; ANALYZE geo.osm_landuse; ANALYZE geo.osm_pois;"

echo "Import complete."
PGPASSWORD="$DB_PASSWORD" psql \
    -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
    -c "SELECT 'buildings' AS table_name, count(*) FROM geo.osm_buildings
        UNION ALL SELECT 'roads', count(*) FROM geo.osm_roads
        UNION ALL SELECT 'waterways', count(*) FROM geo.osm_waterways
        UNION ALL SELECT 'landuse', count(*) FROM geo.osm_landuse
        UNION ALL SELECT 'pois', count(*) FROM geo.osm_pois;"
