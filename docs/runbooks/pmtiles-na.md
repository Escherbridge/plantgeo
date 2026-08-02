# Runbook: North America PMTiles Basemap (Planetiler build + Cloudflare R2 serving)

## Scope

Build a North America basemap as a single PMTiles v3 archive with
[Planetiler](https://github.com/onthegomap/planetiler), and serve it to the
browser (via `pmtiles-js`, this repo pins `pmtiles@^4.2.0`) directly from
Cloudflare R2 behind the custom domain `tiles.aevani.com`. R2 is the
production origin per `.claude/CLAUDE.md`, and it was provisioned on
2026-08-02 — see "One-time setup" below for the recorded state.

Files this runbook governs:
- `infra/tiles/compose.yaml` — pinned Planetiler batch-job container
- `infra/tiles/na-source.env` — Geofabrik input source config
- `infra/tiles/build-na.sh` — build orchestration script
- `infra/tiles/serve/r2-cors.json` — CORS policy applied to the R2 bucket
- `infra/tiles/serve/Dockerfile`, `infra/tiles/serve/nginx.pmtiles.conf`,
  `infra/tiles/serve/pmtiles-server.railway.json` — the self-hosted nginx
  serving plane, kept as a fallback and as the reference for the headers the
  origin must produce. **Not deployed**; no such Railway service exists.

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
- Railway access to the `plantgeo-main` service (in the **Aevani** project)
  for the `NEXT_PUBLIC_PMTILES_URL` cutover and rebuild.
- `npx wrangler` authenticated against the Cloudflare account
  (`wrangler login`; verify with `wrangler whoami`), plus the scoped
  `R2_*` credentials in `.env` for the upload step. Optionally the `aws`
  CLI for multipart uploads of multi-GB archives — not installed by default
  on the dev box.

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
a local/CI build; the finished archive is uploaded to R2 afterwards, and no
Railway compute is involved in producing it).

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

## Deploy to Cloudflare R2

The archive is served **directly from R2** through a custom domain. You build
locally/CI (above) and upload the finished archive straight to the bucket;
browsers fetch it from `tiles.aevani.com`. There is no Railway service, no
persistent volume, and no transit relay in this path.

This supersedes an earlier design that pushed the archive onto a Railway
volume via `railway ssh` using an R2 presigned URL as a transit relay. That
relay existed only to work around a volume attaching to a single service —
serving from R2 directly removes the constraint along with the whole hop.
The nginx serving plane in `infra/tiles/serve/` is retained as a fallback
(and as the reference for what headers the origin must produce), but it is
**not deployed** and no `pmtiles-server` Railway service exists.

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

**Already provisioned as of 2026-08-02** — recorded here so the state is
reproducible, not as work to repeat.

- **Bucket**: `plantgeo-tiles`, location hint `WNAM`, Standard storage class,
  on Cloudflare account `40334173d585cbf4d43918c7d7d3b0ea`.
- **Custom domain**: `tiles.aevani.com`, bound to the bucket on the
  `aevani.com` zone with a TLS 1.2 minimum. This is the origin browsers hit.
- **CORS**: set from `infra/tiles/serve/r2-cors.json` via
  `wrangler r2 bucket cors set plantgeo-tiles --file infra/tiles/serve/r2-cors.json`.
  A public R2 bucket sends **no** `Access-Control-Allow-Origin` by default —
  without this policy pmtiles-js fails cross-origin with the same opaque
  "Failed to fetch" that an expired archive produces. The policy mirrors the
  header set in `nginx.pmtiles.conf`; keep the two in sync if either changes.
  Note `wrangler` expects a nested `{"rules":[{"allowed":{…}}]}` shape, not
  the flat `AllowedOrigins` form Cloudflare's docs show for the dashboard.
- **Credentials**: a scoped R2 API token (Object Read & Write, restricted to
  `plantgeo-tiles` only) lives in the gitignored `.env` as `R2_*`. Verify any
  replacement token is genuinely bucket-scoped before use — the dashboard
  defaults to "all buckets in this account", which silently grants far more
  than the contract in `.env.example` describes.

**Outstanding**: add a Cloudflare **Cache Rule** for `tiles.aevani.com` so
`.pmtiles` responses are edge-cached. Cloudflare's default cache only covers
a fixed list of file extensions and `.pmtiles` is not among them, so every
range request currently returns `cf-cache-status: DYNAMIC` and goes to the R2
origin — costing a class B operation per request and losing the CDN benefit
that motivated the custom domain. Set the rule to match
`http.host eq "tiles.aevani.com"`, action **Cache eligibility → Eligible for
cache**, with edge TTL **respecting origin headers** (objects are uploaded
with `Cache-Control: public, max-age=31536000, immutable`). Re-verify with
`curl -sI https://tiles.aevani.com/<archive>.pmtiles | grep cf-cache-status`
— expect `HIT` on the second request.

