#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DATA_DIR="${PROJECT_DIR}/data/pmtiles"
PMTILES_URL="${PMTILES_URL:-https://build.protomaps.com/20240801T000000Z.pmtiles}"
PMTILES_FILE="${DATA_DIR}/basemap.pmtiles"
PMTILES_SHA256="${PMTILES_SHA256:-}"
PMTILES_CACHE_CONTROL="${PMTILES_CACHE_CONTROL:-public, max-age=300}"
R2_OBJECT_KEY="${R2_OBJECT_KEY:-basemap.pmtiles}"
R2_CORS_ORIGIN="${R2_CORS_ORIGIN:-<plantgeo-web-origin>}"

R2_BUCKET="${R2_BUCKET:-plantgeo-tiles}"
R2_ENDPOINT="${R2_ENDPOINT:-}"

mkdir -p "$DATA_DIR"

if [ ! -f "$PMTILES_FILE" ]; then
    echo "Downloading Protomaps basemap PMTiles..."
    PMTILES_PART="${PMTILES_FILE}.part"
    trap 'rm -f "$PMTILES_PART"' EXIT
    curl --fail --location --retry 3 --retry-all-errors --output "$PMTILES_PART" "$PMTILES_URL"
    if [ -n "$PMTILES_SHA256" ]; then
        printf '%s  %s\n' "$PMTILES_SHA256" "$PMTILES_PART" | sha256sum --check --status
    fi
    mv "$PMTILES_PART" "$PMTILES_FILE"
    trap - EXIT
    echo "Download complete: $(du -h "$PMTILES_FILE" | cut -f1)"
else
    echo "PMTiles file already exists: $PMTILES_FILE"
fi

if command -v pmtiles >/dev/null 2>&1; then
    pmtiles verify "$PMTILES_FILE"
else
    echo "Warning: pmtiles CLI is unavailable; archive structure was not verified." >&2
fi

if [ -n "$R2_ENDPOINT" ]; then
    echo "Uploading to Cloudflare R2 bucket: $R2_BUCKET"
    aws s3 cp "$PMTILES_FILE" "s3://${R2_BUCKET}/${R2_OBJECT_KEY}" \
        --endpoint-url "$R2_ENDPOINT" \
        --content-type "application/vnd.pmtiles" \
        --cache-control "$PMTILES_CACHE_CONTROL" \
        --no-progress
    echo "Upload complete."
    echo ""
    echo "Configure R2 bucket CORS:"
    echo "  [{ \"AllowedOrigins\": [\"${R2_CORS_ORIGIN}\"], \"AllowedMethods\": [\"GET\", \"HEAD\"], \"AllowedHeaders\": [\"range\", \"if-match\"], \"ExposeHeaders\": [\"content-length\", \"content-range\", \"content-type\", \"etag\"], \"MaxAgeSeconds\": 86400 }]"
else
    echo "R2_ENDPOINT not set — skipping upload."
    echo "Set R2_ENDPOINT and R2_BUCKET to upload, or serve tiles locally via Martin."
fi

# Tile cache warming: after upload, request common zoom levels (0-6) to prime CDN cache.
# Example: for z in $(seq 0 6); do curl -s "https://tiles.example.com/basemap/$z/0/0.mvt" > /dev/null; done
