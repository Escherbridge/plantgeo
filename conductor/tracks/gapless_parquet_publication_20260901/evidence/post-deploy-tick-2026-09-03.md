---
type: track-evidence
track: gapless_parquet_publication_20260901
slice: deployment-observation
status: red_build_failures_diagnosed
observed_at: 2026-09-03
---

# Post-push deployment observation — 2026-09-03

Continuation plan step 1 of the 2026-09-03 handoff. Read with the Railway MCP against project
`6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990`, environment `b7cfa813-8a5c-4fcd-80f2-cab736d840a7`.

## Verdict: RED — neither Python image built; production runs mixed versions

| service | push at `e4a101f` (06:28 UTC) | push at `821a830` (12:13 UTC) | code actually running |
|---|---|---|---|
| `plantgeo-main` | `acf6853c` SUCCESS (later REMOVED by the next deploy) | `70703284` SUCCESS, live 12:17 | **new** (`821a830`) |
| `plantgeo-parquet-api` (agri service) | `56fa0804` **FAILED**, stage `quality-receipt` | `9f4e77e0` SKIPPED (docs-only change outside watch paths) | **old** (`40ea78b0` @ `e4490c3`) |
| `plantgeo-job-executor` | `fbc4cbb7` **FAILED**, stage `BUILD_IMAGE` (wrong Dockerfile) | `a4b8396e` SKIPPED | **old** (`b1f35a20` @ `e4490c3`) |
| `plantgeo-martin` | — | `97ddd4cb` SUCCESS | unchanged image |
| `plantgeo-ingest-cron`, `plantgeo-cron-mtbs`, `plantgeo-cron-soilgrids` | — | all **FAILED** at 12:13 with an empty build log | fenced (no-op start, `NEVER`); their `infra/cron-*` Dockerfiles were deleted in `e4490c3`, so every push now fails their build. Harmless noise until the services are removed per the scheduler handoff. |

Consequence: the wave-1/2 runtime repairs (`jobs-matview-refresh` `relation_absent`, WFIGS page halving,
gap-fill rung repair, coverage authority v2) are NOT in production. The old executor still ticks
every ~30 s and every tick logs `plantgeo_job_executor_tick_unhealthy` with
`failing_lanes=['jobs-matview-refresh', 'maintenance-validate-streams', 'parquet-drought',
'parquet-evacuation-zones', 'parquet-fire-perimeters', 'parquet-soil-survey',
'postgres-fire-perimeters', 'postgres-vegetation', 'soilgrids-cache-warm', 'vegetation-catch-up', …]`
(read 12:16–12:25 UTC from deployment `b1f35a20`). No new-code tick can be recorded yet.

## Failure 1 — agri service: the quality receipt can never verify from a Windows checkout

Build log, stage `[quality-receipt 5/5] RUN python scripts/verify_quality_receipt.py`:

```
QUALITY RECEIPT REFUSED: the tree does not match its receipt -- source changed without a green sweep.
  receipt: sha256:3824cf2c7033b635c662d8114b09f68d2e35ea8ad1cdb5383e831dac413b566e over 842 files
  tree:    sha256:b0ec43471f7c3d222b7fa26a3985cbbc9f7a8b956064370136f3c731222f2e2f over 842 files
```

Reproduced locally: `python scripts/verify_quality_receipt.py` on the working tree → verified
(`3824cf2c…`); the same command on `git archive HEAD services/agri-data-service` (the bytes a Linux
clone sees) → refused with exactly Railway's `b0ec4347…`. Per-file comparison: 0 files added or
missing, **181 of 842 files differ, every one only by CRLF→LF** (e.g. `interface/cli/commands.py`
4,941 CR bytes in the working tree, 0 in the archive). `.gitattributes` declares `* text=auto eol=lf`,
so git commits LF, but this checkout (`core.autocrlf=true`, files rewritten by tools) holds CRLF that
`git status` reports as clean. `compute_tree_digest` hashes raw disk bytes, so any receipt written on
this machine describes bytes the build context never contains. This is a defect in the wave-3 gate
itself, not a stale receipt; re-running `--write-receipt` cannot fix it.

