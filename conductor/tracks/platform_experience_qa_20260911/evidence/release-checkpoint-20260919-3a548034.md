---
type: evidence
---

# Release checkpoint - commit 3a548034 - 2026-09-19

PASS -- all four gate services (plantgeo-main, plantgeo-parquet-api, plantgeo-job-executor,
plantgeo-martin) redeployed cleanly to 3a548034cdaeccb5e1529364cc58ecf0d6a868f7 (wave 10 push, head
commit "chore(wave10): verifier fixes and refreshed Python receipt"). plantgeo-main did NOT fail at
`check:data-boundary` or any other build step. Every probe in the standard set answered as expected
and every value that the 0503ccd8 baseline pinned is unchanged: /api/ready 200, botanical `/current`
still `latest_v1` on generation 956c0be7, both botanical bbox bands 200 with the baseline
servingRung and cell counts AND with the release-set pin equal to the pointer generation at BOTH
bands after the pin predicate was rewritten this wave, coverage still `coverage_schema_version` 3
with 14 `layer_bindings` / `region_slug` "pnw" / `region_display_name` "Pacific Northwest" /
land-context `unbound`, the land-context reader surfaces still answering the typed
`source_unbound_for_region` governed absence, drought 2026-09-08 and burn-severity 2026-09-11
selected-day reads byte-shape identical to the baseline on BOTH the direct parquet-api path and the
plantgeo-main tRPC path that now carries the new row-read region guard, four further layer reads
answering normally, and the job-executor inventory still showing
`vegetation-ndvi-governed-plane-promotion` `active: false` with `active_lane_count` 12.

**`region_identity_mismatch` appears ZERO times in production** across plantgeo-main,
plantgeo-parquet-api and plantgeo-job-executor logs, which is the required outcome: the row-read
identity guard shipped and every layer read still answers.

One upstream-provider observation, not a regression: the water-gauges lane took two USGS NWIS
HTTP 503s at 13:16:30Z and 13:17:00Z, entered retry backoff, and then completed normally at
13:19:29Z with `outcome="complete"`, `exit_code=0`, `days_written=5`, `days_unwritten=0`. Detail in
section 3.3.

Read-only throughout: no local run, no Railway variable written, no secret printed, no lane armed.
Every background process started for this watch was reaped.

plantgeo-ml (the fifth service, owned by another session) is INFORMATIONAL ONLY and not part of this
gate: its 3a548034 deployment 17538b65-19ea-427a-bf4d-b0252209dbee resolved SKIPPED with
`skippedReason: "No changes to watched files"`, the expected outcome for a platform push. No action
was taken on it.

## 1. Deploy watch, Railway project Aevani, environment production

| Service | Deployment ID | Result | Commit | Created (UTC) | First observed SUCCESS (UTC) |
| --- | --- | --- | --- | --- | --- |
| plantgeo-martin | 3183ca62-47e5-4b84-b0a0-da0fa1ce0a94 | SUCCESS | 3a548034 | 2026-09-19T13:14:00.260Z | 2026-09-19T13:14:40Z (already SUCCESS at first observation) |
| plantgeo-job-executor | 8b49729d-9812-4941-8055-9c89036b8bd0 | SUCCESS | 3a548034 | 2026-09-19T13:14:00.260Z | 2026-09-19T13:14:56Z |
| plantgeo-parquet-api | 23a2fe1f-cf52-40dd-81ae-22ed41254020 | SUCCESS | 3a548034 | 2026-09-19T13:14:00.260Z | 2026-09-19T13:16:00Z (DEPLOYING at 13:14:56Z) |
| plantgeo-main | 140579ca-a2bf-4232-9468-cc154702b5f3 | SUCCESS | 3a548034 | 2026-09-19T13:14:00.260Z | 2026-09-19T13:19:10Z |
| plantgeo-ml (NOT in the gate) | 17538b65-19ea-427a-bf4d-b0252209dbee | SKIPPED, "No changes to watched files" | 3a548034 | 2026-09-19T13:14:00.787Z | n/a |

All FOUR gate services fired their watch patterns on this push; none SKIPPED. Polled
`railway deployment list -s <service> --json` every ~60 s from 13:14:56Z until every gate service
reached a terminal status at 13:19:10Z -- roughly 5.2 minutes from deployment creation, well inside
the 25-minute give-up window. plantgeo-main was BUILDING at 13:14:56Z, 13:16:00Z, 13:17:03Z and
13:18:06Z and SUCCESS at 13:19:10Z. No build step failed; `check:data-boundary` in particular did
not fail -- the plantgeo-main container log shows a clean drizzle migration check followed by a
clean Next.js 16.2.2 boot. The background poll shell exited on its own and was confirmed reaped.

