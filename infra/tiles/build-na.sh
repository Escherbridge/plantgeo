#!/usr/bin/env bash
#
# infra/tiles/build-na.sh
#
# End-to-end North America PMTiles v3 basemap build:
#   1. download the Geofabrik North America OSM extract (resumable, md5-verified)
#   2. run Planetiler (pinned image, see compose.yaml) against it
#   3. verify the resulting .pmtiles archive
#   4. print size + timing
#
# AUTHORING ONLY — this script is safe to read without executing. It is not
# invoked by any CI/build step in this repo. The multi-hour North America
# build is a deliberate, manually-triggered operation; see
# docs/runbooks/pmtiles-na.md before running it.
#
# Idempotent / resumable:
#   - the pbf download streams into a `.part` sidecar with `curl -C -`
#     (resume), and is only moved into place after its md5 matches; a
#     re-download is skipped entirely if the local file's md5 already
#     matches Geofabrik's current .md5 sidecar.
#   - the Planetiler run is skipped if OUTPUT_PMTILES_NAME already exists
#     and is newer than the input pbf, unless --force is passed.
#   - re-running after an OSM refresh: Geofabrik's "north-america-latest"
#     always points at the current extract, so re-running this script picks
#     up new data automatically (the md5 check will detect the change and
#     force a re-download + rebuild).
#
# Usage:
#   ./build-na.sh                 # normal run, resumable, skips finished steps
#   ./build-na.sh --force         # force re-download and rebuild even if outputs look current
#   ./build-na.sh --skip-download # assume the pbf is already present and verified
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# shellcheck source=infra/tiles/na-source.env
source ./na-source.env

FORCE=0
SKIP_DOWNLOAD=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --skip-download) SKIP_DOWNLOAD=1 ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: $0 [--force] [--skip-download]" >&2
      exit 1
      ;;
  esac
done

PBF_PATH="$DATA_DIR/north-america-latest.osm.pbf"
MD5_PATH="$DATA_DIR/north-america-latest.osm.pbf.md5"
OUTPUT_PATH="$DATA_DIR/$OUTPUT_PMTILES_NAME"

mkdir -p "$DATA_DIR" "$TMP_DIR"

log() { printf '[build-na] %s\n' "$*"; }
fail() { printf '[build-na] ERROR: %s\n' "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "required command '$1' not found on PATH"
}

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
require_cmd curl
require_cmd docker
# Step 2 uses `docker compose run` (Compose V2 plugin), not the standalone
# docker-compose binary — the docker binary alone is not sufficient.
docker compose version >/dev/null 2>&1 ||
  fail "the Docker Compose V2 plugin is required ('docker compose version' failed); a bare 'docker' binary or the legacy 'docker-compose' script is not enough"
require_cmd md5sum
require_cmd du

log "Preflight: checking free disk space under $DATA_DIR"
# Heuristic only — see compose.yaml for the disk-sizing rationale (~120-200 GB
# estimated, extrapolated from Planetiler's full-planet benchmark ratio, not
# measured on a real NA build). We warn hard but don't hard-fail on a
# heuristic, since df portability across platforms is inconsistent.
if command -v df >/dev/null 2>&1; then
  AVAIL_KB=$(df -Pk "$DATA_DIR" 2>/dev/null | awk 'NR==2 {print $4}' || echo "")
  if [ -n "${AVAIL_KB:-}" ]; then
    AVAIL_GB=$((AVAIL_KB / 1024 / 1024))
    log "Free space at $DATA_DIR: ~${AVAIL_GB} GB"
    if [ "$AVAIL_GB" -lt 150 ]; then
      log "WARNING: less than ~150 GB free. Estimated NA build scratch + output need is 120-200 GB (see compose.yaml)."
      log "WARNING: proceeding anyway since this is a heuristic, not a hard check. Ctrl-C now if you want to free space first."
    fi
  fi
fi