Fix landed as `1da1a28`: `compute_tree_digest` normalizes CRLF to LF before hashing (digest domain
v2), regression test, docs; one green sweep rewrote the receipt (`55d40b35…`, 844 files); the receipt was
re-verified on a `git archive 1da1a28` extraction before the push (see below). An independent review of
the fix reproduced the digest three ways (Windows-normalized, Linux-raw, Linux-normalized, all
`55d40b35…`) and confirmed the CRLF replacement equals git's clean filter for all 844 inputs; it asked
for the committed-bytes property to be enforced by the writer rather than by hand, which the same-day
closure adds to `check.py --write-receipt`.

## Failure 2 — executor: a GitHub push builds the wrong Dockerfile

Build log for `fbc4cbb7`: `[build 3/9] COPY . .` then `[build 4/9] RUN if [ "production" = "production" ]
… node -e … NEXT_PUBLIC_PMTILES_URL …` → `Error: NEXT_PUBLIC_PMTILES_URL must be a reviewed production
URL`. Those are the **repository-root** Next.js Dockerfile's steps. The service config reads
`dockerfilePath: infra/job-executor/Dockerfile`, `rootDirectory: /`, but has **no config-as-code file
set**, so Railway discovers the root `railway.json` (`build.dockerfilePath: "Dockerfile"`) on push and
it overrides the dashboard path (the mechanism recorded 2026-08-04 for `plantgeo-ingest-cron`).
History proves the pattern: at `e4490c3` the push deploy `003bfc6e` died identically at 17:51 UTC and
only the manual redeploy `b1f35a20` at 18:03 built `infra/job-executor/Dockerfile`. Every future push
will fail the same way until the setting exists.

Required setting (one field, reversible; the Railway MCP `update-service` mutation was denied by the
permission classifier in this session and Railway CLI 5.45.2 has no service-update verb):

- service `plantgeo-job-executor` (`565ecaad-9946-48f1-8a0b-28fa60494a16`) → Settings → Config-as-code
  file path = `services/agri-data-service/railway.job-executor.json` (already in the repo:
  `dockerfilePath: infra/job-executor/Dockerfile`, start command `agri-service ops jobs-executor`,
  `ON_FAILURE`/10). Equivalent MCP call: `update-service{railwayConfigFile:
  "services/agri-data-service/railway.job-executor.json"}` in environment `b7cfa813-…`.

The repository already documents this exact requirement (`docs/deployment.md:567-568`,
`infra/job-executor/AGENTS.md:9-12`: Root Directory `/`, Config-as-code path
`/services/agri-data-service/railway.job-executor.json`, Dockerfile `infra/job-executor/Dockerfile`
"as one coordinated change"); production diverges from its documented configuration. Interim
workaround per push: `railway service redeploy --service plantgeo-job-executor --environment
production --from-source --yes`, which builds the latest commit under the service's own settings
(the path that produced `b1f35a20`).

Once set, the executor image also needs Failure 1 fixed (same receipt stage in
`infra/job-executor/Dockerfile`).

## Mixed-version risk while the Python images are stale

`src/lib/server/services/parquet-plane-client.ts` reads only `coverage_schema_version` 2 and throws
`ParquetPlaneContractError` on any other version. See the "coverage schema" note appended below for
what the old service (`e4490c3`) emits.

### Coverage schema note — production is degraded right now, not merely stale

