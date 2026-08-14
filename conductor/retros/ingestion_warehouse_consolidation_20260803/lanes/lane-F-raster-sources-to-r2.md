---
type: lane-brief
track: ingestion_warehouse_consolidation_20260803
lane: F
status: ready
depends_on: none
---

# Lane F — Raster sources to R2 (soil, terrain, NLCD, LANDFIRE, NDVI)

Phase 5's raster half. Read [`lanes/README.md`](README.md) first for the rules every lane
inherits. Governing detail: [`plans/ingestion-warehouse-consolidation-2026-08-03.md`](../../../../plans/ingestion-warehouse-consolidation-2026-08-03.md)
§4 (the five new sources), §2 "Storage classes", risk 10/11, open questions 8 and 9.
Settled decisions: [`spec.md`](../spec.md).

Effort in the plan: **7–12 sessions, lowest confidence in the whole track** (§9). The
GDAL / PMTiles toolchain is the unknown, not the design.

---

## 1. Goal

Five raster products — SoilGrids soil properties, terrarium DEM, NLCD land cover,
LANDFIRE EVT, and GIBS MODIS NDVI — exist as first-party objects on Cloudflare R2 under
`tiles.aevani.com`, clipped to the PNW bbox `-125,42,-111,49`, built by reproducible
scripts in this repo, with an R2 key layout and retention policy that are **decided and
written down** rather than improvised. Every uploaded object has a recorded
`uri` + `checksum_sha256` + `size_bytes` + `data_available_at` + license, in a committed
manifest that another lane turns into `agri.artifact` rows. When this lane is done the
bytes are in place and provably fetchable through the CDN with range requests; the
front-end repoint and the `signal_observation` scalars are explicitly **not** in this lane
(see §7).

---

## 2. Prerequisites

**None from other lanes.** Lane F is fully independent of every schema lane — it touches
no migration, no `geo` table, no `agri` table. It can start immediately, in parallel with
lanes A, B, D, E, G, H.

Verify your tooling before writing anything. Each command and the output that means "ready":

| # | Command | Expected |
|---|---|---|
| 1 | `npx wrangler whoami` | an authenticated Cloudflare account; the bucket lives on account `40334173d585cbf4d43918c7d7d3b0ea` (`docs/runbooks/pmtiles-pnw.md:237`) |
| 2 | `npx wrangler r2 bucket domain list plantgeo-tiles --remote` | `tiles.aevani.com`, `ownership_status: active`, `ssl_status: active` |
| 3 | `curl -sI -H "Range: bytes=5-14" https://tiles.aevani.com/pnw-2026-08-02.pmtiles` | `HTTP/2 206`, `content-range: bytes 5-14/…`, `content-length: 10`, **no** `content-encoding`. This is the existing PNW basemap; if it fails, the CDN path is broken before you add anything to it |
| 4 | `gdalinfo --version` | any GDAL ≥ 3.6. **Not verified present on this box** — if absent, install it (OSGeo4W or conda) before step 4.2 |
| 5 | `pmtiles version` | go-pmtiles ≥ 1.31.2. `docs/runbooks/pmtiles-pnw.md:45-48` says it is not installed by default here |
| 6 | `grep -c R2_ .env` | ≥ 4. `R2_BUCKET`, `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` are declared at `.env.example:94-104`; the real values live in the gitignored `.env` |
| 7 | `aws --version` | **expected to FAIL.** The AWS CLI is not installed on this box (`docs/runbooks/pmtiles-pnw.md:157-160`). See trap T1 |

---

## 3. Files you own

From [`lanes/README.md`](README.md) §"File boundaries", lane F row:

> **Owns:** `scripts/raster/**`, `scripts/deploy-pmtiles.sh`, `infra/tiles/**`, `data/**`
> **Must not touch:** `src/**`, `services/agri-data-service/**`, `infra/cron-ingest/**`,
> `scripts/backfill-geometry.*`, everything else under `scripts/`

**`scripts/` and `infra/` are shared directories, not yours wholesale.** Lane B is writing
`scripts/backfill-geometry.sql` and lane D is rewriting `infra/cron-ingest/Dockerfile` and
`infra/cron-ingest/railway.json` **in the same wave**. Your claim is the `scripts/raster/`
subtree plus the single file `scripts/deploy-pmtiles.sh`, and the `infra/tiles/` subtree.
`docker-compose.yml` and `infra/martin/martin.yaml` are nobody's in wave 1 — do not edit them.