## 2. Functional probes, production

### 2.1 /api/ready

GET https://plantgeo.aevani.com/api/ready -> 200

```
{"status":"ready","checks":{"configuration":true,"database":true,"redis":true},"timestamp":"2026-09-19T13:19:26.481Z"}
```

PASS. Identical shape to the 0503ccd8 baseline.

### 2.2 Botanical occurrences pointer, parquet-api

GET https://plantgeo-parquet-api-production.up.railway.app/api/v1/botanical-occurrences/current -> 200

```
generation_id          956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4
pointer_kind           latest_v1
pointer_schema_version 1
pointer_written_at     2026-09-19T02:45:17.011994+00:00
product                botanical-occurrences
published_at           2026-09-13T13:18:49.937501+00:00
release_set_id         956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4
state                  current
manifest_sha256        6fecc4a911d4ee5b357cc17fd56d29e4898d13e6a553773e23275e14ae704110
qc_policy_version      botanical-qc-v1
taxonomy_recipe_version source-names-v1
```

`pointer_kind` is still `latest_v1` and the generation is still `956c0be7...`, byte-identical to the
baseline including `pointer_written_at`. PASS.

### 2.3 Botanical proxy reads, plantgeo-main, both bands, WITH the release-set pin check

The pin predicate changed again this wave (see section 5): the aggregate-band answer is now gated on
`isQueryEnabled` rather than on the band alone. The wire-observable consequence is that the pinned
`releaseSetId` must still equal the pointer generation at BOTH bands, and no band may show a stale
pin or lose its pin.

GET https://plantgeo.aevani.com/api/botanical-occurrences?bbox=-123,47,-122.8,47.2&zoom=8 -> 200

```
state         aggregate
servingRung   grid-0.05
supportId     grid-0.05
cells         36            counts {"returned": 36, "matched": 36}
releaseSetId          956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4
pointer.generationId  956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4
pointer.pointerKind   latest_v1
pointer.manifestChecksum 6fecc4a911d4ee5b357cc17fd56d29e4898d13e6a553773e23275e14ae704110
truncated     false
```

GET https://plantgeo.aevani.com/api/botanical-occurrences?bbox=-125,42,-111,49&zoom=5 -> 200

```
state         aggregate
servingRung   grid-0.25
supportId     grid-0.25
cells         500           counts {"returned": 500, "matched": 1711}
releaseSetId          956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4
pointer.generationId  956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4
pointer.pointerKind   latest_v1
pointer.manifestChecksum 6fecc4a911d4ee5b357cc17fd56d29e4898d13e6a553773e23275e14ae704110
truncated     true (nextCursor present)
```

servingRung and cell counts are identical to the 0503ccd8 baseline at both bands (grid-0.05/36 and
grid-0.25/500), and zoom=5 is correctly the coarser rung. At BOTH bands `releaseSetId` ==
`pointer.generationId` == `956c0be7...`, and `pointer.manifestChecksum` == the pointer's own
`manifest_sha256` from 2.2, so the pin, the manifest and the served cells are one lane's answer.
NEITHER band lost its pin and NEITHER shows a stale one. PASS.

(Note on format only: the baseline's 2.3 listed `manifestChecksum` as a top-level key. It is and was
nested under `pointer`; the baseline flattened it when transcribing. The value is unchanged.)

### 2.4 Coverage, parquet-api

GET https://plantgeo-parquet-api-production.up.railway.app/api/v1/parquet/coverage -> 200

```
top-level keys: coverage_schema_version, evaluated_through_day, generated_at, lanes,
                layer_bindings, region_display_name, region_slug
coverage_schema_version = 3
layer_bindings length   = 14
region_slug             = "pnw"
region_display_name     = "Pacific Northwest"
evaluated_through_day   = 2026-09-19
```

land-context row, quoted verbatim:

```json
{"layer": "land-context", "binding": "unbound", "source": null, "reason": "no_source_bound_in_region"}
```

botanical-occurrences row, quoted verbatim:

```json
{"layer": "botanical-occurrences", "binding": "bound_global", "source": "gbif", "reason": null}
```

