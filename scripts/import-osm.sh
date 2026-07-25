#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DATA_DIR="${PROJECT_DIR}/data/osm"
PBF_URL="${OSM_PBF_URL:-https://download.geofabrik.de/north-america/us/california-latest.osm.pbf}"
PBF_FILE="${DATA_DIR}/region.osm.pbf"
PBF_SHA256="${OSM_PBF_SHA256:-}"
FLEX_CONFIG="${PROJECT_DIR}/infra/db/import/osm-flex-config.lua"

DB_HOST="${PGHOST:-localhost}"
DB_PORT="${PGPORT:-5432}"
DB_NAME="${PGDATABASE:-plantgeo}"
DB_USER="${PGUSER:-geo}"
DB_PASSWORD="${PGPASSWORD:?Set PGPASSWORD before importing OSM data}"

for tool in curl osm2pgsql psql; do
    command -v "$tool" >/dev/null 2>&1 || { echo "Required tool not found: $tool" >&2; exit 1; }
done

if [ -n "${RAILWAY_ENVIRONMENT_NAME:-}" ]; then
    case "${CONFIRM_RAILWAY_DATABASE_SERVICE:-}" in
        Plantgeo|plantgeo-spatiotemporal-db) ;;
        *)
            echo "Refusing Railway import without CONFIRM_RAILWAY_DATABASE_SERVICE set to an approved PlantGeo database service." >&2
            exit 1
            ;;
    esac
fi

mkdir -p "$DATA_DIR"

if [ ! -f "$PBF_FILE" ]; then
    echo "Downloading OSM PBF extract..."
    PBF_PART="${PBF_FILE}.part"
    trap 'rm -f "$PBF_PART"' EXIT
    curl --fail --location --retry 3 --retry-all-errors --output "$PBF_PART" "$PBF_URL"
    if [ -n "$PBF_SHA256" ]; then
        printf '%s  %s\n' "$PBF_SHA256" "$PBF_PART" | sha256sum --check --status
    fi
    mv "$PBF_PART" "$PBF_FILE"
    trap - EXIT
    echo "Download complete: $(du -h "$PBF_FILE" | cut -f1)"
else
    echo "PBF file already exists: $PBF_FILE"
fi

if command -v osmium >/dev/null 2>&1; then
    osmium fileinfo --no-progress "$PBF_FILE" >/dev/null
else
    echo "Warning: osmium is unavailable; PBF structure was not verified." >&2
fi

EXISTING_OSM_TABLES="$(PGPASSWORD="$DB_PASSWORD" psql \
    -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
    --no-psqlrc --set ON_ERROR_STOP=1 --tuples-only --no-align \
    -c "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'geo' AND c.relname LIKE 'osm_%';")"

if [ "${EXISTING_OSM_TABLES:-0}" -gt 0 ] && [ "${ALLOW_OSM_REPLACE:-false}" != "true" ]; then
    echo "Refusing --create import because geo.osm_* tables already exist. Set ALLOW_OSM_REPLACE=true after backup and review." >&2
    exit 1
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
    --no-psqlrc --set ON_ERROR_STOP=1 \
    -c "ANALYZE geo.osm_buildings; ANALYZE geo.osm_roads; ANALYZE geo.osm_waterways; ANALYZE geo.osm_landuse; ANALYZE geo.osm_pois;"

echo "Import complete."
PGPASSWORD="$DB_PASSWORD" psql \
    -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
    --no-psqlrc --set ON_ERROR_STOP=1 \
    -c "SELECT 'buildings' AS table_name, count(*) FROM geo.osm_buildings
        UNION ALL SELECT 'roads', count(*) FROM geo.osm_roads
        UNION ALL SELECT 'waterways', count(*) FROM geo.osm_waterways
        UNION ALL SELECT 'landuse', count(*) FROM geo.osm_landuse
        UNION ALL SELECT 'pois', count(*) FROM geo.osm_pois;"
