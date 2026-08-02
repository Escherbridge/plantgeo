# Runbook: Pacific Northwest PMTiles Basemap (`pmtiles extract` + Cloudflare R2)

## Scope

Produce a Pacific Northwest basemap as a single PMTiles v3 archive by
**extracting a bbox from the Protomaps daily planet build**, and serve it to
the browser (via `pmtiles-js`, this repo pins `pmtiles@^4.2.0`) directly from
Cloudflare R2 behind the custom domain `tiles.aevani.com`.

The pilot region is the Pacific Northwest — Idaho, Washington, Oregon. Do not
build continental assets; expansion beyond the PNW is a separate decision.

Files this runbook governs:
- `infra/tiles/serve/r2-cors.json` — CORS policy applied to the R2 bucket
- `infra/tiles/serve/Dockerfile`, `infra/tiles/serve/nginx.pmtiles.conf`,
  `infra/tiles/serve/pmtiles-server.railway.json` — the self-hosted nginx
  serving plane, kept as a fallback and as the reference for the headers the
  origin must produce. **Not deployed**; no such Railway service exists.

### Why not Planetiler

`infra/tiles/` still contains a Planetiler build path (`compose.yaml`,
`na-source.env`, `build-na.sh`). **It is cancelled and must not be
resurrected**, for two independent reasons:

1. **Wrong tile schema.** `compose.yaml` pins Planetiler's *OpenMapTiles*
   profile. This repo's style (`src/lib/map/styles.ts`) reads the *Protomaps
   basemap v4* schema — source layers `earth`, `water`, `landcover`,
   `landuse`, `roads`, `boundaries`, `places`, `pois`, `buildings` — and pins
   sprites/glyphs to `basemaps-assets/…/v4`. An OpenMapTiles archive renders a
   blank map. (The MapLibre source *id* is `protomaps`; that is not a schema
   claim, and an inline comment in `compose.yaml` conflated the two.)
2. **The host cannot run it.** The build is local, not Railway, so it was never
   a cloud cost — but it wants a 28 GB heap on a 32 GB machine and 120–200 GB
   of scratch against ~135 GB free. It would OOM or fill the disk.

`pmtiles extract` avoids all of it: it reads the remote archive over HTTP range
requests and downloads only the region's bytes. No full planet download, no
large heap, no scratch space. Keep `infra/tiles/` only as reference for the day
a genuinely custom schema is needed — and if that day comes, it must use the
Protomaps profile, not OpenMapTiles.

## Prerequisites