# ---------------------------------------------------------------------------
# Step 1: download + verify the Geofabrik North America extract
# ---------------------------------------------------------------------------
STEP1_START=$SECONDS

if [ "$SKIP_DOWNLOAD" -eq 1 ]; then
  log "Skipping download (--skip-download); expecting $PBF_PATH to already be present and correct."
  [ -f "$PBF_PATH" ] || fail "--skip-download given but $PBF_PATH does not exist"
else
  log "Fetching current md5 from $GEOFABRIK_MD5_URL"
  curl -fsSL -o "$MD5_PATH" "$GEOFABRIK_MD5_URL"
  REMOTE_MD5="$(awk '{print $1}' "$MD5_PATH")"

  NEED_DOWNLOAD=1
  if [ "$FORCE" -eq 0 ] && [ -f "$PBF_PATH" ]; then
    LOCAL_MD5="$(md5sum "$PBF_PATH" | awk '{print $1}')"
    if [ "$LOCAL_MD5" = "$REMOTE_MD5" ]; then
      log "Local extract already matches current Geofabrik md5 ($REMOTE_MD5); skipping download."
      NEED_DOWNLOAD=0
    else
      log "Local extract md5 ($LOCAL_MD5) does not match current remote md5 ($REMOTE_MD5) — Geofabrik has refreshed; re-downloading."
    fi
  fi

  if [ "$NEED_DOWNLOAD" -eq 1 ]; then
    # Download into a .part sidecar and only move it into place after the md5
    # verifies. Resuming (`curl -C -`) against the *destination* would append
    # the new extract's tail onto a stale/complete file: on an md5 mismatch
    # that splices a corrupt pbf that re-running only makes worse, and with
    # --force against a complete file the server answers 416, which -f turns
    # into a hard failure. Resuming only ever applies to our own .part file,
    # and a partially-written download can never masquerade as a complete one.
    PART_PATH="$PBF_PATH.part"

    # A mismatched local extract (or --force) means the remote content changed:
    # any leftover .part belongs to the previous extract, so start clean.
    if [ "$FORCE" -eq 1 ] || [ -f "$PBF_PATH" ]; then
      rm -f "$PART_PATH"
    fi

    log "Downloading $GEOFABRIK_PBF_URL -> $PART_PATH (resumable; approx ${GEOFABRIK_PBF_APPROX_SIZE_GB} GB, confirm actual size below)"
    curl -fL -C - --retry 5 --retry-delay 10 -o "$PART_PATH" "$GEOFABRIK_PBF_URL"

    log "Verifying md5 checksum"
    LOCAL_MD5="$(md5sum "$PART_PATH" | awk '{print $1}')"
    if [ "$LOCAL_MD5" != "$REMOTE_MD5" ]; then
      rm -f "$PART_PATH"
      fail "md5 mismatch after download: local=$LOCAL_MD5 remote=$REMOTE_MD5 (the partial file has been discarded; re-run to retry from a clean slate — do not proceed on a corrupt extract)"
    fi
    log "md5 verified: $LOCAL_MD5"

    mv -f "$PART_PATH" "$PBF_PATH"
  fi
fi

PBF_SIZE_BYTES=$(du -sb "$PBF_PATH" 2>/dev/null | awk '{print $1}' || stat -c%s "$PBF_PATH" 2>/dev/null || echo "unknown")
log "Input extract: $PBF_PATH ($PBF_SIZE_BYTES bytes)"
STEP1_ELAPSED=$((SECONDS - STEP1_START))
log "Step 1 (download/verify) took ${STEP1_ELAPSED}s"

# ---------------------------------------------------------------------------
# Step 2: run Planetiler
# ---------------------------------------------------------------------------
if [ "$FORCE" -eq 0 ] && [ -f "$OUTPUT_PATH" ] && [ "$OUTPUT_PATH" -nt "$PBF_PATH" ]; then
  log "Output $OUTPUT_PATH already exists and is newer than the input extract; skipping Planetiler run (use --force to rebuild)."
