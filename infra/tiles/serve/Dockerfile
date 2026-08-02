# infra/tiles/serve/Dockerfile
#
# Static file server for the North America PMTiles v3 basemap archive,
# serving directly off a Railway volume. This is the near-term serving
# plane referenced in .claude/CLAUDE.md ("PMTiles v3 on Cloudflare R2");
# the Railway volume fills that role until the archive is promoted to R2.
#
# pmtiles-js (this repo pins pmtiles@^4.2.0, see src/lib/map/sources.ts)
# fetches the archive via many small HTTP Range requests rather than
# downloading the whole file, so the serving layer MUST support byte-range
# requests and expose the right CORS headers for Range/Content-Range — see
# nginx.pmtiles.conf for the concrete config, and
# docs/runbooks/pmtiles-na.md for how this gets deployed.

FROM nginx:1.27-alpine

# curl is required by the deploy step in the runbook: the operator uses
# `railway ssh` to shell into this running container and curl the built
# archive in from a temporary Cloudflare R2 presigned URL, straight onto
# the mounted volume. See docs/runbooks/pmtiles-na.md "Deploy to the
# Railway volume".
RUN apk add --no-cache curl

# Railway (per infra/railway/martin.railway.json's sibling convention for
# Dockerfile.martin) builds with the repo root as context regardless of
# where dockerfilePath points, so this COPY is root-relative to match.
COPY infra/tiles/serve/nginx.pmtiles.conf /etc/nginx/nginx.conf

# The Railway volume is expected to be mounted at /data/tiles (configured
# in the Railway service's Volume settings, not here — Railway mounts
# volumes at deploy time, this Dockerfile only needs to agree on the path).
# nginx.pmtiles.conf's `root` directive points at this same path.
RUN mkdir -p /data/tiles

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:8080/health || exit 1
