# Runbook: North America PMTiles Basemap (Planetiler build + Railway volume serving)

## Scope

Build a North America basemap as a single PMTiles v3 archive with
[Planetiler](https://github.com/onthegomap/planetiler), and serve it to the
browser (via `pmtiles-js`, this repo pins `pmtiles@^4.2.0`) from a Railway
persistent volume as the near-term serving plane. Cloudflare R2 remains the
eventual production origin per `.claude/CLAUDE.md`; this runbook's Railway
volume stage is the near-term step, and the deploy procedure below is
designed so promoting to R2 later is a one-line env var change on
`plantgeo-main`, not a re-architecture.

Files this runbook governs:
- `infra/tiles/compose.yaml` — pinned Planetiler batch-job container
- `infra/tiles/na-source.env` — Geofabrik input source config
- `infra/tiles/build-na.sh` — build orchestration script
- `infra/tiles/serve/Dockerfile`, `infra/tiles/serve/nginx.pmtiles.conf` —
  the static archive server image deployed to Railway
- `infra/tiles/serve/pmtiles-server.railway.json` — Railway service config

## Prerequisites

- Docker (or Podman with `docker` alias, matching this repo's convention of
  Podman for local infra) with `compose` support, on a machine with:
  - **RAM**: 28 GB+ available to the Planetiler container (default heap in
    `compose.yaml`; tunable via `PLANETILER_JAVA_OPTS`). See the sizing
    rationale comment in `compose.yaml` — this is extrapolated from
    Planetiler's published full-planet benchmark ratio, not a directly
    measured NA build, so watch `docker stats` on your first run and adjust.
  - **Disk**: 250 GB+ free, ideally on fast local SSD/NVMe (the temp
    node-location cache and feature sort/merge phases are I/O-heavy).
    Estimated need is 120-200 GB (input pbf + scratch + output); see
    `compose.yaml` for how that estimate was derived.
  - **CPU**: Planetiler scales with cores; more cores materially reduces
    wall-clock time. No hard minimum, but single-core or shared/burstable
    cloud instances will be dramatically slower than the estimate below.
- `curl`, `md5sum`, `bash` (build-na.sh is a bash script; on Windows, run it
  under Git Bash/WSL — this repo's shell tooling already assumes Git Bash
  per the environment conventions).
- Railway CLI (`railway`) authenticated against this project, for the
  deploy step.
- A Cloudflare R2 bucket + API token you can generate presigned URLs
  against, used purely as a transit relay in the deploy step below (not as
  the browser-facing origin yet — that's the future R2 promotion, out of
  scope for this runbook).

## Expected wall-clock and disk (estimates — confirm on first run)

| Phase | Estimate | Basis |
|---|---|---|
| Geofabrik NA extract download | ~10-13 GB, 10 min - 2 hr depending on bandwidth | `infra/tiles/na-source.env` `GEOFABRIK_PBF_APPROX_SIZE_GB`; actual size printed by `build-na.sh` |
| Planetiler build | 1-4 hours on an 8-16 core host with 28+ GB RAM and fast local disk | Extrapolated from Planetiler's published continent/planet-scale runs; NOT a measured NA benchmark — treat as a planning figure and record the real number from your first run for future estimates |
| Output archive size | Roughly 15-35 GB | OpenMapTiles-profile PMTiles archives for large regions have historically landed in this range; actual size depends on the OSM data density at build time and is printed by `build-na.sh` |
| Total disk (scratch + output) | 120-200 GB | See `infra/tiles/compose.yaml` rationale comment (ratio derived from Planetiler's planet-scale benchmark, scaled down) |

If any of these numbers are materially wrong on your first real run, update
this table — these are the load-bearing planning numbers for anyone running
this again.

## Step-by-step build

Run on a machine meeting the prerequisites above (NOT on Railway — this is
a local/CI build; Railway only hosts the finished archive, per the
task framing that this is a near-term Railway-volume serving plane, not a
Railway-compute build plane).

```bash
cd infra/tiles

# Optional: review/adjust na-source.env (Geofabrik URLs, output filename)
# and compose.yaml (PLANETILER_JAVA_OPTS heap size) for your host first.

# Normal run — resumable, skips already-finished steps:
./build-na.sh

# Force a full re-download + rebuild even if outputs look current:
./build-na.sh --force

# Skip the download step (you've already staged a verified pbf):
./build-na.sh --skip-download
```

What it does (see inline comments in `build-na.sh` for the authoritative
detail):
1. Downloads `north-america-latest.osm.pbf` from Geofabrik with resume
   support (`curl -C -`), verifies against Geofabrik's `.md5` sidecar,
   skips re-download if the local file already matches the current remote
   md5.
2. Runs the pinned Planetiler image (`infra/tiles/compose.yaml`) against
   the extract with `--nodemap-type=sparsearray --storage=mmap` (Planetiler's
   recommended flags for continent-sized inputs) and `--output=*.pmtiles`
   (Planetiler writes PMTiles v3 natively — no separate conversion pass).
3. Verifies the output: checks the PMTiles v3 magic bytes and spec version
   byte directly (works with no extra tooling), and additionally runs
   `pmtiles verify` if the [go-pmtiles CLI](https://github.com/protomaps/go-pmtiles)
   is installed on your PATH (recommended for a stronger guarantee before
   deploying to production, but not required).
4. Prints final archive size and phase timings.

Output lands at `infra/tiles/data/north-america.pmtiles`.

## Verification steps (before deploying)

1. `build-na.sh` already ran the magic-byte + version check; confirm the
   log line says `Spec version OK: PMTiles v3`.
2. If you have `go-pmtiles` installed, independently re-run
   `pmtiles verify infra/tiles/data/north-america.pmtiles` and
   `pmtiles show infra/tiles/data/north-america.pmtiles` to inspect
   zoom range / bounds / tile count sanity.
3. Smoke-test locally before shipping to Railway:
   ```bash
   # from infra/tiles/data/
   python3 -m http.server 8099 --bind 127.0.0.1
   # in another terminal, point pmtiles' own CLI or a scratch MapLibre page at
   # http://127.0.0.1:8099/north-america.pmtiles and confirm tiles render for
   # a couple of spot-check locations (e.g. a US city, a Canadian city, a
   # Mexican city, and something near the NA bounding-box edge in Panama)
   ```
   The plain `http.server` module does support HTTP Range requests
   correctly for this smoke test, even though it's not what you'll run in
   production.

## Deploy to the Railway volume

Railway volumes attach to exactly one service at a time in the common case,
so the build does not happen on Railway itself — you build locally/CI (above)
and push the finished archive onto the volume of the **already-deployed**
`pmtiles-server` service via `railway ssh`, using a Cloudflare R2 presigned
URL as a transit relay (this reuses infra already in the stack rather than
depending on scp/sftp support that Railway containers don't generally have).

> **⚠️ If you use `wrangler` for any R2 step: pass `--remote` on EVERY command.**
> Wrangler v4 defaults to a **local simulator**, not your real R2 bucket. Without
> `--remote`, commands such as `wrangler r2 object put …` / `get` / `delete` and
> the `wrangler r2 bucket` subcommands report success while reading and writing
> only a local on-disk emulation under `.wrangler/state` — nothing reaches
> Cloudflare. This was confirmed empirically: an upload "succeeded", and the
> object did not exist in R2. Always write
> `wrangler r2 object put <bucket>/<key> --file=<path> --remote` and verify
> afterwards with a `--remote` `get`/list (or the Cloudflare dashboard) before
> treating an upload as done. The AWS-CLI-against-R2 examples below are not
> affected — this caveat applies only to `wrangler`.

### One-time setup

1. Create a new Railway service `pmtiles-server` in this project, building
   from `infra/tiles/serve/Dockerfile`
   (`infra/tiles/serve/pmtiles-server.railway.json` mirrors the existing
   `infra/railway/martin.railway.json` convention — wire it up the same
   way you wired up the Martin service).
2. Attach a Railway persistent volume to `pmtiles-server`, mounted at
   `/data/tiles` (must match `nginx.pmtiles.conf`'s `root /data/tiles;`
   and the Dockerfile's `RUN mkdir -p /data/tiles`). Size it generously
   above your expected archive size (e.g. 50 GB) to leave room for the
   atomic-swap staging described below and for future archive growth.
3. Under Railway > `pmtiles-server` > Settings > Networking, set the
   **target port to 8080** explicitly (this config does not read Railway's
   injected `$PORT` — see the comment in `nginx.pmtiles.conf` for why) and
   enable a public domain.
4. Note the public domain Railway assigns, e.g.
   `https://pmtiles-server-production.up.railway.app`.

### Every deploy (new archive, including OSM refreshes)

1. Pick a content-versioned filename, e.g. `north-america-2026-08-02.pmtiles`
   (date-stamped — never reuse a filename for different bytes, since the
   nginx config sets `Cache-Control: public, max-age=31536000, immutable`
   on `*.pmtiles` responses).
2. Upload the built archive to a temporary object in your R2 bucket and
   generate a presigned GET URL valid for, say, 1 hour (long enough to
   cover the transfer):
   ```bash
   # example using the AWS CLI against R2's S3-compatible endpoint —
   # substitute your actual bucket/endpoint/credentials
   aws s3 cp infra/tiles/data/north-america.pmtiles \
     s3://<your-r2-bucket>/staging/north-america-2026-08-02.pmtiles \
     --endpoint-url https://<account-id>.r2.cloudflarestorage.com
   aws s3 presign \
     s3://<your-r2-bucket>/staging/north-america-2026-08-02.pmtiles \
     --endpoint-url https://<account-id>.r2.cloudflarestorage.com \
     --expires-in 3600
   ```
3. Shell into the running `pmtiles-server` container and pull the archive
   directly onto the volume, downloading to a `.tmp` name first and
   `mv`-ing into place so nginx never serves a partially-written file
   (an atomic rename on the same filesystem):
   ```bash
   railway ssh --service pmtiles-server -- \
     "curl -fL -o /data/tiles/north-america-2026-08-02.pmtiles.tmp '<presigned-url>' \
      && mv /data/tiles/north-america-2026-08-02.pmtiles.tmp /data/tiles/north-america-2026-08-02.pmtiles \
      && ls -la /data/tiles"
   ```
4. Verify the deployed copy's size/checksum matches what you built:
   ```bash
   railway ssh --service pmtiles-server -- "md5sum /data/tiles/north-america-2026-08-02.pmtiles"
   md5sum infra/tiles/data/north-america.pmtiles   # compare by hand
   ```
5. Delete the staging object from R2 (it was only a transit relay):
   ```bash
   aws s3 rm s3://<your-r2-bucket>/staging/north-america-2026-08-02.pmtiles \
     --endpoint-url https://<account-id>.r2.cloudflarestorage.com
   ```
6. Point the app at the new archive. `src/lib/map/sources.ts` already reads
   `NEXT_PUBLIC_PMTILES_URL` (falling back to a public Protomaps dev
   archive if unset — see the comment in that file about expired daily
   builds going blank). On the `plantgeo-main` Railway service, set:
   ```
   NEXT_PUBLIC_PMTILES_URL=https://pmtiles-server-production.up.railway.app/north-america-2026-08-02.pmtiles
   ```
   and redeploy `plantgeo-main` (or trigger a restart if your Railway
   plan hot-reloads env vars — verify build-time vs. runtime env injection
   for `NEXT_PUBLIC_*` before assuming a plain restart is sufficient; Next.js
   inlines `NEXT_PUBLIC_*` vars at build time, so a full rebuild is the safe
   default here).
7. Spot-check the live map (same locations as the local smoke test above)
   and check the browser network tab for `206 Partial Content` responses
   with `Access-Control-Allow-Origin` present on requests to the new domain.

No Martin or nginx-proxy config changes are required for this — Martin only
serves the dynamic PostGIS-backed layers (`infra/martin/martin.yaml`); the
static basemap has always been fetched client-side straight from whatever
`NEXT_PUBLIC_PMTILES_URL` points at, per `src/lib/map/sources.ts`.

## Rollback

Because filenames are content-versioned and never reused:
- **Fastest rollback**: revert `NEXT_PUBLIC_PMTILES_URL` on `plantgeo-main`
  back to the previous archive's URL (e.g.
  `north-america-2026-07-01.pmtiles`) and redeploy. The old file is still
  sitting on the volume untouched — nothing to restore.
- Keep at least the last 2 archives on the volume before deleting old ones
  (`railway ssh --service pmtiles-server -- "ls -la /data/tiles"` to check
  what's present, `rm` old ones only after confirming the current one has
  been live and stable for a while).
- If the volume itself is corrupted or lost, rebuild is a full re-run of
  `build-na.sh` plus a fresh deploy — there is no other backup of the
  archive by design (it's a derived artifact from OSM data, not source of
  truth; the source of truth is Geofabrik's extract + the pinned Planetiler
  version + this runbook's flags, all of which are reproducible).

## Re-running after an OSM refresh

Geofabrik regenerates `north-america-latest.osm.pbf` continuously, so:
1. Re-run `./build-na.sh` (no flags needed — it detects the changed md5
   automatically and re-downloads + rebuilds).
2. Follow "Every deploy" above with a new date-stamped filename.
3. There's no automated cadence/cron wired up as part of this runbook
   (that would be a separate CI/scheduling task, out of scope here); treat
   this as a manually-triggered operation until/unless that's built. A
   reasonable starting cadence to propose to the team is monthly, given the
   multi-hour build cost versus how fast the basemap itself goes stale.

## Assumptions to confirm before running for real

These are called out inline above too, but collected here for visibility:
- **Planetiler version pin** (`infra/tiles/compose.yaml`, currently
  `0.8.2`): confirm this is still the latest stable tag at
  https://github.com/onthegomap/planetiler/releases before a real NA build,
  and bump the pin if not.
- **RAM/disk/time estimates** throughout this runbook are extrapolated from
  Planetiler's published full-planet benchmark ratios scaled down to a
  North America extract, not measured on an actual NA build. Record real
  numbers from your first run and update this document.
- **Railway volume-per-service model**: this runbook assumes a Railway
  volume attaches to a single service and that cross-service file transfer
  must go through `railway ssh` + a relay (R2 presigned URL). If Railway's
  current plan/UI supports directly sharing one volume across two services,
  that would simplify the deploy step (build could write straight to the
  shared volume) — worth re-checking against the current Railway dashboard
  before treating the R2-relay approach as permanent.
- **R2 bucket/credentials** for the staging relay are assumed to already
  exist per this project's stated eventual-R2 target; if they don't yet,
  provisioning that bucket is a prerequisite not covered by this runbook.