else
  log "Running Planetiler (pinned image from compose.yaml) against $PBF_PATH"
  STEP2_START=$SECONDS

  # Planetiler writes PMTiles v3 natively when --output ends in .pmtiles — no
  # separate pmtiles-convert step required. Flags:
  #   --osm-path            use the extract we already downloaded (skip Planetiler's own fetch)
  #   --output               .pmtiles path -> native v3 archive output
  #   --tmpdir                scratch space for the node-location cache + feature sort/merge
  #   --force                 overwrite an existing output file inside the container
  #   --nodemap-type=sparsearray --storage=mmap
  #                            recommended by Planetiler docs for continent-sized
  #                            extracts to keep the node-location cache memory-mapped
  #                            rather than fully heap-resident
  docker compose run --rm planetiler \
    --osm-path=/data/"$(basename "$PBF_PATH")" \
    --output=/data/"$OUTPUT_PMTILES_NAME" \
    --tmpdir=/data/tmp \
    --nodemap-type=sparsearray \
    --storage=mmap \
    --force

  STEP2_ELAPSED=$((SECONDS - STEP2_START))
  log "Step 2 (Planetiler build) took ${STEP2_ELAPSED}s ($((STEP2_ELAPSED / 60)) min)"
fi

# ---------------------------------------------------------------------------
# Step 3: verify the output archive
# ---------------------------------------------------------------------------
[ -f "$OUTPUT_PATH" ] || fail "expected output not found at $OUTPUT_PATH"

log "Verifying PMTiles v3 header magic bytes"
# PMTiles v3 spec: first 7 bytes are the ASCII magic "PMTiles", byte 8 is the
# spec version (0x03 for v3). We check both, since a v2 archive would break
# the pmtiles-js client this repo uses (src/lib/map/sources.ts).
HEADER_HEX="$(head -c 8 "$OUTPUT_PATH" | od -An -tx1 | tr -d ' \n')"
MAGIC_HEX="504d54696c6573"       # "PMTiles" in hex
VERSION_HEX="${HEADER_HEX:14:2}"

case "$HEADER_HEX" in
  "$MAGIC_HEX"*)
    log "Magic bytes OK (PMTiles archive detected)"
    ;;
  *)
    fail "output does not start with PMTiles magic bytes; got header hex: $HEADER_HEX"
    ;;
esac

if [ "$VERSION_HEX" = "03" ]; then
  log "Spec version OK: PMTiles v3"
else
  fail "expected PMTiles spec version 0x03, got 0x$VERSION_HEX — this repo's pmtiles-js client (pmtiles@^4.2.0) requires v3 archives"
fi

if command -v pmtiles >/dev/null 2>&1; then
  log "go-pmtiles CLI detected, running 'pmtiles verify' for a deeper structural check"
  pmtiles verify "$OUTPUT_PATH" || fail "pmtiles verify reported a corrupt/invalid archive"
else
  log "go-pmtiles CLI not found on PATH — skipping deep structural verification (magic-byte check above still passed)."
  log "Install it (https://github.com/protomaps/go-pmtiles) for a stronger guarantee before deploying to production."
fi

# ---------------------------------------------------------------------------
# Step 4: report
# ---------------------------------------------------------------------------
OUTPUT_SIZE_HUMAN="$(du -h "$OUTPUT_PATH" | awk '{print $1}')"
TOTAL_ELAPSED=$((SECONDS))

log "----------------------------------------------------------------"
log "Build complete"
log "  Output:   $OUTPUT_PATH"
log "  Size:     $OUTPUT_SIZE_HUMAN"
log "  Total time this run: ${TOTAL_ELAPSED}s ($((TOTAL_ELAPSED / 60)) min)"
log "----------------------------------------------------------------"
log "Next step: see docs/runbooks/pmtiles-na.md 'Deploy to the Railway volume'."