**Other sessions are running concurrently against this repo. Do not edit, create or delete
anything outside that list.** If you conclude a change is needed elsewhere — and you will,
for the front-end repoint and for `agri.artifact` — stop and record it in §7, do not reach
across.

Concrete files this lane creates or edits (all inside the boundary):

| Path | Status | Purpose |
|---|---|---|
| `scripts/raster/AGENTS.md` | new | rationale for the whole directory (house style: the "why" lives here, code carries one-liners) |
| `scripts/raster/fetch-terrain-tiles.mjs` | new | pull terrarium z0–12 for the bbox, pack to MBTiles |
| `scripts/raster/build-soil-cogs.sh` | new | 6 SoilGrids properties @ 0–5 cm → clipped COGs |
| `scripts/raster/build-nlcd-pmtiles.sh` | new | NLCD 2021 → reprojected categorical PMTiles |
| `scripts/raster/build-landfire-pmtiles.sh` | new | LANDFIRE US_200 EVT → categorical PMTiles |
| `scripts/raster/harvest-ndvi-composite.mjs` | new | one GIBS 8-day composite → PMTiles |
| `scripts/raster/upload-to-r2.mjs` | new | multipart upload + checksum + manifest emit (replaces the `aws` CLI path) |
| `scripts/raster/prune-ndvi-archives.mjs` | new | 24-month NDVI retention |
| `scripts/deploy-pmtiles.sh` | **edit** | currently unusable — see trap T1 |
| `infra/tiles/manifests/<source>-<release>.json` | new | the committed artifact manifests |
| `infra/tiles/AGENTS.md` | new | R2 layout + retention decision. **Referenced by `src/lib/map/sources.ts:7` and by `scripts/check-client-provider-urls.mjs:25` but does not exist** — creating it fixes two dangling pointers |
| `infra/tiles/serve/r2-cors.json` | edit only if needed | current policy at `infra/tiles/serve/r2-cors.json:1-13` already allows `Range`/`If-Range`/`If-None-Match` |

`data/` is **entirely gitignored** (`.gitignore:85`). Multi-GB build outputs go there and
are never committed. That is why manifests live under `infra/tiles/manifests/`, not `data/`.

---

## 4. The work

### 4.0 — Decide the R2 layout and retention FIRST (open question 9)

**Do not upload a single object before this is written into `infra/tiles/AGENTS.md`.**
The plan is explicit: "Decide before Phase 5 uploads anything" (plan open question 9).

Current state, measured:

- Bucket `plantgeo-tiles`, location hint `WNAM`, custom domain `tiles.aevani.com`
  (`docs/runbooks/pmtiles-pnw.md:233-241`).
- Exactly one object family today, at the **bucket root**: `pnw-<YYYY-MM-DD>.pmtiles`,
  served as `https://tiles.aevani.com/pnw-2026-08-02.pmtiles` (`src/lib/map/sources.ts:9-10`).
- `scripts/deploy-pmtiles.sh:43-49` does `aws s3 sync data/pmtiles/ s3://$R2_BUCKET/`
  — flat, root, `*.pmtiles` only.

**Recommended decision** (adopt unless you find a reason not to; record whichever you pick):

```
<root>/pnw-<YYYY-MM-DD>.pmtiles        # basemap — LEAVE WHERE IT IS, do not migrate
terrain/pnw-terrarium-<YYYY-MM-DD>.pmtiles
soil/soilgrids-v2.0/<property>-0-5cm-mean.tif      # 6 COGs
landcover/nlcd-<year>/pnw.pmtiles
fuels/landfire-<version>/pnw-evt.pmtiles
ndvi/<composite-end-date>.pmtiles                  # the prunable prefix
```

Rules that go with it:

1. **The basemap objects stay at the root and are never moved.** `NEXT_PUBLIC_PMTILES_URL`
   is inlined into the client bundle at build time (`docs/runbooks/pmtiles-pnw.md:213-217`),
   so moving the key silently breaks every already-built deploy until a rebuild.
2. **One prefix per source family; the release identifier is in the key, never in a
   sibling metadata file.**
3. **Keys are immutable and never reused for different bytes.** Every object ships
   `Cache-Control: public, max-age=31536000, immutable` (the pattern at
   `docs/runbooks/pmtiles-pnw.md:172-175`). A reused key serves stale bytes from the edge
   for a year.