`git grep coverage_schema_version e4490c3 -- services/agri-data-service/src` returns nothing: the
running Parquet API emits no `coverage_schema_version` at all, while the running web app requires the
key (`parquet-plane-client.ts`, zod `z.number().int()`, then `!== 2` → `ParquetPlaneContractError`).
`parquet-trpc-readers.ts` maps that error to `{ state: "upstream_unavailable", fault: { kind:
"contract" } }` and `parquet-slider-capabilities.ts` counts it as a coverage boundary fault, so every
Parquet-backed layer and its slider has been reporting "upstream unavailable" since `plantgeo-main`
went live at 12:17 UTC on `821a830`. `/api/ready` still answers 200 (0.66 s). Nothing is lost, but
the site is worse than before the push until `plantgeo-parquet-api` builds. This is why the digest
fix is pushed ahead of the wave-3 review verdict.

## Fix push `1da1a28` — 12:39 UTC

| service | deployment | result |
|---|---|---|
| `plantgeo-parquet-api` | `3a3430bf` (push) | `[quality-receipt 5/5]` printed `quality receipt verified: sha256:55d40b35… over 844 files` at 12:39:59 UTC; runtime stage continued (`COPY --from=quality-receipt` at 12:40:08). Final status recorded below. |
| `plantgeo-job-executor` | `9fa4c8a8` (push) | FAILED in 11 s, root Dockerfile again (`[build 4/9] … NEXT_PUBLIC_PMTILES_URL`). |
| `plantgeo-job-executor` | `5523d2e8` (`railway service redeploy --from-source`, 12:40:28) | FAILED in 11 s, **same root-Dockerfile failure** — so a from-source redeploy does NOT bypass root config discovery either. The `b1f35a20` success on 2026-09-02 must have run under a config-file setting that no longer exists. The executor cannot deploy by any repository-side or CLI action; the config-as-code path must be set on the service. |

The digest fix was verified before the push by extracting `git archive 1da1a28 services/agri-data-service`
and running `python scripts/verify_quality_receipt.py` on it: `verified: sha256:55d40b35… over 844
files` — identical to what Railway then computed.

## Parquet API live at `1da1a28` — deployment `3a3430bf`, SUCCESS 12:41:27 UTC

Container up 12:41:25 (`service_profile: published_reader`). Verified through the public web app
procedure `environmental.getSliderCapabilities`:

| probe (UTC) | latency | result |
|---|---|---|
| 12:42:3x (first after deploy) | 8.4 s at the edge; the app's own upstream call **timed out** (`Parquet slider coverage unavailable; withholding every Parquet-owned row { error: 'Upstream request timed out' }`, app log 12:42:39) | `parquetCoverageUnavailable: true`, 22 Parquet capabilities withheld with `coverage_unavailable` |
| 12:43+ (second, third) | 0.64 s / 0.44 s | `parquetCoverageUnavailable: false`, `parquetCoverageGeneratedAt 12:42:26.9`, 17 layers listed, 7 withheld |