The 14 layers: botanical-occurrences, burn-severity, drought, evacuation-zones, fire-detections,
fire-perimeters, land-context, sensors, signal, soil-survey, vegetation, water-gauges, watersheds,
weather-observations. Unchanged from the baseline in every field. PASS.

### 2.5 Row reads, BOTH paths -- the region-identity behaviour change

The new guard (`assertServedRegionMatchesBundle`) lives in plantgeo-main, so the direct parquet-api
reads establish the baseline shape and the plantgeo-main tRPC reads are what actually exercise it.

Direct parquet-api, same recipe as the baseline
(`.../api/v1/parquet/day?layer=<L>&kind=observed&zoom=5&day=<D>&bbox=-124%2C47%2C-122%2C49`):

| Layer | Day | HTTP | state | requested_day | served_day | Rows | vs baseline |
| --- | --- | --- | --- | --- | --- | --- | --- |
| drought | 2026-09-08 | 200 | published | 2026-09-08 | 2026-09-08 | 2 | match |
| burn-severity | 2026-09-11 | 200 | published | 2026-09-11 | 2026-09-11 | 2 | match |
| water-gauges | 2026-09-17 | 200 | published | 2026-09-17 | 2026-09-17 | 38 | n/a (new probe) |
| fire-detections | 2026-09-17 | 200 | published | 2026-09-17 | 2026-09-17 | 6 | n/a (new probe) |

`truncated` false on all four. burn-severity still carries its own `mtbs_snapshot` block.

Through plantgeo-main's tRPC readers, which now call `assertServedRegionMatchesBundle` before every
row read:

| Procedure | Input | HTTP | state | requestedDay | servedDay | Rows |
| --- | --- | --- | --- | --- | --- | --- |
| environmental.getDroughtClassification | bbox=-124,47,-122,49 date=2026-09-08 zoom=5 | 200 | ready | 2026-09-08 | 2026-09-08 | 2 |
| environmental.getBurnSeverity | bbox=-124,47,-122,49 date=2026-09-11 zoom=5 | 200 | ready | 2026-09-11 | 2026-09-11 | 2 |
| environmental.getStreamflow | bbox=-124,47,-122,49 zoom=5 | 200 | ready | 2026-09-19 | 2026-09-19 | 38 |
| environmental.getSensorStations | bbox=-124,47,-122,49 zoom=5 | 200 | ready | 2026-09-19 | 2026-09-19 | 18 |
| environmental.getEvacuationZones | bbox=-124,47,-122,49 zoom=5 | 200 | ready | 2026-09-19 | 2026-09-16 | 0 |
| wildfire.getFireDetections | bbox=-124,47,-122,49 zoom=5 | 200 | not_generated (`reason: "day_not_written"`) | 2026-09-19 | n/a | n/a |

drought and burn-severity match the baseline exactly (2 rows each, published/ready, requested day
served). Four further layer reads answered normally -- well past the "at least two other layer
reads succeed" bar. The fire-detections `not_generated` / `day_not_written` answer is the ordinary
governed absence for TODAY's not-yet-written partition, not a refusal: the same layer at 2026-09-17
returns 6 rows on the direct path above. `environmental.getWatersheds` was attempted and rejected
at 400 by its own pre-existing 1-square-degree bbox ceiling before any row read, so it is not
counted here.

**No response body on any of these probes contained the string `region_identity_mismatch`.** PASS.

### 2.6 Land-context surfaces

GET https://plantgeo.aevani.com/api/trpc/landContext.resolveBoundaryAtPoint?input=%7B%22json%22%3A%7B%22lon%22%3A-122.33%2C%22lat%22%3A47.61%7D%7D -> 200

```json
{"result":{"data":{"json":{"status":"ok","data":[{"coverageState":"source_unbound_for_region","sourceFeature":null,"sourceRelease":null,"matchedRegionOrOverlap":null,"organizationOffice":null,"route":null,"roleOrRouteType":null,"assignmentEvidence":null,"publicContactUrl":null,"verificationTime":null,"documentedHelp":null,"unresolvedGaps":["Land context is not available in this region: this deployment covers Pacific Northwest and its region manifest binds no land-context data source, so no parcel, boundary or office record exists for it anywhere here. This is not a gap in the record and not a failed lookup."],"isCurrentReferenceOnly":true,"geometry":null}]}}}}
```

