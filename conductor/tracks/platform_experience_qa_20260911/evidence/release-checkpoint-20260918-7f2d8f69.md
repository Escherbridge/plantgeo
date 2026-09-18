---
type: evidence
---

# Release checkpoint - commit 7f2d8f69 - 2026-09-18

Commit 7f2d8f69 region manifest, dead service modules deleted, receipt b964e9c1 over 930 files
was pushed to origin/main at 19:41Z on 2026-09-18. This checkpoint watched the resulting Railway
auto-deploy of all four services to completion, then re-ran the full functional and data-quality
probe set against production. No local run, no writes to production data -- HTTP GET probes only.

## 1. Deploy watch Railway project Aevani

| Service | Deployment ID | Result | Commit landed |
| --- | --- | --- | --- |
| plantgeo-martin | 330c3a3b-f18a-4dc9-9081-3b2ce5fa6a5d | SUCCESS / RUNNING | 7f2d8f69 |
| plantgeo-parquet-api | cb11b89b-b681-4d54-b156-bae383a273bd | SUCCESS / RUNNING | 7f2d8f69 |
| plantgeo-job-executor | d3413903-a7db-482b-85bc-3aa039828c57 | SUCCESS / RUNNING | 7f2d8f69 |
| plantgeo-main | c6a43daf-3ccc-4f01-a768-75ab10d8715b | SUCCESS / RUNNING | 7f2d8f69 |

All four services landed cleanly on 7f2d8f69. plantgeo-parquet-api and plantgeo-job-executor
depend on the refreshed quality receipt b964e9c1 for this push (930 files); neither failed, so no
build-log capture was needed for that failure mode. plantgeo-main also built clean this time --
the earlier check:data-boundary failure was already fixed by hotfix 8451ebcf before this push, and
the region-manifest / dead-module-deletion change did not reintroduce a boundary violation.
Martin deployed the same image path as before; nothing in this push required martin.railway.json
changes.

## 2. Functional probes production

All probes hit plantgeo.aevani.com and plantgeo-parquet-api-production.up.railway.app, both now
on 7f2d8f69.

### /api/ready

GET https://plantgeo.aevani.com/api/ready -> 200
{"status":"ready","checks":{"configuration":true,"database":true,"redis":true},"timestamp":"2026-09-18T19:52:14.137Z"}

PASS.

### Parquet layer readers /api/v1/parquet/day

Same 13-request set as the 64a586e1 checkpoint, replayed against 7f2d8f69. Every state,
served_day and row count is identical to the pre-push baseline -- the 930-file readability and
dead-module-deletion refactor changed nothing observable at the wire.

| Layer | Day | HTTP | Latency | state | served_day | Row count | Shape match |
| --- | --- | --- | --- | --- | --- | --- | --- |
| burn-severity | 2026-09-11 | 200 | 3434ms | published | 2026-09-11 | 2 | match incl mtbs_snapshot |
| burn-severity governed-absence day | 2026-08-31 | 200 | 474ms | governed_absence | 2026-08-31 | n/a | match |
| fire-detections | 2026-09-12 | 200 | 984ms | published | 2026-09-12 | 2 | match |
| fire-perimeters | 2026-09-04 | 200 | 810ms | published | 2026-09-04 | 5 | match |
| water-gauges | 2026-09-14 | 200 | 677ms | published | 2026-09-14 | 38 | match |
| weather-observations | 2026-09-07 | 200 | 1062ms | published | 2026-09-07 | 4 | match |
| soil-field-vpd | 2026-09-05 | 200 | 548ms | published | 2026-09-05 | 58 | match |
| drought | 2026-09-08 | 200 | 617ms | published | 2026-09-08 | 2 | match |
| vegetation | 2026-09-07 | 200 | 615ms | published | 2026-09-07 | 0 | match, same zero-row bbox/day as before |
| watersheds | 2026-08-07 | 200 | 681ms | published | 2026-08-07 | 24 | match |
| evacuation-zones | 2026-09-14 | 200 | 694ms | published | 2026-09-14 | 0 | match |
| sensors | 2026-09-09 | 200 | 1223ms | published | 2026-09-09 | 98 | match |
| soil-survey | 2026-08-28 | 200 | 370ms | lane_never_written | n/a | n/a | match, known pre-existing state, RUNBOOK section 0.29.1 |

Water gauges named-day rule. Sample row: observed_day 2026-09-14, observed_at 2026-09-15T06:00:00Z.
served_day from the envelope is 2026-09-14, matching that same rows observed_day and not the naive
truncation of observed_at. PASS, unchanged from the 64a586e1 checkpoint.

### tRPC

GET https://plantgeo.aevani.com/api/trpc/environmental.getSliderCapabilities -> 200
GET https://plantgeo.aevani.com/api/trpc/teams.listMyTeams -> 401
{"error":{"json":{"message":"UNAUTHORIZED","code":-32001,"data":{"code":"UNAUTHORIZED","httpStatus":401,"path":"teams.listMyTeams"}}}}

Clean structured UNAUTHORIZED, teamsRouter still composes into router.ts after the module
deletions in this push. PASS.

## 3. Data quality

### Botanical occurrences query

GET https://plantgeo-parquet-api-production.up.railway.app/api/v1/botanical-occurrences/query
    ?release_set_id=956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4&bbox=-125,42,-111,49&zoom=5
-> 200
counts: {"matched":1711,"returned":500}, cells.length=500, sum(record_count)=2698, truncated=true

Identical to the 64a586e1 checkpoint. PASS.

### Slider capabilities and availability freshness

serverCurrentDate is 2026-09-18, same as the prior checkpoint run about an hour earlier.
climate-field-shortwave-radiation still appears in withheldParquetCapabilities with reason
availability_stale, the known NASA POWER outage, not a regression. No lane went stale or backward.

### Ingestion bbox / coverage indicator (extra probe requested)

Grepped src/app/api for coverage, ingest-bbox, ingestBbox, ingest_bbox and region/manifest.
No dedicated ingestion-bbox or coverage-indicator API route exists. The only bbox-shaped logic in
src/app/api is the botanical-occurrences proxy route's own request-validation bbox schema
(src/app/api/botanical-occurrences/route.ts), which is a query parameter, not a served coverage
indicator. The only region/manifest hit is src/app/api/ai/regional-intelligence/route.ts, an
unrelated AI feature. Per instructions, this probe is skipped -- there is nothing to hit.

## Verdict

PASS -- all four services (plantgeo-main, plantgeo-parquet-api, plantgeo-job-executor,
plantgeo-martin) redeployed cleanly to 7f2d8f69, /api/ready is 200, all 13 layer readers, the
named-day rule, both tRPC probes, and the botanical query are byte-for-byte consistent with the
pre-push baseline. No regression from the 930-file region-manifest and dead-module-deletion
refactor.