### Every deploy (new archive, including OSM refreshes)

1. Pick a content-versioned filename, e.g. `north-america-2026-08-02.pmtiles`
   (date-stamped — never reuse a filename for different bytes, since the
   objects are uploaded with `Cache-Control: public, max-age=31536000,
   immutable`; a reused name would serve stale bytes from cache for a year).
2. Upload the archive to the bucket root. The `--cache-control` flag is not
   optional — R2 stores it as object metadata and replays it on every
   response, and without it the `immutable` caching this scheme depends on
   never reaches the browser:
   ```bash
   npx wrangler r2 object put \
     plantgeo-tiles/north-america-2026-08-02.pmtiles \
     --file infra/tiles/data/north-america.pmtiles \
     --content-type application/octet-stream \
     --cache-control "public, max-age=31536000, immutable" \
     --remote
   ```
   For a multi-GB archive prefer the AWS CLI against R2's S3 endpoint, which
   does multipart uploads and can resume (`R2_*` come from `.env`):
   ```bash
   AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
   AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
   aws s3 cp infra/tiles/data/north-america.pmtiles \
     "s3://${R2_BUCKET}/north-america-2026-08-02.pmtiles" \
     --endpoint-url "$R2_ENDPOINT" \
     --cache-control "public, max-age=31536000, immutable"
   ```
   Note the `aws` CLI is **not currently installed** on the dev box; either
   install it or use the `wrangler` form above.
3. Verify the uploaded object is complete and reachable before cutting over:
   ```bash
   npx wrangler r2 object get plantgeo-tiles/north-america-2026-08-02.pmtiles \
     --remote --pipe | md5sum
   md5sum infra/tiles/data/north-america.pmtiles   # compare by hand

   curl -s -D - -o /dev/null \
     -H "Range: bytes=0-1023" \
     https://tiles.aevani.com/north-america-2026-08-02.pmtiles
   ```
   Expect `206 Partial Content`, a `Content-Range` whose total matches the
   local file size, `Access-Control-Allow-Origin: *`, the `immutable`
   `Cache-Control`, and **no** `Content-Encoding` (a gzipped body would break
   the byte-offset math pmtiles-js relies on).
4. Point the app at the new archive. `src/lib/map/sources.ts` already reads
   `NEXT_PUBLIC_PMTILES_URL` (falling back to a public Protomaps dev
   archive if unset — see the comment in that file about expired daily
   builds going blank). On the `plantgeo-main` Railway service — which lives
   in the **Aevani** project, not a project named `plantgeo` — set:
   ```
   NEXT_PUBLIC_PMTILES_URL=https://tiles.aevani.com/north-america-2026-08-02.pmtiles
   ```
   and **rebuild** `plantgeo-main`. A restart is not sufficient: Next.js
   inlines `NEXT_PUBLIC_*` vars into the client bundle at build time, so the
   old URL stays baked in until a full rebuild runs.
5. Spot-check the live map (same locations as the local smoke test above)
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
  `north-america-2026-07-01.pmtiles`) and rebuild. The old object is still
  in the bucket untouched — nothing to restore.
- Keep at least the last 2 archives in the bucket before deleting old ones
  (`npx wrangler r2 object get` / the Cloudflare dashboard to check what's
  present; delete old ones only after confirming the current one has been
  live and stable for a while).
- If the bucket contents are lost, rebuild is a full re-run of
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
  `0.8.2`): verified 2026-08-02 — the `ghcr.io/onthegomap/planetiler:0.8.2`
  manifest exists and pulls, so the build will not fail on a missing image.
  It is however well behind: the latest release is `v0.10.2` (2026-03-29),
  which among other things adds zoom-16 tile generation. Decide whether to
  bump before committing to a multi-hour run.
- **RAM/disk/time estimates** throughout this runbook are extrapolated from
  Planetiler's published full-planet benchmark ratios scaled down to a
  North America extract, not measured on an actual NA build. Record real
  numbers from your first run and update this document.
- **Railway volume-per-service model**: moot for this runbook now that the
  archive is served from R2 directly — there is no volume in the path. The
  question was never resolved: Railway's docs state "each service can only
  have a single volume" but say nothing either way about attaching one
  volume to multiple services. Re-open only if the nginx fallback is ever
  deployed.
- **R2 bucket/credentials**: provisioned and verified 2026-08-02. Range
  requests return `206` with correct `Content-Range`, CORS headers are
  present on both GET and the OPTIONS preflight, and responses are not
  gzipped. The one gap is edge caching — see the Cache Rule note under
  "One-time setup".