GET https://plantgeo.aevani.com/api/trpc/landContext.resolveBoundaryInArea?input=%7B%22json%22%3A%7B%22bbox%22%3A%7B%22west%22%3A-122.4%2C%22south%22%3A47.5%2C%22east%22%3A-122.2%2C%22north%22%3A47.7%7D%7D%7D -> 200

Same body shape: `"coverageState":"source_unbound_for_region"` with the identical refusal sentence.

GET https://plantgeo.aevani.com/api/trpc/landContext.coverageStatus?input=%7B%22json%22%3A%7B%22state%22%3A%22WA%22%2C%22county%22%3Anull%7D%7D -> 200

```json
{"result":{"data":{"json":{"status":"ok","data":{"state":"WA","county":null,"coverageState":"source_unbound_for_region","gap":"Land context is not available in this region: this deployment covers Pacific Northwest and its region manifest binds no land-context data source, so no parcel, boundary or office record exists for it anywhere here. This is not a gap in the record and not a failed lookup."}}}}}
```

Byte-identical to the 0503ccd8 baseline on all three surfaces. The typed governed absence survived
wave 10 intact. PASS.

As in the baseline, the land-context AGENT TOOL refusal envelope remains NOT EXERCISED: its only
dispatch surface `POST /api/ai/regional-intelligence` is session-gated and read-only QA holds no
session. Unchanged status, not a new gap.

## 3. job-executor: startup, a full hourly cycle, and the weather lane

### 3.1 Startup inventory

`railway logs -s plantgeo-job-executor -d 8b49729d-9812-4941-8055-9c89036b8bd0`

`plantgeo_job_executor_inventory` at 2026-09-19T13:14:57Z lists **14 lanes, 12 active**, matching
`active_lane_count=12` on every `plantgeo_job_executor_tick_started` line and `lane_count=14` on
every `plantgeo_job_executor_tick_healthy` line.

| Lane | Schedule | active |
| --- | --- | --- |
| vegetation-sentinel2-ndvi-direct-forward | 5 * * * * | true |
| fire-perimeters-direct-forward | 10 * * * * | true |
| fire-detections-direct-forward | 15 * * * * | true |
| water-gauges-direct-forward | 15 * * * * | true |
| sensors-direct-forward | 20 * * * * | true |
| **vegetation-ndvi-governed-plane-promotion** | 25 * * * * | **false** |
| weather-observations-direct-forward | 30 * * * * | true |
| evacuation-zones-direct-forward | 35 * * * * | true |
| climate-nasa-power-direct-forward | 40 * * * * | true |
| drought-direct-forward | 45 * * * * | true |
| soil-era5-land-direct-forward | 50 * * * * | true |
| watersheds-direct-forward | 0 3 * * * | true |
| burn-severity-direct-forward | 55 8 * * * | true |
| mtbs-forward | 55 7 * * 2 | false |

`vegetation-ndvi-governed-plane-promotion` is `active: false` and was NOT armed by this pass. Its
13:25:00Z bucket shows `run_id: null`, `run_status: null`, `state: "shadow"`,
`blockers: ["lane is not in the active allow-list"]` -- a schedule prediction only; the lane was
never dispatched. PASS.

### 3.2 A full hourly cycle, 13:05Z through 13:54Z

Every hourly lane's 13:xx bucket reached `run_status: "succeeded"` on this deployment:

```
vegetation-sentinel2-ndvi-direct-forward  succeeded  2026-09-19T13:05:00+00:00
fire-perimeters-direct-forward            succeeded  2026-09-19T13:10:00+00:00
fire-detections-direct-forward            succeeded  2026-09-19T13:15:00+00:00
water-gauges-direct-forward               succeeded  2026-09-19T13:15:00+00:00
sensors-direct-forward                    succeeded  2026-09-19T13:20:00+00:00
vegetation-ndvi-governed-plane-promotion  (null)     2026-09-19T13:25:00+00:00   <- shadow, unarmed
weather-observations-direct-forward       succeeded  2026-09-19T13:30:00+00:00
evacuation-zones-direct-forward           succeeded  2026-09-19T13:35:00+00:00
climate-nasa-power-direct-forward         succeeded  2026-09-19T13:40:00+00:00
drought-direct-forward                    succeeded  2026-09-19T13:45:00+00:00
soil-era5-land-direct-forward             succeeded  2026-09-19T13:50:00+00:00
```

