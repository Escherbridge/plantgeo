#!/usr/bin/env bash
set -euo pipefail

# deploy-pmtiles.sh — Upload PMTiles assets to Cloudflare R2
#
# Required environment variables:
#   R2_BUCKET        — R2 bucket name (e.g. plantgeo-tiles)
#   R2_ENDPOINT      — R2 S3-compatible endpoint (e.g. https://<id>.r2.cloudflarestorage.com)
#   R2_ACCESS_KEY_ID — R2 API token access key ID
#   R2_SECRET_ACCESS_KEY — R2 API token secret
#
# Optional:
#   PMTILES_DIR — local directory containing .pmtiles files (default: data/pmtiles)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PMTILES_DIR="${PMTILES_DIR:-${PROJECT_DIR}/data/pmtiles}"
PMTILES_CACHE_CONTROL="${PMTILES_CACHE_CONTROL:-public, max-age=300}"
R2_CORS_ORIGIN="${R2_CORS_ORIGIN:-<plantgeo-web-origin>}"

: "${R2_BUCKET:?R2_BUCKET is required}"
: "${R2_ENDPOINT:?R2_ENDPOINT is required}"
: "${R2_ACCESS_KEY_ID:?R2_ACCESS_KEY_ID is required}"
: "${R2_SECRET_ACCESS_KEY:?R2_SECRET_ACCESS_KEY is required}"

if [ ! -d "$PMTILES_DIR" ]; then
  echo "Error: PMTiles directory not found: $PMTILES_DIR" >&2
  echo "Run scripts/setup-pmtiles.sh first to download basemap tiles." >&2
  exit 1
fi

if ! find "$PMTILES_DIR" -maxdepth 1 -type f -name '*.pmtiles' -print -quit | grep -q .; then
  echo "Error: no .pmtiles archives found in $PMTILES_DIR" >&2
  exit 1
fi

export AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
export AWS_DEFAULT_REGION="auto"

echo "Uploading PMTiles from ${PMTILES_DIR} to s3://${R2_BUCKET}/ ..."

aws s3 sync "${PMTILES_DIR}/" "s3://${R2_BUCKET}/" \
  --endpoint-url "${R2_ENDPOINT}" \
  --exclude "*" \
  --include "*.pmtiles" \
  --content-type "application/vnd.pmtiles" \
  --cache-control "${PMTILES_CACHE_CONTROL}" \
  --no-progress

echo ""
echo "Upload complete."
echo ""
echo "Next steps:"
echo "  1. Set NEXT_PUBLIC_PMTILES_URL to the public URL of your R2 bucket."
echo "  2. Configure CORS on the R2 bucket (via Cloudflare dashboard):"
echo "     [{ \"AllowedOrigins\": [\"${R2_CORS_ORIGIN}\"], \"AllowedMethods\": [\"GET\", \"HEAD\"],"
echo '        "AllowedHeaders": ["range", "if-match"],'
echo '        "ExposeHeaders": ["content-length", "content-range", "content-type", "etag"],'
echo '        "MaxAgeSeconds": 86400 }]'
