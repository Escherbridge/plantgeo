---
type: evidence
---

# Release checkpoint - commit 0503ccd8 - 2026-09-19

PASS (one probe NOT EXERCISED, see below) -- all four gate services (plantgeo-main,
plantgeo-parquet-api, plantgeo-job-executor, plantgeo-martin) redeployed cleanly to
0503ccd847ebda73362d33c8c43cd88067eac8fc (wave 9 push, head commit "docs: NDVI re-activation
attempt and rollback evidence"). plantgeo-main did NOT fail at `check:data-boundary` or any other
build step. Every probe in the standard set answered as expected: /api/ready 200, botanical
`/current` pointer still `latest_v1` on generation 956c0be7, both botanical bbox bands 200 with the
baseline servingRung and cell counts AND with the release-set pin equal to the generation the cells
came from at both bands (blocker B3), coverage still `coverage_schema_version` 3 with 14
`layer_bindings` plus the two NEW additive keys `region_slug` / `region_display_name` and
land-context still `unbound`, the land-context reader surfaces now answering the typed
`source_unbound_for_region` governed absence instead of an empty result, drought and burn-severity
selected-day reads byte-shape identical to the c922509d baseline, and the job-executor inventory
still showing `vegetation-ndvi-governed-plane-promotion` `active: false` with `active_lane_count`
12 and no Traceback or crash loop. The one item NOT exercised is the land-context AGENT TOOL
refusal: that surface is reachable only behind an authenticated session (section 2.5), so it was
verified by shared-gate reasoning and code citation, not by a live production call. Read-only
throughout: no local run, no Railway variable written, no secret printed, no lane armed. Every
background poll process started for this watch was reaped.

plantgeo-ml (the fifth service, owned by another session) is INFORMATIONAL ONLY and not part of
this gate: its 0503ccd8 deployment 89864131-02ce-4769-ad69-bf7f6175c323 resolved SKIPPED by watch
pattern. No action was taken on it.

## 1. Deploy watch, Railway project Aevani, environment production

| Service | Deployment ID | Result | Commit | Created (UTC) | First observed SUCCESS (UTC) |
| --- | --- | --- | --- | --- | --- |
| plantgeo-martin | 70435a49-87c5-4514-8498-79eeb3789482 | SUCCESS | 0503ccd8 | 2026-09-19T12:05:22.194Z | 2026-09-19T12:06:39Z |
| plantgeo-job-executor | d05c8c08-4fd9-41f5-8188-3208907c6f63 | SUCCESS | 0503ccd8 | 2026-09-19T12:05:22.196Z | 2026-09-19T12:06:39Z |
| plantgeo-parquet-api | 1df9f2f2-6f2d-4bd3-bcc1-2b95a7121f74 | SUCCESS | 0503ccd8 | 2026-09-19T12:05:22.196Z | 2026-09-19T12:06:39Z |
| plantgeo-main | 55c87428-bbe2-467f-8019-5f49319c3efd | SUCCESS | 0503ccd8 | 2026-09-19T12:05:22.194Z | 2026-09-19T12:11:01Z |
| plantgeo-ml (NOT in the gate) | 89864131-02ce-4769-ad69-bf7f6175c323 | SKIPPED by watch pattern | 0503ccd8 | 2026-09-19T12:05:22.658Z | n/a |

All four gate services fired their watch patterns on this push; none legitimately SKIPPED. Polled
`railway deployment list` every 60 s from 12:06:39Z. martin, job-executor and parquet-api were
already SUCCESS on the first poll; plantgeo-main was BUILDING at 12:06:39Z and 12:08:50Z and
SUCCESS at 12:11:01Z, roughly 5.7 minutes after the deployment was created. No build step failed;
`check:data-boundary` in particular did not fail, and the plantgeo-main container log shows a clean
drizzle migration check followed by a clean Next.js 16.2.2 boot. The background poll shell and its
`sleep` child were killed after the gate closed.

## 2. Functional probes, production

### 2.1 /api/ready

GET https://plantgeo.aevani.com/api/ready -> 200

```
{"status":"ready","checks":{"configuration":true,"database":true,"redis":true},"timestamp":"2026-09-19T12:12:11.954Z"}
```

PASS. Identical shape to the c922509d baseline.

### 2.2 Botanical occurrences pointer, parquet-api

GET https://plantgeo-parquet-api-production.up.railway.app/api/v1/botanical-occurrences/current -> 200

```
generation_id        956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4
pointer_kind         latest_v1
pointer_schema_version 1
pointer_written_at   2026-09-19T02:45:17.011994+00:00
product              botanical-occurrences
published_at         2026-09-13T13:18:49.937501+00:00
release_set_id       956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4
state                current
manifest_sha256      6fecc4a911d4ee5b357cc17fd56d29e4898d13e6a553773e23275e14ae704110
```

`pointer_kind` is still `latest_v1` and the generation is still `956c0be7...`, unchanged from the
baseline. No `legacy_current_json` key anywhere in the body (the legacy pointer write was retired in
75f37f0a this wave and the pointer is unaffected). PASS.

### 2.3 Botanical proxy reads, plantgeo-main, both bands, WITH the release-set pin check (B3)

GET https://plantgeo.aevani.com/api/botanical-occurrences?bbox=-123,47,-122.8,47.2&zoom=8 -> 200

```
servingRung   grid-0.05
supportId     grid-0.05
cells         36            counts {"returned": 36, "matched": 36}
releaseSetId          956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4
pointer.generationId  956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4
pointer.pointerKind   latest_v1
truncated     false
```

GET https://plantgeo.aevani.com/api/botanical-occurrences?bbox=-125,42,-111,49&zoom=5 -> 200

```
servingRung   grid-0.25
supportId     grid-0.25
cells         500           counts {"returned": 500, "matched": 1711}
releaseSetId          956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4
pointer.generationId  956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4
pointer.pointerKind   latest_v1
truncated     true (nextCursor present)
```

servingRung and cell counts are identical to the c922509d baseline at both bands (grid-0.05/36 and
grid-0.25/500), and zoom=5 is correctly the coarser rung.

Release-set pin, blocker B3 (commit c060e119, "pin the release set to the lane that rendered the
cells"): at BOTH bands the response's pinned release `releaseSetId` equals
`pointer.generationId` equals `956c0be7...`, i.e. the pinned release IS the generation the cells
were read from. `manifestChecksum` on both bands is `6fecc4a9...`, matching the pointer's
`manifest_sha256` in 2.2, so the pin, the manifest and the served cells are one lane's answer. The
client-side half of B3 (the aggregate band's retained `keepPreviousData` answer no longer pinning
its releaseSetId onto a detail-band screen) is a store/band branch that a server probe cannot
observe; the wire contract it depends on is correct here. PASS.

### 2.4 Coverage, parquet-api, WITH the two new additive keys

GET https://plantgeo-parquet-api-production.up.railway.app/api/v1/parquet/coverage -> 200

```
top-level keys: coverage_schema_version, generated_at, evaluated_through_day, lanes,
                layer_bindings, region_slug, region_display_name
coverage_schema_version = 3
layer_bindings length   = 14
region_slug             = "pnw"                  <- NEW this wave
region_display_name     = "Pacific Northwest"    <- NEW this wave
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
weather-observations. Schema version is still 3 (the two new keys are ADDITIVE, so no version bump
was owed), the count is still 14, and land-context is still `unbound`. PASS.

### 2.5 Land-context: the behaviour change

Reader path (tRPC over HTTP, `publicProcedure`, no authentication) -- three procedures exercised.

GET https://plantgeo.aevani.com/api/trpc/landContext.resolveBoundaryAtPoint?input=%7B%22json%22%3A%7B%22lon%22%3A-122.33%2C%22lat%22%3A47.61%7D%7D -> 200

```json
{"result":{"data":{"json":{"status":"ok","data":[{"coverageState":"source_unbound_for_region","sourceFeature":null,"sourceRelease":null,"matchedRegionOrOverlap":null,"organizationOffice":null,"route":null,"roleOrRouteType":null,"assignmentEvidence":null,"publicContactUrl":null,"verificationTime":null,"documentedHelp":null,"unresolvedGaps":["Land context is not available in this region: this deployment covers Pacific Northwest and its region manifest binds no land-context data source, so no parcel, boundary or office record exists for it anywhere here. This is not a gap in the record and not a failed lookup."],"isCurrentReferenceOnly":true,"geometry":null}]}}}}
```

GET https://plantgeo.aevani.com/api/trpc/landContext.resolveBoundaryInArea?input=%7B%22json%22%3A%7B%22bbox%22%3A%7B%22west%22%3A-122.4%2C%22south%22%3A47.5%2C%22east%22%3A-122.2%2C%22north%22%3A47.7%7D%7D%7D -> 200

Same body shape: `"coverageState":"source_unbound_for_region"` with the identical refusal sentence in
`unresolvedGaps`, `geometry` null, no features.

GET https://plantgeo.aevani.com/api/trpc/landContext.coverageStatus?input=%7B%22json%22%3A%7B%22state%22%3A%22WA%22%2C%22county%22%3Anull%7D%7D -> 200

```json
{"result":{"data":{"json":{"status":"ok","data":{"state":"WA","county":null,"coverageState":"source_unbound_for_region","gap":"Land context is not available in this region: this deployment covers Pacific Northwest and its region manifest binds no land-context data source, so no parcel, boundary or office record exists for it anywhere here. This is not a gap in the record and not a failed lookup."}}}}}
```

This is the CORRECT outcome, not a failure. Before this wave these surfaces answered an empty
result (or, worse, the budget refusal `outside_pilot_states`, which tells a reader to ask something
smaller when there is nothing smaller to ask). The refusal now names the deployment's region and
says explicitly that it is not a gap in the record and not a failed lookup, so a model relaying it
cannot restate it as "nothing found there" -- a claim about the ground rather than about the
deployment.

Region-scoped request vocabulary, same commit (0c9130e7): the `state` enum is no longer the pilot's
compile-time tuple but is validated per parse against the SELECTED manifest.

GET https://plantgeo.aevani.com/api/trpc/landContext.coverageStatus?input=%7B%22json%22%3A%7B%22state%22%3A%22TX%22%2C%22county%22%3Anull%7D%7D -> 400

```json
{"error":{"json":{"message":"[{\"code\":\"custom\",\"message\":\"not a subdivision code this deployment's region admits\",\"path\":[\"state\"]}]","code":-32600,"data":{"code":"BAD_REQUEST","httpStatus":400,"path":"landContext.coverageStatus"}}}}
```

PASS for every reader path exercised.

AGENT TOOL -- NOT EXERCISED in production, blocked by authentication. The six land-context tools run
IN-PROCESS on plantgeo-main (`src/lib/server/services/land-context-tools.ts`, dispatched by
`callRegionalEvidenceTool` in `src/lib/server/services/regional-evidence-tools.ts:99`) and are
deliberately NOT part of the parquet-api remote registry. The only HTTP surface that dispatches
them, `POST /api/ai/regional-intelligence`, is session-gated:

```
POST https://plantgeo.aevani.com/api/ai/regional-intelligence -> 401
{"error":"Authentication required","retryable":false}
```

Read-only QA holds no session and will not mint one, so the tool envelope
`{"error":"not_available_in_region", ...}` could not be quoted from production. Two facts were
verified instead:

- GET https://plantgeo-parquet-api-production.up.railway.app/api/v1/agent-tools/ -> 200 lists 15
  remote tools (signals_near_point, drought_history_at_point, fire_history_near_point,
  forecast_summary_for_cell, signal_value_on_day, signal_neighbors_in_time, nearest_signal_cells,
  observation_coverage_on_day, observation_temporal_neighbors, feature_value_near_point,
  surface_value_near_point, and the four botanical tools). No land-context tool name appears, which
  is correct and also means the registry-collision guard at
  `src/lib/server/services/regional-evidence-tools.ts:78` cannot fire.
- `callLandContextTool` asks the region BEFORE parsing arguments and returns
  `landContextRegionAbsence()` -- `{"error":"not_available_in_region","unbound_layers":["land-context"],
  "region_slug":...,"region_display_name":...,"note": <the same refusal sentence quoted above>}` --
  through the same `isLandContextBoundInRegion()` gate that the three reader probes above just
  proved returns false in this deployment. The gate is shared; only the envelope differs.

Treat the agent-tool half as open until a session-authenticated pass can quote it. It is not a
known failure; it is unverified.

### 2.6 Drought and burn-severity selected-day reads

Same URLs as the c922509d baseline (recipe from
`conductor/tracks/platform_experience_qa_20260911/evidence/api-samples-session3-20260914.json`,
bbox=-124,47,-122,49, zoom=5, kind=observed).

| Layer | Day | HTTP | state | requested_day | served_day | Rows | vs baseline |
| --- | --- | --- | --- | --- | --- | --- | --- |
| drought | 2026-09-08 | 200 | published | 2026-09-08 | 2026-09-08 | 2 | match |
| burn-severity | 2026-09-11 | 200 | published | 2026-09-11 | 2026-09-11 | 2 | match |

`truncated` false on both. The drought rows still carry `area_id` `direct:2026-09-08:0`,
`dm_category`, `source_url https://droughtmonitor.unl.edu/data/json/usdm_20260908.json` and
MultiPolygon geometry. The burn-severity response still carries its own `mtbs_snapshot` block with
`capture_complete: true`, `manifest_sha256 4690af46...`, `mode full_replacement`, `source_row_count
747`, `available_day 2026-09-11`.

The drought lane gained a `--target-day` CLI flag this wave (75e7c77a, "pin one settled USDM
release"). It is an INGEST-side flag only: serving is byte-shape identical to the baseline. PASS.

## 3. job-executor startup log

`railway logs -s plantgeo-job-executor -d d05c8c08-4fd9-41f5-8188-3208907c6f63 -n 200` (68 lines,
the full available tail for the new deployment).

- `plantgeo_job_executor_inventory` at 2026-09-19T12:06:12Z lists 14 lanes, 12 with `active: true`,
  matching the `active_lane_count=12` on every `plantgeo_job_executor_tick_started` line.
- The two inactive lanes are `mtbs-forward` and **`vegetation-ndvi-governed-plane-promotion`
  `active: false`** -- unchanged, still unarmed, and nothing in this pass armed it.
- `plantgeo_job_executor_tick_healthy incomplete_lanes=[] lane_count=14 leader=True` on every tick.
- Case-insensitive grep for `traceback`, `importerror`, `modulenotfound`, `crash`, `restarting`,
  `CRITICAL`: zero matches. No crash loop.
- Region boot check passed: `assert_region_bindings_are_servable` raises on failure and the process
  reached steady-state ticking, so it passed. One informational
  `coverage_rollup_probe_deferred layer=climate-field-shortwave-radiation` line is the known POWER
  `ALLSKY_SFC_SW_DWN` upstream outage, pre-existing and unrelated to this push.

PASS.

## 4. plantgeo-main and plantgeo-parquet-api log sweep

`railway logs -s plantgeo-main -d 55c87428... -n 200` (8 lines) and
`railway logs -s plantgeo-parquet-api -d 1df9f2f2... -n 200` (18 lines), both on the new
deployments, grepped case-insensitively for `Traceback`, `contract_mismatch`,
`region_identity_mismatch` and `DeprecationWarning`.

| Pattern | plantgeo-main | plantgeo-parquet-api | plantgeo-job-executor |
| --- | --- | --- | --- |
| Traceback | 0 | 0 | 0 |
| contract_mismatch | 0 | 0 | 0 |
| region_identity_mismatch | 0 | 0 | 0 |
| DeprecationWarning | 0 | 2 (one event, pre-existing) | 0 |

- plantgeo-main: zero matches. Clean tail -- container start, `migrate: drizzle migrations are up to
  date`, one stop/start cycle, Next.js 16.2.2 ready on :8080.
- plantgeo-parquet-api: the single DeprecationWarning is the SAME pre-existing Sanic framework
  notice already documented and dismissed in
  `conductor/tracks/platform_experience_qa_20260911/evidence/release-checkpoint-20260918-37963657.md`
  section 5 and re-confirmed in the c922509d baseline: `sanic/logging/deprecation.py:33
  [DEPRECATION v26.6] Passing the loop argument to listeners is deprecated. Your listener
  'create_app.<locals>.setup_resources' should only accept the app argument.` It is framework noise
  from `create_app`, present before this push, not a regression. NO new DeprecationWarning.
- Also present on parquet-api, not a fault: `region_bindings_unverified region=pnw
  source_slugs=[...10 slugs...]` at INFO level. This is `app.py:55`, which logs sources with no
  declared coverage claim AFTER `assert_region_bindings_are_servable` has already passed;
  `services/agri-data-service/src/agri_data_service/app.py` is byte-identical between c922509d and
  0503ccd8, so the event is pre-existing, not introduced by wave 9. A
  `availability_coverage_withheld code=availability_stale` WARN for
  `climate-field-shortwave-radiation` is the known POWER outage, also pre-existing.

PASS, no new warning class introduced by this push.

## 5. Intended behaviour changes confirmed in this release

- **Land-context now refuses per region.** Commit 0c9130e7. Every land-context reader asks
  `isLandContextBoundInRegion()` before it asks anything else
  (`src/lib/server/services/land-context/reader.ts`, gate function
  `src/lib/server/services/land-context/region-binding.ts`). Where the deployment's manifest binds
  no land-context source -- which is every region today, the PNW pilot included -- the answer is the
  typed governed absence `source_unbound_for_region` carrying the sentence quoted in 2.5, and no
  read is attempted. The matching agent-tool envelope is `not_available_in_region` with
  `unbound_layers`, `region_slug`, `region_display_name` and the same `note`. **A refusal on these
  surfaces is the CORRECT shipped outcome for the PNW deployment, not a probe failure.** What it
  replaces is worse than an empty result: a budget refusal (`outside_pilot_states`) that told a
  reader its area was too large for a layer the platform simply does not hold there.
- **Region identity on the coverage payload.** Same commit. `/api/v1/parquet/coverage` gained the
  two additive top-level keys `region_slug` ("pnw") and `region_display_name` ("Pacific Northwest").
  Additive, so `coverage_schema_version` correctly stays 3 and the 14-row `layer_bindings` array is
  untouched. They exist so an operator can correlate a refusal payload with which region a
  deployment actually is.
- **Release-set pin follows the lane that rendered the cells.** Commit c060e119 (blocker B3).
  Verified on the wire at both bands in 2.3.
- **Drought `--target-day`.** Commit 75e7c77a. Ingest-side CLI flag; serving unchanged, verified
  in 2.6.

## Verdict

PASS with one item unverified. All four gate services -- plantgeo-main, plantgeo-parquet-api,
plantgeo-job-executor, plantgeo-martin -- landed SUCCESS on
0503ccd847ebda73362d33c8c43cd88067eac8fc; none SKIPPED and plantgeo-main did not fail
`check:data-boundary` or any other build step. /api/ready 200; botanical pointer still `latest_v1`
on generation 956c0be7; both botanical bands match the baseline servingRung and cell counts with the
release-set pin equal to the serving generation at both bands; coverage still schema 3 / 14
bindings / land-context `unbound`, now with the new `region_slug` and `region_display_name` keys;
the land-context reader surfaces answer the typed `source_unbound_for_region` refusal (the intended
change) and reject a non-admitted subdivision code with 400; drought and burn-severity selected-day
reads are unchanged from the baseline; the job-executor still reports
`vegetation-ndvi-governed-plane-promotion active: false`, `active_lane_count` 12, no Traceback and
no crash loop; the log sweep is clean apart from the one pre-existing Sanic DeprecationWarning and
the known POWER shortwave staleness. UNVERIFIED: the land-context AGENT TOOL refusal envelope,
whose only dispatch surface is session-gated (401) and therefore unreachable to read-only QA. No
regression introduced by wave 9.

plantgeo-ml (informational, another session's service, not in this gate): deployment
89864131-02ce-4769-ad69-bf7f6175c323 on 0503ccd8 resolved SKIPPED by watch pattern. No action taken.