`plantgeo_job_executor_tick_healthy incomplete_lanes=[] lane_count=14 leader=True` on every tick
from 13:19:59Z through the last observed tick at 13:54:21Z. Case-insensitive grep across the whole
captured window for `Traceback`, `ImportError`, `CRITICAL`, `contract_mismatch`,
`region_identity_mismatch`, `duplicate grain` and `DeprecationWarning`: **zero matches for every
pattern.** No crash loop.

Note on log levels: several lane events are tagged `[ERRO]` by Railway because those lanes write
their structured JSON to stderr. The payloads read `outcome="written"` / `outcome="complete"` /
`exit_code=0`; the tag is a stream classification, not a fault.

### 3.3 water-gauges: two upstream 503s, then a clean completion

```
2026-09-19T13:16:30.721519165Z [INFO]  detail="upstream request failed with status 503" error_type="UpstreamHttpError" event="water_gauges_forward_failed"
2026-09-19T13:17:00.632644708Z [INFO]  detail="upstream request failed with status 503" error_type="UpstreamHttpError" event="water_gauges_forward_failed"
```

Those two ticks carried `failed=true` with the lane at `state: "failed"`,
`detail: "work item entered retry backoff"`, `retried: 1`. They are the ONLY two `failed=true` ticks
in the window. The same run then completed:

```
2026-09-19T13:19:29.185996261Z  event="water_gauges_forward_complete" outcome="complete" exit_code=0
  days=5 days_written=5 days_unwritten=0 rows=79472 rows_added=797 rows_updated=10
  recovered_duplicate_rows=0 availability_extended=5 unwritten=[]
  run_id="water-gauges-nwis-forward-20260919T131759Z"
```

A transient USGS NWIS 503 absorbed by the lane's own retry policy. Not attributable to this push and
not a FAIL; recorded so a future reader does not mistake the two `failed=true` ticks for a
regression.

### 3.4 weather-observations: the rewired lane, one full turn

This wave rewired the lane to run recovery BEFORE retention on every poll. Its 13:30:00Z turn,
complete event chain:

```
2026-09-19T13:30:17.470001444Z  event="weather_observations_forward_fetch"
  days_seen=["2026-09-19"] days_selected=["2026-09-19"] observations_written=83
  points_sampled=98 points_unavailable=15 fetched_at="2026-09-19T13:30:08.185227+00:00"
  run_id="weather-observations-direct-forward-20260919T133008Z"

2026-09-19T13:30:37.378795033Z  event="weather_observations_source_retention"
  source_checkpoints_attempted=83 source_checkpoints_retained=83 source_checkpoints_failed=0

2026-09-19T13:30:37.378804743Z  event="weather_observations_forward_checkpoint"
  day="2026-09-19" outcome="written" existing_rows=1182 incoming_rows=83
  incoming_rows_verified=83 merged_rows=1265 added_rows=83 updated_rows=0 parts=1 bytes=19376
  tier_statuses={"0":"data","5":"data","9":"data","13":"data"}

2026-09-19T13:30:37.378809723Z  event="weather_observations_forward_complete"
  outcome="complete" exit_code=0 days=1 days_written=1 days_unwritten=0 unwritten=[]
  recovered_days=[] source_checkpoints_attempted=83 source_checkpoints_retained=83
  source_checkpoints_failed=0 rows=1265 rows_added=83 rows_updated=0 availability_extended=1
```

The NEW retention fields are present and clean: `source_checkpoints_attempted=83`,
`source_checkpoints_retained=83`, **`source_checkpoints_failed=0`**. Because
`_bucket_verdict` now downgrades a turn to `incomplete` on any retention failure, a zero here is
what keeps `outcome="complete"` honest.

**No `no_retained_capture` verdict anywhere in the window** -- zero occurrences of the string across
every log captured. The lane did not start failing and did not double-write: `merged_rows` 1265 =
`existing_rows` 1182 + `added_rows` 83, with `updated_rows=0` and `rows_updated=0`, so the 83
readings landed once each and nothing already published was rewritten.

`recovered_days=[]` is the correct answer for this turn and is explained in section 5.

## 4. plantgeo-main and plantgeo-parquet-api log sweep

`railway logs -s plantgeo-main -d 140579ca... -n 400` (8 lines) and
`railway logs -s plantgeo-parquet-api -d 23a2fe1f... -n 400` (18 lines), both on the new
deployments.

