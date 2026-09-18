---
type: evidence
---

# Release checkpoint - commit 9f5f936a - 2026-09-18

Commit 9f5f936a wave 2, land-context Parquet readers plus auto-viewport hook, NDVI promotion lane
registered but not activated, federation step 2 literals behind the manifest, style fixes,
receipt f1f6410e over 934 files was pushed to origin/main at 20:41Z on 2026-09-18. This checkpoint
watched all four services to SUCCESS, re-ran the standard probe set, and added land-context and
NDVI-lane-specific probes. No local run, no writes to production data -- HTTP GET/POST-query
probes and read-only deploy log capture only.

## 1. Deploy watch Railway project Aevani

| Service | Deployment ID | Result | Commit landed |
| --- | --- | --- | --- |
| plantgeo-martin | 09d33190-451a-40dd-b479-d890e24349d1 | SUCCESS / RUNNING | 9f5f936a |
| plantgeo-parquet-api | 7f938393-3888-4477-ae90-343d3157114a | SUCCESS / RUNNING | 9f5f936a |
| plantgeo-job-executor | 2a2d8975-6587-4456-9e76-f92863fe725c | SUCCESS / RUNNING | 9f5f936a |
| plantgeo-main | fe42127d-a795-4d9d-9e67-a76aea17d29b | SUCCESS / RUNNING | 9f5f936a |

All four services landed cleanly on 9f5f936a. plantgeo-parquet-api and plantgeo-job-executor
depend on the refreshed quality receipt f1f6410e for this push (934 files); neither failed.

## 2. Functional probes production

All probes hit plantgeo.aevani.com and plantgeo-parquet-api-production.up.railway.app, both now
on 9f5f936a.

### /api/ready

GET https://plantgeo.aevani.com/api/ready -> 200
{"status":"ready","checks":{"configuration":true,"database":true,"redis":true},"timestamp":"2026-09-18T20:48:03.450Z"}

PASS.

### Parquet layer readers /api/v1/parquet/day

Same 13-request set as the prior checkpoints, replayed against 9f5f936a. Every state, served_day
and row count is unchanged from the pre-push baseline.

| Layer | Day | HTTP | state | served_day | Row count | Shape match |
| --- | --- | --- | --- | --- | --- | --- |
| burn-severity | 2026-09-11 | 200 | published | 2026-09-11 | 2 | match |
| burn-severity governed-absence day | 2026-08-31 | 200 | governed_absence | 2026-08-31 | n/a | match |
| fire-detections | 2026-09-12 | 200 | published | 2026-09-12 | 2 | match |
| fire-perimeters | 2026-09-04 | 200 | published | 2026-09-04 | 5 | match |
| water-gauges | 2026-09-14 | 200 | published | 2026-09-14 | 38 | match |
| weather-observations | 2026-09-07 | 200 | published | 2026-09-07 | 4 | match |
| soil-field-vpd | 2026-09-05 | 200 | published | 2026-09-05 | 58 | match |
| drought | 2026-09-08 | 200 | published | 2026-09-08 | 2 | match |
| vegetation | 2026-09-07 | 200 | published | 2026-09-07 | 0 | match, same zero-row bbox/day as before |
| watersheds | 2026-08-07 | 200 | published | 2026-08-07 | 24 | match |
| evacuation-zones | 2026-09-14 | 200 | published | 2026-09-14 | 0 | match |
| sensors | 2026-09-09 | 200 | published | 2026-09-09 | 98 | match |
| soil-survey | 2026-08-28 | 200 | lane_never_written | n/a | n/a | match, known pre-existing state |

Water gauges named-day rule. served_day 2026-09-14 equals that same rows observed_day 2026-09-14,
not the naive truncation of observed_at 2026-09-15T06:00:00Z. PASS, unchanged.

### tRPC

GET https://plantgeo.aevani.com/api/trpc/environmental.getSliderCapabilities -> 200
GET https://plantgeo.aevani.com/api/trpc/teams.listMyTeams -> 401
{"error":{"json":{"message":"UNAUTHORIZED","code":-32001,"data":{"code":"UNAUTHORIZED","httpStatus":401,"path":"teams.listMyTeams"}}}}

PASS, unchanged.

### Botanical occurrences query, parquet-api raw

GET https://plantgeo-parquet-api-production.up.railway.app/api/v1/botanical-occurrences/query
    ?release_set_id=956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4&bbox=-125,42,-111,49&zoom=5
-> 200
counts: {"matched":1711,"returned":500}, cells.length=500, sum(record_count)=2698