4. **NDVI retention is a prune COMMAND, not an R2 lifecycle rule.** Reasons, both real:
   - The retention predicate is `source_release.observed_to` (plan §4 retention table),
     which lives in Postgres. R2 lifecycle rules can only age on upload time, which
     diverges the moment anyone backfills a historical composite.
   - A lifecycle rule deletes objects that `agri.artifact` rows still point at, producing
     dangling URIs with no trace. A prune command deletes the object **and** hands back the
     list of URIs it removed so the artifact rows can be reconciled in the same operation.

   Optionally add an R2 lifecycle rule on `ndvi/` at a much longer floor (36 months) as a
   cost backstop; it must never be the primary mechanism.
5. **Content types:** `application/vnd.pmtiles` for PMTiles (matches
   `scripts/deploy-pmtiles.sh:47`), `image/tiff; application=geotiff; profile=cloud-optimized`
   for COGs.

While you are in the bucket, close the **outstanding Cloudflare Cache Rule**
(`docs/runbooks/pmtiles-pnw.md:246-256`): match `http.host eq "tiles.aevani.com"`, action
*Cache eligibility → Eligible for cache*, edge TTL *respect origin headers*. Without it
every range request is `cf-cache-status: DYNAMIC` and costs a class-B op against R2 origin.
This matters far more once there are hundreds of NDVI objects than it did with one basemap.

### 4.1 — Terrain (easiest; do it first to prove the pipeline)

Upstream is already ours to copy: `https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png`
(`src/lib/map/sources.ts:3-5`, consumed at `:109-111` with `encoding: "terrarium"`).

1. Enumerate z0–12 tile indices covering `-125,42,-111,49` (~23 000 tiles per plan §4).
2. Download with bounded concurrency; retry on 5xx; **fail the run on any 404 that is
   inside the bbox** rather than writing a hole.
3. Pack into MBTiles (raster, `format=png`), then `pmtiles convert <in>.mbtiles <out>.pmtiles`.
4. `pmtiles show` the result: expect `tile type: png`, `min zoom: 0`, `max zoom: 12`, bounds
   matching the bbox.
5. Upload to `terrain/pnw-terrarium-<YYYY-MM-DD>.pmtiles`, emit manifest.

Doing terrain first is deliberate — it exercises download → MBTiles → PMTiles → R2 →
manifest end-to-end with no reprojection and no categorical resampling in the way.

### 4.2 — Soil (SoilGrids, 6 COGs)

Six properties at 0–5 cm, matching what the live point-read service already uses:
`phh2o`, `soc`, `nitrogen`, `bdod`, `cec`, `ocd` (`src/lib/server/services/soilgrids.ts:59-66`;
depth label `0-5cm` at `:47`). Bulk source is ISRIC's public COG/VRT tree, not the
per-point REST endpoint at `soilgrids.ts:45`.

**The bulk endpoint is not recorded anywhere in this repo — locate it before scripting.** The
repo only knows the per-point REST service. Resolve the actual VRT/COG root live, confirm one
property downloads and opens in `gdalinfo`, then write the resolved URL template into
`scripts/raster/AGENTS.md` with the date you verified it. Do not hardcode a guessed path into
`build-soil-cogs.sh` — a wrong root fails late, after the clip, or silently returns a global
mosaic you then clip to nothing.

Per property: `gdalwarp` to EPSG:4326 clipped to the bbox, then `gdal_translate -of COG`
with `-co COMPRESS=DEFLATE -co PREDICTOR=2 -co OVERVIEWS=IGNORE_EXISTING`.
Validate each output with `gdalinfo` — confirm the CRS is 4326, the extent matches, and the
band nodata is preserved.

**See trap T3 — SoilGrids is not in a lon/lat CRS.**

### 4.3 — NLCD (categorical)

Source: MRLC national land-cover GeoTIFF, 30 m, current release 2021 — the layer name the
repo already uses is `NLCD_2021_Land_Cover_L48` (`src/lib/server/services/nlcd.ts:12-14`),
served today as WMS from `https://www.mrlc.gov/geoserver/mrlc_display`
(`src/lib/server/services/nlcd.ts:45`).

1. `gdalwarp -t_srs EPSG:3857 -te_srs EPSG:4326 -te -125 42 -111 49 -r near` — **`-r near`
   is mandatory**, see trap T4.