| Pattern | plantgeo-main | plantgeo-parquet-api | plantgeo-job-executor |
| --- | --- | --- | --- |
| Traceback | 0 | 0 | 0 |
| contract_mismatch | 0 | 0 | 0 |
| **region_identity_mismatch** | **0** | **0** | **0** |
| duplicate grain | 0 | 0 | 0 |
| DeprecationWarning | 0 | 2 (one event, pre-existing) | 0 |

- plantgeo-main: zero matches. Clean tail -- container start, `migrate: drizzle migrations are up to
  date`, one stop/start cycle, Next.js 16.2.2 ready on :8080.
- plantgeo-parquet-api: the single DeprecationWarning is the SAME pre-existing Sanic framework
  notice documented in the 37963657 and 0503ccd8 checkpoints:
  `sanic/logging/deprecation.py:33 [DEPRECATION v26.6] Passing the loop argument to listeners is
  deprecated. Your listener 'create_app.<locals>.setup_resources' should only accept the app
  argument.` NO new DeprecationWarning class was introduced.
- Also present on parquet-api, pre-existing and not a fault: `region_bindings_unverified region=pnw`
  with ten source slugs at INFO (logged after `assert_region_bindings_are_servable` has already
  passed), plus the known POWER `ALLSKY_SFC_SW_DWN` outage as
  `coverage_rollup_probe_deferred` / `availability_coverage_withheld code=availability_stale` for
  `climate-field-shortwave-radiation`.

PASS, no new warning class introduced by this push.

## 5. The two behaviour changes exercised: what was proven, what was not

### 5.1 The pin predicate, again (`useBotanicalViewportLanes.ts`)

**What changed.** W8 scoped `aggregateBandAnswer` by BAND
(`band === "detail" ? undefined : botanicalQuery.data`). Wave 10 replaced that with the enablement
predicate the hook already owned:

```ts
const aggregateBandAnswer = isQueryEnabled ? botanicalQuery.data : undefined;
```

The reasoning recorded in the comment is that `isQueryEnabled` is false at the detail band BY
CONSTRUCTION, so it subsumes the band test -- and it additionally closes the identical hole at the
AGGREGATE band, where the react-query observer is equally disabled whenever both aggregate toggles
are off and `keepPreviousData` still hands back the last landed frame. `isError` was rewired onto
the same predicate in the same change.

**What was PROVEN here.** The wire contract the pin depends on is intact after the rewrite: at BOTH
bands (zoom=8 detail-adjacent narrow bbox and zoom=5 wide bbox) the proxy answers 200 with
`releaseSetId` == `pointer.generationId` == `956c0be7...` and `pointer.manifestChecksum` ==
the pointer's own `manifest_sha256`. No band lost its pin; no band shows a stale one; servingRung
and cell counts are unchanged from the baseline. A predicate change that had dropped or mismatched
the pin on either band would have shown here.

**What was NOT proven.** The defect the predicate closes is a CLIENT-side one: a retained frame
from a DISABLED observer supplying a pin for a screen it was never read for. Reproducing it needs a
mounted hook with the aggregate toggles switched off while a previous aggregate answer is still in
`keepPreviousData`, which is a React state transition. A server probe issues one independent
request per call and can never hold a retained frame, so the browser-side half of this change is
covered by its unit tests
(`src/__tests__/components/map/layer-manager/useBotanicalViewportLanes.test.tsx`) and not by this
checkpoint. Treat the client half as unexercised in production -- not as a known failure.

### 5.2 Region identity enforced on ROW reads (`parquet-plane-client.ts`)

**What changed.** `assertServedRegionMatchesBundle()` is now awaited at the top of
`getParquetLayerDay`, `getParquetLayerDayWindow` and `getParquetLatestRelease`, and is exported so
`botanical-occurrences-client.ts` -- which speaks its own wire contract -- can call it before its
POINTER read. It compares the region the newest decoded census STATED (`lastStatedRegionSlug`)
against the slug this bundle was compiled for, via the single `regionIdentityVerdict` function that
the slider's capability withholding already used. On `mismatch` it throws `ParquetRegionIdentityError`
carrying `reason = "region_identity_mismatch"`, which
`src/app/api/botanical-occurrences/route.ts` maps to a 503 `governed_refusal`. Silence still
renders: an `unstated` census, or a census that did not answer at all, proceeds exactly as before.