- The [go-pmtiles CLI](https://github.com/protomaps/go-pmtiles) (`pmtiles`),
  v1.31.2 or newer. Not installed by default on the dev box; grab the
  `Windows_x86_64` zip from the GitHub releases page and unzip it anywhere on
  PATH.
- Node (for the upload script below) and `npx wrangler` authenticated against
  the Cloudflare account (`wrangler login`; verify with `wrangler whoami`).
- The scoped `R2_*` credentials in the gitignored `.env`.
- Railway access to the `plantgeo-main` service (in the **Aevani** project) for
  the `NEXT_PUBLIC_PMTILES_URL` cutover and rebuild.
- Roughly 3 GB of free disk for the extract. That is the whole requirement —
  contrast with Planetiler's 120–200 GB.

## Measured cost (recorded 2026-08-02, PNW bbox, z0–15)

| Phase | Measured | Notes |
|---|---|---|
| `pmtiles extract` | **10 min 36 s** | 4 download threads, 2184 tiles/s, 197 total HTTP requests |
| Bytes transferred | **1.5 GB** | overfetch factor 0.05 |
| Output archive | **1.4 GB** (1,346 MB / 1.31 GiB) | 1,629,447 addressed tiles; 1,390,424 tile entries |
| Upload to R2 | see below | 64 MiB multipart parts, 4 concurrent |

These are real numbers from an actual run, not estimates. The archive is
comfortably inside R2's 10 GB free tier, so at $0.015/GB-month and $0 egress
the hosting cost is effectively zero.

## Step 1 — pick a source build

Protomaps publishes a daily planet build and **retains only the last 7 days**
(plus the latest per patch version). This is exactly why a pinned build URL
rots: an expired build 404s without CORS headers, which surfaces in the browser
as an opaque "Failed to fetch" and a blank map.

Check what is actually live before extracting — do not assume a date:

```bash
# https://maps.protomaps.com/builds/ renders the list in JS; probe directly:
for d in $(seq 0 8); do
  day=$(date -d "-$d day" +%Y%m%d)
  code=$(curl -s -o /dev/null -w '%{http_code}' -I "https://build.protomaps.com/$day.pmtiles")
  echo "$day $code"
done
```

The archive host is `build.protomaps.com/<YYYYMMDD>.pmtiles`. Confirm the build
is the schema you expect before spending the download:

```bash
pmtiles show https://build.protomaps.com/<YYYYMMDD>.pmtiles
```

Expect `tile type: mvt`, `min zoom: 0`, `max zoom: 15`, and metadata naming
`Protomaps Basemap` at `version 4.x`. Sanity-check the layer list with
`pmtiles show --metadata …` — it must contain `earth`/`water`/`roads`/
`places`/`buildings`, not OpenMapTiles' `transportation`/`water_name`/etc.

Data is ODbL. The style already carries the required OSM attribution
(`src/lib/map/sources.ts`); keep it.

Protomaps explicitly recommends copying builds into your own storage rather
than hotlinking theirs — which is what the rest of this runbook does.

## Step 2 — extract the region

```bash
pmtiles extract https://build.protomaps.com/<YYYYMMDD>.pmtiles pnw-<YYYYMMDD>.pmtiles \
  --bbox=-125.10,41.85,-110.90,49.10 \
  --maxzoom=15
```

The bbox is Idaho + Washington + Oregon, buffered slightly past the state lines
so border tiles are not clipped:

| Edge | Value | Rationale |
|---|---|---|
| West | `-125.10` | past the Pacific coastline |
| South | `41.85` | OR/CA and ID/NV lines sit at ~42.00 |
| East | `-110.90` | past the ID/WY line at ~111.05 |
| North | `49.10` | past the Canadian border at 49.00 |

Re-extracting is cheap (~10 min), so widening later is not expensive — but the
R2 object name and `NEXT_PUBLIC_PMTILES_URL` both change with it, so treat a
bbox change as a full redeploy, not an edit in place.

Verify before uploading:

```bash
pmtiles show pnw-<YYYYMMDD>.pmtiles          # bounds/zoom/tile counts sane?
pmtiles tile pnw-<YYYYMMDD>.pmtiles 12 683 1477 | wc -c   # Boise z12 — expect ~1.4 KB, not 0
```

## Step 3 — upload to Cloudflare R2

> **⚠️ If you use `wrangler` for any R2 step: pass `--remote` on EVERY command.**
> Wrangler v4 defaults to a **local simulator**, not your real R2 bucket. Without
> `--remote`, commands such as `wrangler r2 object put …` / `get` / `delete` and
> the `wrangler r2 bucket` subcommands report success while reading and writing
> only a local on-disk emulation under `.wrangler/state` — nothing reaches
> Cloudflare. This was confirmed empirically: an upload "succeeded", and the
> object did not exist in R2.

> **⚠️ `wrangler r2 object put` refuses files over 300 MiB.** It has no
> multipart path, so it cannot upload this archive at all — it fails with
> `Error: Wrangler only supports uploading files up to 300 MiB in size`.
> Use the S3-compatible multipart route below. (The `aws` CLI is **not**
> installed on the dev box; the Node script avoids needing it.)

Upload with the AWS SDK's managed multipart uploader against R2's S3 endpoint,
reading credentials from `.env`:

```js
// scratch script — @aws-sdk/client-s3 + @aws-sdk/lib-storage
const upload = new Upload({
  client,                        // S3Client, region "auto", endpoint R2_ENDPOINT
  params: {
    Bucket: env.R2_BUCKET,
    Key: "pnw-<YYYY-MM-DD>.pmtiles",
    Body: createReadStream(file),
    ContentType: "application/octet-stream",
    // Never omit: R2 replays this on every response, and the immutable
    // caching this scheme depends on is what keeps class-B ops down.
    CacheControl: "public, max-age=31536000, immutable",
  },
  queueSize: 4,
  partSize: 64 * 1024 * 1024,
});
await upload.done();
```

Use a **date-stamped filename and never reuse one** for different bytes — the
objects are served `immutable` for a year, so a reused name serves stale bytes
from cache long after you have moved on.

## Step 4 — CORS

A public R2 bucket sends **no** `Access-Control-Allow-Origin` by default, and
without it pmtiles-js fails cross-origin with the same opaque "Failed to fetch"
that an expired archive produces. Apply `infra/tiles/serve/r2-cors.json`:

```bash
npx wrangler r2 bucket cors set plantgeo-tiles \
  --file infra/tiles/serve/r2-cors.json --remote
```

`wrangler` expects the nested `{"rules":[{"allowed":{…}}]}` shape, not the flat
`AllowedOrigins` form Cloudflare's dashboard docs show. The policy mirrors the
header set in `nginx.pmtiles.conf`; keep the two in sync if either changes.
`Range`, `If-Range` and `If-None-Match` must stay in `AllowedHeaders`, and
`Content-Range`/`Accept-Ranges`/`ETag` in `ExposeHeaders`, or range reads break.

## Step 5 — verify through the CDN edge

Do **not** skip this, and do not substitute a check against the R2 bucket
endpoint: the failure modes this catches (a proxy that gzips the body, or one
that drops range support) are introduced *at the edge*, and either one breaks
PMTiles completely — the format is byte-offset addressed.

```bash
curl -sI -H "Range: bytes=5-14" https://tiles.aevani.com/pnw-<YYYY-MM-DD>.pmtiles
```

Require all of:
- `HTTP/2 206` (not `200` — a `200` means ranges were ignored and the whole
  1.4 GB is coming down)
- `content-range: bytes 5-14/<total>` where `<total>` equals the local file size
- `content-length: 10`
- **no** `content-encoding` header (a gzipped body breaks the offset math)
- `access-control-allow-origin` present
- `cache-control: public, max-age=31536000, immutable`

## Step 6 — cut over the app

`src/lib/map/sources.ts` reads `NEXT_PUBLIC_PMTILES_URL`, falling back to the
R2 archive constant in that file. Update **both**:

1. On the `plantgeo-main` Railway service — which lives in the **Aevani**
   project (shared with another app; never bulk-reset it), not a project named
   `plantgeo`:
   ```
   NEXT_PUBLIC_PMTILES_URL=https://tiles.aevani.com/pnw-<YYYY-MM-DD>.pmtiles
   ```
   Then **rebuild**. A restart is not sufficient: Next.js inlines
   `NEXT_PUBLIC_*` into the client bundle at build time, so the old URL stays
   baked in until a full rebuild runs.
2. `DEFAULT_PMTILES_ARCHIVE_URL` in `src/lib/map/sources.ts`, so local dev does
   not rot back to an expiring upstream build.

Then load the map and confirm in the network tab: `206 Partial Content` on
requests to `tiles.aevani.com`, `Access-Control-Allow-Origin` present, and the
PNW rendering with labels and roads.

No Martin or nginx-proxy changes are required — Martin only serves the dynamic
PostGIS-backed layers (`infra/martin/martin.yaml`); the static basemap is
fetched client-side straight from `NEXT_PUBLIC_PMTILES_URL`.

## Provisioned state

- **Bucket**: `plantgeo-tiles`, location hint `WNAM`, Standard storage class, on
  Cloudflare account `40334173d585cbf4d43918c7d7d3b0ea`.
- **Custom domain**: `tiles.aevani.com`, bound to the bucket on zone
  `e32ebceceeb4ccf6e33c917d22fa8ec2` (`aevani.com`) with a TLS 1.2 minimum.
  Verified 2026-08-02: `ownership_status: active`, `ssl_status: active`.
  Confirm with `npx wrangler r2 bucket domain list plantgeo-tiles`.
- **Credentials**: a scoped R2 API token (Object Read & Write, restricted to
  `plantgeo-tiles`) lives in the gitignored `.env` as `R2_*`. Verify any
  replacement token is genuinely bucket-scoped before use — the dashboard
  defaults to "all buckets in this account", which silently grants far more than
  the contract in `.env.example` describes.

**Outstanding**: add a Cloudflare **Cache Rule** for `tiles.aevani.com` so
`.pmtiles` responses are edge-cached. Cloudflare's default cache only covers a
fixed list of file extensions and `.pmtiles` is not among them, so every range
request returns `cf-cache-status: DYNAMIC` and goes to the R2 origin — costing a
class B operation per request and losing the CDN benefit that motivated the
custom domain. Set the rule to match `http.host eq "tiles.aevani.com"`, action
**Cache eligibility → Eligible for cache**, edge TTL **respecting origin
headers**. Re-verify with
`curl -sI https://tiles.aevani.com/<archive>.pmtiles | grep cf-cache-status` —
expect `HIT` on the second request.

## Rollback

Because filenames are content-versioned and never reused:
- **Fastest rollback**: revert `NEXT_PUBLIC_PMTILES_URL` on `plantgeo-main` to
  the previous archive's URL and rebuild. The old object is still in the bucket
  untouched — nothing to restore.
- Keep at least the last 2 archives before deleting old ones.
- If the bucket contents are lost, recovery is one ~10-minute `pmtiles extract`
  against a current build plus a redeploy. The archive is a derived artifact,
  not a source of truth, and there is deliberately no other backup.

## Refreshing the basemap

Protomaps rebuilds the planet daily and prunes after 7 days, so a pinned
archive in R2 never breaks — but it does go stale. To refresh:

1. Re-run Steps 1–3 with a current build date and a new date-stamped filename.
2. Re-verify (Step 5) and cut over (Step 6).

There is no automated cadence wired up; treat it as a manual operation. Given
the ~10-minute cost, monthly is a reasonable starting cadence — far cheaper to
run often than the multi-hour Planetiler path it replaced.
