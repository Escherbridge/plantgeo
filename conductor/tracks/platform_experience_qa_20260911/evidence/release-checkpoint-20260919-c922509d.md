---
type: evidence
---

# Release checkpoint - commit c922509d - 2026-09-19

PASS -- all four services (plantgeo-main, plantgeo-parquet-api, plantgeo-job-executor,
plantgeo-martin) redeployed cleanly to c922509d3feec94fb11793553383da2f7d0abd41 (wave 8 push,
docs commit: "docs: prune RUNBOOK to open state; wave 8 record, pointer-advance and NDVI
activation evidence"). Every probe from the standard set answered as expected: /api/ready 200,
botanical current pointer latest_v1 with no legacy_current_json field anywhere in the response,
both botanical bbox queries 200 with pointerKind latest_v1 and the expected coarser servingRung at
zoom=5, coverage 14 layer_bindings with land-context unbound/botanical-occurrences
bound_global, drought and burn-severity selected-day reads matching the 5c8c34c4-checkpoint shape
(same row counts, published state, served_day), the job-executor startup log showing
vegetation-ndvi-governed-plane-promotion active:false / active_lane_count 12 with zero
ImportError/Traceback/crash-loop signal, and only one pre-existing, already-documented Sanic
framework DeprecationWarning in plantgeo-parquet-api log (unrelated to this push, traced to the
20260918-37963657 checkpoint). Read-only against production throughout: no local run, no Railway
variable writes, no destructive commands. All started background poll processes were reaped on
completion.

## 1. Deploy watch, Railway project Aevani, environment production

| Service | Deployment ID | Result | Commit landed |
| --- | --- | --- | --- |
| plantgeo-martin | 460b7e8d-3f66-4a7c-a6c1-f58d0b6f8c62 | SUCCESS (already resolved at watch start) | c922509d |
| plantgeo-job-executor | 2729f604-5d11-4acd-adff-e7dc192b9947 | SUCCESS (already resolved at watch start) | c922509d |
| plantgeo-parquet-api | 70132715-4106-48b4-b531-f071c2418d07 | SUCCESS at 2026-09-19T03:34:58Z | c922509d |
| plantgeo-main | 49fb677f-36eb-4b20-8b3d-796e07097cfb | SUCCESS at 2026-09-19T03:38:09Z | c922509d |

Polled deployment lists every 60 s for plantgeo-main and plantgeo-parquet-api (the two still
building or deploying at watch start); plantgeo-martin and plantgeo-job-executor already showed
SUCCESS on this commit hash when the watch began, so no polling was needed for those two. No
service needed to be excluded as SKIP; all four watch patterns fired for this push. plantgeo-main
did not fail at the check:data-boundary build step or any other build step; its main.log startup
tail (below) shows a clean drizzle migration check followed by a clean Next.js 16.2.2 boot with no
errors. Total resolve time: main and parquet-api both green within about 5.5 minutes of watch
start.

## 2. Functional probes, production

All probes hit plantgeo.aevani.com and plantgeo-parquet-api-production.up.railway.app, both now on
c922509d.

### /api/ready

GET https://plantgeo.aevani.com/api/ready -> 200
{"status":"ready","checks":{"configuration":true,"database":true,"redis":true},"timestamp":"2026-09-19T03:38:21.662Z"}

PASS.

### Botanical occurrences pointer, parquet-api /api/v1/botanical-occurrences/current

GET https://plantgeo-parquet-api-production.up.railway.app/api/v1/botanical-occurrences/current -> 200
generation_id 956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4, pointer_kind
latest_v1, pointer_schema_version 1, pointer_written_at 2026-09-19T02:45:17.011994+00:00, product
botanical-occurrences, published_at 2026-09-13T13:18:49.937501+00:00, state current.

pointer_kind equals latest_v1, generation 956c0be7 matches the pinned release. Grepped the full raw
response body for legacy_current_json: zero matches anywhere. PASS.

### Botanical occurrences query, plantgeo-main proxy

GET https://plantgeo.aevani.com/api/botanical-occurrences?bbox=-123,47,-122.8,47.2&zoom=8 -> 200
pointer.pointerKind = latest_v1, servingRung = grid-0.05, cells.length = 36 (greater than 0).

GET https://plantgeo.aevani.com/api/botanical-occurrences?bbox=-125,42,-111,49&zoom=5 -> 200
pointer.pointerKind = latest_v1, servingRung = grid-0.25, cells.length = 500 (greater than 0),
correctly coarser than the zoom=8 request (0.25-degree grid vs 0.05-degree grid). Both responses
carry the same pointer generation 956c0be7. PASS.

### Coverage, parquet-api /api/v1/parquet/coverage

GET https://plantgeo-parquet-api-production.up.railway.app/api/v1/parquet/coverage -> 200
coverage_schema_version = 3, layer_bindings.length = 14.

land-context row, quoted verbatim:
{"layer":"land-context","binding":"unbound","source":null,"reason":"no_source_bound_in_region"}

botanical-occurrences row, quoted verbatim:
{"layer":"botanical-occurrences","binding":"bound_global","source":"gbif","reason":null}

PASS.

### Drought and burn-severity selected-day reads

Recipe replayed from the exact URLs recorded in
conductor/tracks/platform_experience_qa_20260911/evidence/api-samples-session3-20260914.json
(bbox=-124,47,-122,49, zoom=5, kind=observed), matching the shape reported by the
release-checkpoint-20260919-5c8c34c4.md baseline.

| Layer | Day | HTTP | state | served_day | Row count | Shape match vs 5c8c34c4 checkpoint |
| --- | --- | --- | --- | --- | --- | --- |
| drought | 2026-09-08 | 200 | published | 2026-09-08 | 2 | match |
| burn-severity | 2026-09-11 | 200 | published | 2026-09-11 | 2 | match, own mtbs_snapshot block present with capture_complete true |

Both feature/area ids, geometry, and metadata fields are structurally identical to the
5c8c34c4-checkpoint recipe (same day, same bbox, same row counts, same published state). PASS.

## 3. job-executor startup log

railway logs -s plantgeo-job-executor -n 500 (55 lines returned, the full available tail for the
current deployment).

- plantgeo_job_executor_inventory startup event lists 14 registered lanes; 12 report
  active: true, matching the active_lane_count=12 field logged 12 times in this tail.
- The vegetation-ndvi-governed-plane-promotion lane entry has active: false, unchanged from every
  prior checkpoint in this series (2026-09-18/19 owner decision to leave promotion unarmed pending
  the checksum-scoping call remains in effect).
- Case-insensitive grep for traceback, importerror, modulenotfounderror, and
  assert_region_bindings: zero matches. No crash loop (restarting, crash, CRITICAL: zero matches).
  The region boot check passed silently (no assert_region_bindings failure logged).

PASS.

## 4. plantgeo-main and plantgeo-parquet-api log sweep

railway logs -s plantgeo-main -n 200 (8 lines, full available tail) and
railway logs -s plantgeo-parquet-api -n 200 (18 lines, full available tail), both on the new
deployments, grepped case-insensitively for DeprecationWarning, contract_mismatch, and Traceback.

- plantgeo-main: zero matches of any kind. Tail is a clean startup: drizzle migrations up to date,
  container start and stop cycling once, Next.js 16.2.2 ready, container start again.
- plantgeo-parquet-api: zero contract_mismatch or Traceback matches. One DeprecationWarning event
  (2 lines: the warning plus its warn source line) from sanic/logging/deprecation.py, DEPRECATION
  v26.6, about passing the loop argument to listeners, naming the setup_resources listener inside
  create_app. This is the same pre-existing Sanic-framework create_app loop-argument deprecation
  noise already documented and dismissed in
  conductor/tracks/platform_experience_qa_20260911/evidence/release-checkpoint-20260918-37963657.md
  section 5, unrelated to wave 8 changes, present before this push, not a regression.

PASS, no new warnings introduced by this push.

## Verdict

PASS -- all four services (plantgeo-main, plantgeo-parquet-api, plantgeo-job-executor,
plantgeo-martin) landed cleanly on c922509d3feec94fb11793553383da2f7d0abd41. plantgeo-main did not
fail check:data-boundary or any other build step. Every functional probe answered correctly:
/api/ready, botanical pointer (latest_v1, no legacy_current_json), both botanical bbox queries
(correct pointer/servingRung/nonzero cells at both zoom levels), the 14-row
/api/v1/parquet/coverage with land-context unbound and botanical-occurrences bound_global, and
drought/burn-severity selected-day reads matching the 5c8c34c4-checkpoint shape. The job-executor
startup log shows the vegetation-ndvi promotion lane still correctly inactive,
active_lane_count=12, zero ImportError/Traceback/crash-loop signal. plantgeo-main and
plantgeo-parquet-api logs are clean except one pre-existing, already-documented Sanic framework
DeprecationWarning unrelated to this push. No regression introduced by wave 8.