**What was PROVEN here.** A correctly configured deployment behaves exactly as it did before the
guard existed. Six plantgeo-main tRPC row reads across five layers (drought, burn-severity,
water-gauges/streamflow, sensors, evacuation-zones, fire-detections) all answered normally through
the guarded path; drought 2026-09-08 and burn-severity 2026-09-11 are row-for-row identical to the
baseline; the botanical proxy, which now calls the guard before its pointer read, still returns its
pointer and pin at both bands. `region_identity_mismatch` was searched for explicitly and is
**absent from every production log on all three services and from every probe body**. That is the
required outcome: its appearance would have been a regression, since production's compiled slug and
served slug are both `pnw` (coverage `region_slug: "pnw"`, section 2.4).

**What was NOT proven.** The REFUSAL path itself. Making the guard fire requires
`PLANTGEO_REGION` and `NEXT_PUBLIC_PLANTGEO_REGION` to disagree, i.e. writing a Railway variable,
which this read-only pass is forbidden to do and would not do in production regardless. So the 503
`governed_refusal` body, the `ParquetRegionIdentityError` message text and the log line
"Parquet row read refused: the plane names a region this bundle was not compiled for" are covered by
unit tests and by code reading, not by a production observation. Also NOT proven: the
`unstated`-census render path, since this deployment's census always states `pnw`, and the
once-per-process cold-census cost, since the slider had already warmed `lastStatedRegionSlug`
before any probe ran.

### 5.3 Incidental to the above: weather recovery-before-retention ordering

The weather lane's 13:30Z turn ran with `recovered_days=[]`. That is CORRECT and expected, not a
skipped step: `_days_owed_a_recovery` only probes days that are not `data` at every tier, and
2026-09-19 was already complete at z0/z5/z9/z13 from the 12:30Z poll, so no day was owed a recovery
and no `weather_observations_source_recovery` event was emitted. What this turn proves is that the
rewire did not break the ordinary path -- one poll, retention measured and clean, one merge, no
double write, `outcome="complete"`. What it does NOT prove is the ORDER the change exists for
(reparse retained responses before this poll's checkpoints overwrite them), because that ordering is
only observable on a turn that actually owes a recovery. Observing one would require a genuinely
failed prior bucket. Unexercised, not failed.

## Verdict

PASS. All four gate services -- plantgeo-main, plantgeo-parquet-api, plantgeo-job-executor,
plantgeo-martin -- landed SUCCESS on 3a548034cdaeccb5e1529364cc58ecf0d6a868f7; none SKIPPED and
plantgeo-main did not fail `check:data-boundary` or any other build step. /api/ready 200; botanical
pointer still `latest_v1` on generation 956c0be7; both botanical bands match the baseline
servingRung and cell counts with the release-set pin equal to the serving generation at BOTH bands
after the pin predicate was rewritten; coverage still schema 3 / 14 bindings / `region_slug` "pnw" /
`region_display_name` "Pacific Northwest" / land-context `unbound`; the land-context reader surfaces
still answer the typed `source_unbound_for_region` refusal; drought 2026-09-08 and burn-severity
2026-09-11 selected-day reads are unchanged from the baseline on both the direct and the newly
guarded plantgeo-main path, and four further layer reads answer normally; **`region_identity_mismatch`
appears zero times in production**; the job-executor reports 14 lanes / 12 active with
`vegetation-ndvi-governed-plane-promotion` `active: false` and never dispatched, a full hourly cycle
13:05Z-13:50Z in which every scheduled lane succeeded, `tick_healthy` on every tick and no Traceback;
the weather-observations lane completed its 13:30Z turn with the new retention fields clean
(`source_checkpoints_failed=0`), no `no_retained_capture` anywhere, and no double write; and the log
sweep is clean apart from the one pre-existing Sanic DeprecationWarning, the known POWER shortwave
staleness, and two transient USGS 503s that the water-gauges retry policy absorbed into a clean
completion. UNEXERCISED (not failed, see section 5): the client-side half of the pin predicate, the
region-identity REFUSAL path, the weather recovery ORDERING on a turn that owes a recovery, and --
unchanged from the baseline -- the session-gated land-context agent-tool envelope. No regression
introduced by wave 10.

plantgeo-ml (informational, another session's service, not in this gate): deployment
17538b65-19ea-427a-bf4d-b0252209dbee on 3a548034 resolved SKIPPED, `skippedReason: "No changes to
watched files"`. No action taken.