API-side timeline of that first census (its logs): 12:42:27 `availability_census_fallback` for every
un-bootstrapped observed lane ("no bootstrap receipt and no pointer, so this lane still costs a
whole-stream listing"), 12:42:45–54 `snapshot_forward_census_listing` for each snapshot-rooted
climate/soil product, 12:42:55 `snapshot_coverage_withheld` with `census_budget_exhausted` ("exceeded
its 30000-key aggregate listing budget; a partial census would report lanes it never reached as
absent, so nothing is claimed at all") for at least `climate-field-relative-humidity` and
`climate-field-dew-point`. So under `census_until_bootstrap` a cold coverage census takes ~28 s,
longer than the app's coverage timeout, and the snapshot products can exhaust the listing budget.
This is the designed pre-bootstrap behaviour, not a regression to route back: it is exactly what
step 4 (per-lane availability bootstrap → one pointer GET + one generation GET) and step 5 (the
authority flip) remove, and it is why "zero LIST on the request path" is the step-5 tripwire.
Coverage schema v2 is confirmed live: the app no longer reports a contract fault.

## Step 2 — browser check at `1da1a28` (web) + `3a3430bf` (API), 2026-09-03 ~12:50 UTC

Headless Chromium (Playwright 1.62.1, `--use-gl=angle`), fresh anonymous context per scenario,
1440×900, camera set through `?focusLng&focusLat&focusZoom`, layers toggled through the Map manager
rows (`data-testid="layer-row-<toggle>"`). Script and raw report: session scratchpad
`browser-check.cjs` / `shots/report.json`; captures sent to the owner and copied under the multiscale
track evidence.

| scenario | reader call | slider day | drawn | verdict |
|---|---|---|---|---|
| fire, default PNW camera (~z6) | `wildfire.getFireDetections` 200, 91 KB, 946 ms | 2026-09-01 (settled, lag 2) | density cells (yellow→red squares), no perimeters | GREEN — cells; the not-a-perimeter line is a hover caption (`src/lib/map/fire-cell-caption.ts`), verified separately below |
| climate air temperature, z8 | `environmental.getClimateField` 200, 109 KB, 683 ms | 2026-08-06 (the known ~27-day tail; step 6 closes it) | filled 1° tessellation, one rung, cell outlines are the deliberate `fill-outline-color` stroke from `src/lib/map/layer-styles.ts:21-23`, not cracks | GREEN (pixel seam check still owed to acceptance A2) |
| vegetation + water gauges, z5 | `environmental.getVegetationIndex` 200 (2,993 ms), `environmental.getStreamflow` 200 (610 ms), `getGroundwater` 200 | 2026-09-01 / 2026-09-03 ("Mixed dates" banner) | vegetation 0.25° cells; water gauges as cells | GREEN |
| soil moisture (ERA5-Land), z5 | `environmental.getSoilField` 200, **1.9 KB**, 1,261 ms | none — the row mounted no date input | nothing drawn after 4 s; "Loading map data…" banner still up | **INCONCLUSIVE at first pass** — re-run with a longer settle and the response body recorded below |

No request to `/api/fires` in any scenario (0 of ~170 requests each); zero console or page errors;
map load 2.7–3.4 s after the first (cold) 5.8 s. The batched
`layers.getIngestionCoverage,environmental.getSliderCapabilities` call took 8,183 ms cold and
165–737 ms afterwards (the census memo).

Capability roster the app resolved (`environmental.getSliderCapabilities`): 17 layers listed under
`coverage_authority: census`; withheld — `fire-perimeters`, `sensors`, `watersheds`,
`evacuation-zones` as `reader_not_parquet` (the four non-Parquet layers, expected), `soil-survey` as
`lane_never_written` (expected, key cap), and `climate-field-dew-point` and
`climate-field-relative-humidity` as **`lane_not_registered`** — but the API withheld those two with
`census_budget_exhausted`, so the app is relabelling an API withholding as a registration gap.
Contract note for the reader-cutover track: a lane absent from the coverage answer because the API
withheld it should surface the API's `withheld_reason`, not `lane_not_registered`. Ceilings as served:
soil-field-* through 2026-08-02, climate-field-* through 2026-08-06 (shortwave 2026-05-31),
fire/vegetation/weather/drought 2026-09-01, water 2026-09-03.

**Second pass (longer settle, response bodies recorded).** Soil moisture at z5 is GREEN: the first
pass had caught the row before its capability resolved (the 1.9 KB answer was the pre-day read); with
a 15 s settle `environmental.getSoilField` answered 474 KB of 0.25° polygons for
`soil-field-moisture-0-7cm` on 2026-08-02 (the slider's latest; "1 gap with no data: 2026-08-03 to
2026-09-03" is the 31-day tail step 7 closes) and the map drew a uniform 0.25° tessellation with no
nested blocks. Fire's reader body confirms the contract: `state: "ready"`, `requestedDay ==
servedDay == 2026-09-01`, every cell carrying `support.supportKind: "aggregate_cell"`, `zoomTier 5`,
`cellWidthDegrees 0.2`, `origin: "cell_origin"`, `aggregationMethod: "count"`. The climate body is a
FeatureCollection of 1° polygons with `aggregated: true` and `coverageFraction: 1`. The fire
not-a-perimeter caption is rendered in the hover tooltip (`src/lib/map/fire-cell-caption.ts`) and
sampling mouse positions over the canvas did not surface a tooltip element under automation, so it
remains verified in code only. Step 2 verdict: **GREEN on all four gates** (fire cells + no
`/api/fires`; climate z8 filled one-rung tessellation; vegetation and water z5 cells; soil moisture z5
without nested blocks). Captures: `conductor/tracks/multiscale_polygon_surface_20260901/evidence/screenshots-2026-09-03/`.

## Closure push `4a679d2` — 14:13 UTC

| service | deployment | result |
|---|---|---|
| `plantgeo-parquet-api` | `36e86e9c` (push) | `quality receipt verified: sha256:5ad493c5… over 1124 files` at 14:13:54 UTC (the hardened gate: schema 2, digest domain v2, `alembic/`, `db/`, `mypy.ini`, `ruff.toml`, `alembic.ini` now covered); `DEPLOYING` at 14:14. |
| `plantgeo-job-executor` | `b4c11f50` (push) | FAILED in 9 s, root Dockerfile — unchanged until the owner sets the Config-as-code path. |
| `plantgeo-main` | push | redeploys as usual. |

Wave-3 closure contents and the review ledger are in `conductor/RUNBOOK.md` (HANDOFF 2026-09-03, "Progress
2026-09-03"). Step 1 stands at: web app and API observed at the new code; executor observation blocked on
the owner's one setting. Steps 4–12 of the continuation plan are unchanged and next.

## Step 4 scoping note — 2026-09-03

The availability bootstrap input is a per-lane document of `(day, rung)` rows each binding `{key, sha256}`
receipts for the source object, the terminal marker, every data part and the completion marker
(`availability_index.py:361-400`, `:2409-2466`), plus `input_receipts` for the manifests/checkpoints; `--apply`
verifies the digests against R2 (`:1038`). No compiler exists and completion markers carry no part digests, so
producing the document means listing and hashing the whole ladder per lane. Step 4 therefore starts with a
tooling slice (charter under this track) and must wait for the new executor to be live. Recorded in the
RUNBOOK progress block.

## Executor deployed - 18:13 UTC

`update-service railwayConfigFile` was rejected ("Config as Code is deprecated. Use Infrastructure as Code
(.railway/railway.ts)"). Fix `fd79875`: main's settings moved onto the service, root `railway.json` deleted.
`railway service redeploy --from-source` -> `c3ffa03d`: `load build definition from infra/job-executor/Dockerfile`
(18:11:37), `quality receipt verified: sha256:5ad493c5... over 1124 files` (18:11:48), SUCCESS 18:13:55. Push
`ac9ec00` (dependency removals) -> `4f2502a0` SUCCESS 18:19:17 unaided; `3b6de19b` (API) and `34ad922c` (main)
rolling out at 18:22.

### First new-code ticks (deployment `c3ffa03d`, 18:14-18:17 UTC)

`plantgeo_job_executor_tick_started active_lane_count=37` every ~30 s, leader acquired; `jobs-strategy-mv-refresh`
`succeeded`; `water-gauges-direct-forward` 18:15 bucket: `water_gauges_forward_failed` `UpstreamHttpError`
"upstream request failed with status 503" -> `job_work_item_failed attempt_number=1 disposition=retry_wait
max_attempts=5` (expected transient). The lane table shows every other active lane `not_due` with its 18:00
bucket `succeeded`, the eleven shadow lanes `would_be_due_if_activated`, and TEN lanes in state `failed` with
detail `latest run remains failed; clear its dead-lettered work before another bucket opens`, still at their
2026-09-02 buckets - including the two blocker lanes this observation was meant to prove. See the RUNBOOK progress
block ("NEW BLOCKER"): the executor gate at `job_executor_service.py:1282` never reopens a lane after a dead
letter and no verb clears one, so the wave-1 matview/WFIGS repairs have not executed in production.
