#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DATA_DIR="${PROJECT_DIR}/data/pmtiles"
PMTILES_URL="${PMTILES_URL:-https://build.protomaps.com/20240801T000000Z.pmtiles}"
PMTILES_FILE="${DATA_DIR}/basemap.pmtiles"

R2_BUCKET="${R2_BUCKET:-plantgeo-tiles}"
R2_ENDPOINT="${R2_ENDPOINT:-}"

mkdir -p "$DATA_DIR"

if [ ! -f "$PMTILES_FILE" ]; then
    echo "Downloading Protomaps basemap PMTiles..."
    curl -L -o "$PMTILES_FILE" "$PMTILES_URL"
    echo "Download complete: $(du -h "$PMTILES_FILE" | cut -f1)"
else
    echo "PMTiles file already exists: $PMTILES_FILE"
fi

if [ -n "$R2_ENDPOINT" ]; then
    echo "Uploading to Cloudflare R2 bucket: $R2_BUCKET"
    aws s3 cp "$PMTILES_FILE" "s3://${R2_BUCKET}/basemap.pmtiles" \
        --endpoint-url "$R2_ENDPOINT" \
        --content-type "application/x-protomaps-tiles"
    echo "Upload complete."
    echo ""
    echo "Configure R2 bucket CORS:"
    echo '  [{ "AllowedOrigins": ["*"], "AllowedMethods": ["GET", "HEAD"], "AllowedHeaders": ["range", "if-match"], "ExposeHeaders": ["content-length", "content-range", "content-type"], "MaxAgeSeconds": 86400 }]'
else
    echo "R2_ENDPOINT not set — skipping upload."
    echo "Set R2_ENDPOINT and R2_BUCKET to upload, or serve tiles locally via Martin."
fi

# Tile cache warming: after upload, request common zoom levels (0-6) to prime CDN cache.
# Example: for z in $(seq 0 6); do curl -s "https://tiles.example.com/basemap/$z/0/0.mvt" > /dev/null; done