2. Keep the class codes in the raster; the class table and palette already exist in-repo at
   `src/lib/environmental/nlcd.ts` (`NLCD_CLASSES`, `NLCD_CATEGORY_CLASSES`,
   `DEGRADED_NLCD_CLASSES`, re-exported at `src/lib/server/services/nlcd.ts:1-7`). Do not
   invent a second palette — read that one and bake the identical RGB values.
3. Tile to a raster pyramid (z0–12), MBTiles, `pmtiles convert`.
4. Upload to `landcover/nlcd-2021/pnw.pmtiles`.

### 4.4 — LANDFIRE EVT (categorical)

Source: LANDFIRE national EVT GeoTIFF, 30 m, version `US_200` — the same version the live
identify endpoint queries (`src/lib/server/services/landfire.ts:51-52`). Same pipeline as
NLCD, same `-r near` requirement. Upload to `fuels/landfire-US_200/pnw-evt.pmtiles`.

The EVT-code → fuel-parameter mapping already exists server-side
(`getFuelParamsByCode`, `src/lib/server/services/landfire.ts:42-50`). It is **not** this
lane's job to reproduce it — it belongs with the `signal_observation` work (see §7).

### 4.5 — NDVI (the one that grows) + open question 8

Product: GIBS `MODIS_Terra_NDVI_8Day`, tile matrix set `GoogleMapsCompatible_Level9`,
**max zoom 9**, 8-day composite window — all four pinned at `src/lib/vegetation.ts:24-32`.
The existing first-party proxy that the browser uses today is
`src/app/api/tiles/vegetation/ndvi/[time]/[z]/[y]/[x]/route.ts`, which fetches
`https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_NDVI_8Day/default/{time}/{tms}/{z}/{y}/{x}.png`
(`route.ts:68-71`). The Python-side ingest is still a hard-coded skip:
`runVegetationIngestionJob` returns `status: "skipped"` with reason
`"No versioned warehouse-backed NDVI adapter is configured"`
(`src/lib/server/services/ingestion-jobs.ts:379-388`).

Work:

1. `harvest-ndvi-composite.mjs --date <YYYY-MM-DD>`: pull z0–9 tiles for the bbox from the
   GIBS WMTS endpoint above, pack MBTiles, `pmtiles convert`, upload to
   `ndvi/<composite-end-date>.pmtiles`, emit manifest.
   **Do not exceed z9** — GIBS 404s past it, and `route.ts:56-61` already refuses deeper
   requests for exactly this reason.
2. `prune-ndvi-archives.mjs --older-than-months 24 [--dry-run]`: list `ndvi/`, select keys
   whose composite date is outside the window, print them, delete on confirmation, and
   **print the deleted URIs to stdout as JSON** so artifact rows can be reconciled.
   Default to `--dry-run`.
3. **Measure GIBS publication latency (open question 8).** Do not assume the plan's
   "typ. 1–3 d" (plan §5b). Method: for each of the last ~8 composite end dates, probe the
   dated tile endpoint at increasing offsets and record the first date on which a
   non-404 tile appears. Write the measured table into `scripts/raster/AGENTS.md` with the
   date the measurement was taken. If a few days of observation is not enough to be
   confident, say so and record the uncertainty rather than picking a number — the plan
   points at `agri.covariate_declared_gap()` as the mechanism for exactly this
   (plan §5b closing paragraph).

### 4.6 — Manifests

One JSON per uploaded object family, committed under `infra/tiles/manifests/`. This is the
handoff artifact; get its shape right because another lane consumes it verbatim. Fields map
1:1 onto the real DDL:

| Manifest field | Destination column | Verified at |
|---|---|---|
| `uri` | `agri.artifact.uri` | `services/agri-data-service/db/agri/tables/artifact.sql:11` |
| `checksum_sha256` | `agri.artifact.checksum_sha256` | `artifact.sql:13` |
| `size_bytes` | `agri.artifact.size_bytes` | `artifact.sql:14` |
| `storage_class: "r2"` | `agri.artifact.storage_class` | `artifact.sql:15` |
| `kind` | `agri.artifact.kind` | `artifact.sql:10` |
| `media_type` | `agri.artifact.media_type` | `artifact.sql:12` |
| `source_version` | `agri.source_release.source_version` | `services/agri-data-service/db/agri/tables/source_release.sql:10` |
| `retrieved_at` | `agri.source_release.retrieved_at` | `source_release.sql:11` |
| `data_available_at` | `agri.source_release.data_available_at` | `source_release.sql:12` |
| `observed_from` / `observed_to` | same names | `source_release.sql:13-14` |
| `license_snapshot` | `agri.source_release.license_snapshot` | `source_release.sql:19` |