Unchanged from the pre-push baseline. PASS.

## 3. Burn-severity and drought scope-envelope check

The coordinator asked whether burn-severity now derives its scope envelope from the manifest
rather than a hardcoded constant, and whether that changed anything observable. Same request as
every prior checkpoint:

GET .../api/v1/parquet/day?layer=burn-severity&kind=observed&zoom=5&day=2026-09-11&bbox=-124,47,-122,49
-> 200, state published, served_day 2026-09-11, rows 2, identical mtbs_snapshot manifest fields
to every prior checkpoint in this series.

GET .../api/v1/parquet/day?layer=drought&kind=observed&zoom=5&day=2026-09-08&bbox=-124,47,-122,49
-> 200, state published, served_day 2026-09-08, rows 2, unchanged.

Both the MTBS-fed burn-severity lane and the USDM-fed drought lane are byte-for-byte unchanged
from the 64a586e1 baseline through every intervening push. The manifest-driven scope envelope did
not alter the served answer for this bbox/day pair. PASS.

## 4. Land-context tRPC procedures wave 2

grep of src/lib/server/trpc/routers/land-context.ts turned up five public procedures:
resolveBoundaryAtPoint, resolveBoundaryInArea, resolveBoundaryByParcelKey, lookupContactsForSubject,
coverageStatus, plus the side-effect-free draftInquiry. Probed the three read-only lookups with a
small PNW bbox/point (the new land-context Parquet readers this push adds are wave 2; the
underlying land-context-boundaries Parquet lane itself is not yet populated).

GET /api/trpc/landContext.resolveBoundaryInArea with bbox west -123 south 47 east -122.9 north
47.1 -> 200
{"status":"ok","data":[{"coverageState":"partial_area_coverage", ... "unresolvedGaps":["no Parquet lane named \"land-context-boundaries\" appears in the warehouse coverage census; source_unbound_for_region for the land-context reference plane"], ...}]}

GET /api/trpc/landContext.resolveBoundaryAtPoint with lon -122.95 lat 47.05 -> 200
{"status":"ok","data":[{"coverageState":"unknown_coverage", ... "unresolvedGaps":["no Parquet lane named \"land-context-boundaries\" appears in the warehouse coverage census; source_unbound_for_region for the land-context reference plane"], ...}]}

GET /api/trpc/landContext.coverageStatus with state WA county null -> 200
{"status":"ok","data":{"state":"WA","county":null,"coverageState":"unknown_coverage","gap":"no Parquet lane named \"land-context-boundaries\" appears in the warehouse coverage census; source_unbound_for_region for the land-context reference plane"}}

All three answer 200 with a typed governed-absence shape carrying the exact
source_unbound_for_region gap string the coordinator named -- never a 500, never an empty success
that would look like zero features exist. This is the correct behaviour for a reference plane
whose Parquet reader landed this push but whose underlying lane has no data yet. PASS.

## 5. job-executor logs, vegetation-ndvi-governed-plane-promotion lane

railway logs -d -s plantgeo-job-executor on the new deployment shows one
plantgeo_job_executor_inventory event at startup (2026-09-18T20:42:06Z) listing every registered
lane. The new lane appears exactly once, in that inventory, with active false:

lane_id vegetation-ndvi-governed-plane-promotion, active false, schedule 25 star star star star,
command python -m agri_data_service.execution.vegetation_partition_promotion, description
Governed-plane promotion for the vegetation NDVI direct-writer stream ... Wraps
execution/vegetation_ndvi_plane.register_governed_forward_plane, which has never had a caller.

No second occurrence of that lane_id appears anywhere else in the 400-line log tail: no
plantgeo_job_executor_repairs_authored entry names it, no run/tick/dead_letter event names it, and
the same-turn repairs_authored event (20:42:37Z) only authorizes sensors and vegetation gap
repairs -- the NDVI-writer lane itself, not the new promotion lane. This matches the coordinator's
expectation exactly: registered/inactive, does NOT tick. PASS.

## Verdict

PASS -- all four services (plantgeo-main, plantgeo-parquet-api, plantgeo-job-executor,
plantgeo-martin) redeployed cleanly to 9f5f936a. The standard probe set is unchanged from the
pre-push baseline. burn-severity and drought (the MTBS- and USDM-fed lanes) are byte-for-byte
unchanged despite the manifest-driven scope envelope. All three land-context tRPC reads answer
200 with the typed source_unbound_for_region governed-absence shape, never a 500 or empty
success. The new vegetation-ndvi-governed-plane-promotion lane is registered inactive in the
job-executor inventory and does not tick.