`(uri, checksum_sha256)` is UNIQUE (`artifact.sql:30-32`), and
`(data_source_id, source_version, payload_checksum, transform_version)` is UNIQUE on the
release (`source_release.sql:37-39`) — so a re-upload of identical bytes under the same key
must be idempotent, not a duplicate manifest entry.

`data_available_at` values to record, from plan §5b:

| Source | `data_available_at` |
|---|---|
| SoilGrids | v2.0 release publication date (static release, `soilgrids.ts:56`) |
| Terrain | archive publication date |
| NLCD | the 2021 product's publication date — **not** 2021-01-01 |
| LANDFIRE | US_200 release publication date |
| NDVI | composite end date **+ the latency you measured in 4.5** |

Licenses to record: SoilGrids CC-BY 4.0; terrain ODbL/public-domain mix, attribution
required; NLCD and LANDFIRE US federal public domain (plan §4 licensing row).

---

## 5. Traps

Lane-specific only; the generic rules are in [`lanes/README.md`](README.md#rules-every-lane-inherits).

**T1 — `scripts/deploy-pmtiles.sh` does not work on this machine.** It shells out to
`aws s3 sync` (`scripts/deploy-pmtiles.sh:43`), and the AWS CLI is **not installed** here —
recorded explicitly at `docs/runbooks/pmtiles-pnw.md:157-160`. The path that has actually
been used successfully is a Node script using `@aws-sdk/client-s3` + `@aws-sdk/lib-storage`
`Upload` against the R2 S3 endpoint (`docs/runbooks/pmtiles-pnw.md:162-186`). Neither SDK
package is in `package.json` today — it was a scratch script. Either add them as devDeps
and write `scripts/raster/upload-to-r2.mjs` properly, or install the AWS CLI; do not assume
`deploy-pmtiles.sh` runs.

**T2 — `wrangler` silently writes to a local simulator.** Wrangler v4 defaults to a local
on-disk emulation under `.wrangler/state`. **Pass `--remote` on every single R2 command.**
An upload will report success and the object will not exist. Confirmed empirically —
`docs/runbooks/pmtiles-pnw.md:141-148`. Also: `wrangler r2 object put` hard-refuses files
over 300 MiB with no multipart path (`:150-155`), which rules it out for every archive this
lane produces except possibly the soil COGs.

**T3 — SoilGrids is in Interrupted Goode Homolosine, not lon/lat.** A `-projwin` with
lon/lat numbers will silently clip the wrong region, or nothing. Use
`gdalwarp -t_srs EPSG:4326 -te -125 42 -111 49 -te_srs EPSG:4326`, which makes the target
extent's CRS explicit, and verify the output extent with `gdalinfo` before uploading.

**T4 — NLCD and LANDFIRE are categorical; bilinear/cubic resampling fabricates classes.**
Resampling class code 41 against 82 with any averaging kernel produces intermediate integers
that are *different, real, wrong* land-cover classes. Every `gdalwarp` and every overview
build in 4.3 and 4.4 must pass `-r near` (and `-r nearest` for `gdaladdo`). This is the
single easiest way to ship a plausible-looking, silently wrong product.

**T5 — NDWI has no upstream. Do not substitute anything.** GIBS publishes zero water-index
rasters across all 1268 layers. The repo already states this in the code that would consume
it: "NDWI has no upstream at all (GIBS publishes no water-index raster)"
(`src/components/map/layers/VegetationLayer.tsx:87-89`), and the plan reconfirms it
(plan Appendix A5). Do not build an NDWI product from a land/water mask, from
`ENVIRONMENTAL_TILES_CONFIGURED`, or from a green/NIR approximation off a rendered basemap.
If NDWI is needed it is a Sentinel-2 project (plan Appendix A5, 9–15 sessions), out of scope.

**T6 — bulk raster processing must never enter the hourly cron container.** Plan risk 10:
"Do not put raster processing in the hourly container." `infra/cron-ingest/` is the hourly
Railway cron service. Everything in this lane runs **locally**, on demand, and only the
resulting bytes are uploaded. This includes the NDVI harvester until it has been measured
(see §7 open question F3).

**T7 — the raster PMTiles are NOT the basemap, and must not collide with it.**
`src/lib/map/sources.ts:9-10` pins one specific archive name. Uploading anything to the root
that matches `*.pmtiles` risks confusion with the basemap sync in
`scripts/deploy-pmtiles.sh:43-49`, which globs the whole `data/pmtiles` directory into the
bucket root. Either scope that script to a subdirectory or move raster outputs to
`data/raster/` — decide and record it.

**T8 — do not commit build outputs.** `.gitignore:85` ignores all of `data/`, so a
multi-GB PMTiles archive will not be committed by accident *unless* you write it somewhere
else. Keep every intermediate under `data/`.

**T9 — `pmtiles verify` is only in the optional branch today.** `scripts/setup-pmtiles.sh:34-38`
warns and continues when the CLI is absent. Your scripts must **fail**, not warn, on a
missing `pmtiles`/`gdal` binary — a raster pipeline that half-runs produces a truncated
archive that uploads fine and renders blank.

---

## 6. Definition of done

Run these; the stated output is the proof.

| # | Command | Passing output |
|---|---|---|
| 1 | `cat infra/tiles/AGENTS.md` | contains the key-prefix table from §4.0 and the "prune command, not lifecycle rule" decision with its two stated reasons |
| 2 | `npx wrangler r2 object get plantgeo-tiles/terrain/pnw-terrarium-<date>.pmtiles --remote --pipe \| wc -c` | equals the `size_bytes` in the manifest |
| 3 | `sha256sum data/raster/<each built artifact>` | equals `checksum_sha256` in `infra/tiles/manifests/*.json`, for every entry |
| 4 | `curl -sI -H "Range: bytes=5-14" https://tiles.aevani.com/<each new key>` | `HTTP/2 206`; `content-length: 10`; **no** `content-encoding`; `access-control-allow-origin` present; `cache-control: …immutable` |
| 5 | `curl -sI https://tiles.aevani.com/<any new key> \| grep cf-cache-status` (twice) | second call returns `HIT` — proves the Cache Rule from §4.0 landed |
| 6 | `pmtiles show <each pmtiles>` | terrain: `png`, z0–12; NLCD/LANDFIRE: z0–12, bounds inside the PNW bbox; NDVI: z0–**9** |
| 7 | `gdalinfo <each soil COG> \| head -20` | `Coordinate System is: … EPSG 4326`, extent within `-125 42 -111 49`, `LAYOUT=COG` in the image structure metadata |
| 8 | `node scripts/raster/prune-ndvi-archives.mjs --older-than-months 24 --dry-run` | prints a JSON list and exits 0 **without deleting anything** |
| 9 | `node scripts/raster/upload-to-r2.mjs --help` (or `bash scripts/raster/*.sh --help`) | every script documents its inputs and exits non-zero when a required binary or env var is missing (trap T9) |
| 10 | `git status --porcelain -- scripts infra` | shows only `scripts/raster/**`, `scripts/deploy-pmtiles.sh` and `infra/tiles/**`. Anything under `scripts/backfill-geometry.*` or `infra/cron-ingest/**` is lane B's or lane D's work in the shared tree — **do not stage or revert it**, and do not include it in your commit |
| 11 | `git status --porcelain -- data` | empty (`.gitignore:85`) |
| 12 | `npm run check:data-boundary` | passes — you should not have changed anything it guards, and a failure means you strayed into `src/**` |

For NLCD/LANDFIRE specifically, a spot check that T4 did not fire: sample ~1000 pixels from
the reprojected raster and confirm every value is a member of `NLCD_CLASSES`
(`src/lib/environmental/nlcd.ts`) / the LANDFIRE EVT code ranges
(`src/lib/server/services/landfire.ts:42-50`). Any value outside the class set is
resampling contamination.

---

## 7. Open questions

**F1 — Who writes the `agri.artifact` rows?** The plan is unambiguous that every R2 object
registers as an `agri.artifact` row hanging off its `source_release`, with no separate
raster catalog (plan §2 "Storage classes"). But that write happens in
`services/agri-data-service/src/**`, which this lane **must not touch**
(`lanes/README.md` lane F row), and the provenance helper is lane I's deliverable
(`lanes/README.md` lane I row).
**Recommendation:** lane F stops at the committed manifest (§4.6) and does not write to any
database. Lane I gains one CLI verb — `register-raster-artifacts <manifest.json>` — that
reads the manifest and writes `source_release` + `artifact`. Escalate to the orchestrator so
lane I's brief carries that verb; the manifest schema in §4.6 is the contract between them.

**F2 — Who does the front-end repoint?** Phase 5's stated value includes "repoint the five
front-end layers off third-party endpoints" (plan §7 Phase 5), which means editing
`src/lib/map/sources.ts:3-5` (terrain), `src/components/map/layers/LandCoverLayer.tsx` +
`src/lib/server/services/nlcd.ts:45`, `src/components/map/layers/LandFireLayer.tsx` +
`src/lib/server/services/landfire.ts:51-52`, and the NDVI URL builder at
`src/lib/vegetation.ts:73`. **All of these are in `src/**`, outside lane F.**
Also note: repointing terrain off `s3.amazonaws.com/elevation-tiles-prod/` makes the
allowlist entry at `scripts/check-client-provider-urls.mjs:28` dead, and a new
`tiles.aevani.com/` prefix is already allowed at `:25`.
**Recommendation:** the repoint is a separate, small lane sequenced *after* F (call it F2),
owning only those `src/**` files. It is a handful of URL constants and cannot start until
the objects exist. Do not do it here.

**F3 — Does the NDVI harvester get scheduled, and where?** The plan says NDVI needs "a
scheduled WMTS harvester" (§4) but also that raster processing must not go in the hourly
container (risk 10). Those are only compatible if a single NDVI composite build is minutes,
not hours.
**Recommendation:** ship the harvester as a local one-shot CLI first and **measure** one
full composite build end-to-end. If it is under ~10 minutes, propose a *separate* Railway
cron service on a daily schedule (the 8-day cadence means most days are no-ops) — never
`infra/cron-ingest`. If it is longer, it stays a manual monthly operation with the cadence
recorded in `scripts/raster/AGENTS.md`, exactly as the basemap refresh is today
(`docs/runbooks/pmtiles-pnw.md:290-297`).

**F4 — GIBS publication latency may not be measurable in one session (open question 8).**
The plan says "measure it empirically over a few weeks rather than assuming" (open q. 8).
A single session can only probe historical composites retrospectively.
**Recommendation:** do the retrospective probe in §4.5, record the observed range and the
sample size, and mark the value provisional in the manifest and in
`scripts/raster/AGENTS.md`. Do **not** let a guessed latency reach
`signal_observation.data_available_at` — plan risk 4 rates that High severity precisely
because it is invisible downstream. If confidence is low, the honest move is
`agri.covariate_declared_gap()`, not a round number.

**F5 — The per-cell scalars cannot be produced by this lane, and possibly not by anyone
yet.** The plan's source table lists `signal_observation` as each raster source's model
plane (plan §4, rows 6–10). That requires grid-cell geometries — and plan open question 1
records that `agri.spatial_cell` has **0 rows and no grids defined**, so `geo.geometry` will
have zero `grid_cell` rows even after the Phase 3 backfill. There is nothing to aggregate
onto.
**Recommendation:** treat "rasters on R2 + manifests" as lane F's complete deliverable and
state that plainly in `scripts/raster/AGENTS.md`. The zonal-statistics step (soil properties,
elevation/slope/aspect, NLCD class fractions, LANDFIRE fuel params, `ndvi_mean` per cell)
is downstream of the grid decision and belongs with Phase 6. Escalate open question 1 as a
blocker on the *scalar* half of Phase 5, not on this lane.

**F6 — `infra/tiles/` still contains a cancelled Planetiler build path.**
`infra/tiles/compose.yaml`, `na-source.env` and `build-na.sh` are explicitly cancelled and
"must not be resurrected" for two independent reasons (`docs/runbooks/pmtiles-pnw.md:20-41`).
They are inside lane F's `infra/**` boundary and a future session will trip over them.
**Recommendation:** do not resurrect and do not delete them silently. Add a one-line pointer
in the new `infra/tiles/AGENTS.md` back to the runbook's "Why not Planetiler" section, per
the house rule that rationale lives in a directory-level AGENTS.md.
