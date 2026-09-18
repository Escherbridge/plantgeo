---
type: evidence
---

# Release checkpoint - commit 2271e394 - 2026-09-18

Commit 2271e394 wave 3, per-layer source Protocols plus a boot-time region-binding coverage check
in app.py, stray-literal guard tests, botanical rung-select replacing the bbox refusal,
LayerManager mounts, receipt 02216205 over 950 files was pushed to origin/main at 21:06Z on
2026-09-18. This checkpoint watched all four services to SUCCESS, re-ran the standard probe set,
and added botanical rung-select probes. No local run, no writes to production data -- HTTP
GET/POST-query probes only.

## 1. Deploy watch Railway project Aevani

| Service | Deployment ID | Result | Commit landed |
| --- | --- | --- | --- |
| plantgeo-martin | 50002ebf-dd21-4f80-b102-1345214c49e3 | SUCCESS / RUNNING | 2271e394 |
| plantgeo-parquet-api | a95faa03-2c00-4504-bd03-bc37aef3f57b | SUCCESS / RUNNING | 2271e394 |
| plantgeo-job-executor | aa5fda4c-0067-4bf4-89a4-f0153d506d4e | SUCCESS / RUNNING | 2271e394 |
| plantgeo-main | 62b737e3-d009-4a74-9a17-9c1c9e93bf1c | SUCCESS / RUNNING | 2271e394 |

All four services landed cleanly on 2271e394. plantgeo-parquet-api now runs
assert_region_bindings_are_servable at create_app (the boot-time region-binding coverage check
this push adds); it did NOT crash-loop, reached RUNNING, and served every probe below, so no
RegionBindingNotServableError line needed capturing. plantgeo-parquet-api and plantgeo-job-executor
also depend on the refreshed quality receipt 02216205 for this push (950 files); neither failed.

## 2. Functional probes production

All probes hit plantgeo.aevani.com and plantgeo-parquet-api-production.up.railway.app, both now
on 2271e394.

### /api/ready

GET https://plantgeo.aevani.com/api/ready -> 200
{"status":"ready","checks":{"configuration":true,"database":true,"redis":true},"timestamp":"2026-09-18T21:15:35.969Z"}

PASS.

### Parquet layer readers /api/v1/parquet/day

Same 13-request set as every prior checkpoint, replayed against 2271e394.

| Layer | Day | HTTP | state | served_day | Row count | Shape match |
| --- | --- | --- | --- | --- | --- | --- |
| burn-severity | 2026-09-11 | 503 first attempt, 200 on 3 retries | published | 2026-09-11 | 2 | match, see note below |
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

Note on burn-severity: the first probe immediately after this deploy returned 503 with
{"error":{"code":"read_timed_out","message":"the day read did not finish inside 14s; this is a
serving fault and says nothing about what the warehouse holds"}}. Three immediate retries all
returned 200 in 1.3-1.7s with state published, served_day 2026-09-11, rows 2, truncated false --
identical to every prior checkpoint. This reads as one cold-start-adjacent slow MTBS-snapshot read
(this lane has consistently been the slowest reader in every checkpoint in this series, 3.2-3.4s
even when warm) immediately after redeploy, not a data or contract regression: the shape and
content are unchanged once warm. Flagged here rather than silently retried away.

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

## 3. Drought and burn-severity, sources moved behind shims

Both lanes were re-probed with the same requests used in every checkpoint since 64a586e1:

burn-severity 2026-09-11 -> published, served_day 2026-09-11, rows 2, mtbs_snapshot fields
identical to the 64a586e1 baseline (once warm; see the transient 503 noted above).
drought 2026-09-08 -> published, served_day 2026-09-08, rows 2, identical to the 64a586e1
baseline.

The MTBS shim and USDM shim this push introduces did not change either lane's served answer.
PASS.

## 4. Botanical rung-select probes (this push replaces the flat bbox_too_large_for_zoom refusal
with rung selection)

All three probes hit the Next.js frontend proxy, GET /api/botanical-occurrences.

### 1a. Formerly-refused case: bbox=-130,40,-110,50, zoom=8

Prior to this push (per the RUNBOOK botanical handoff and the serving-contract doc), this exact
bbox/zoom combination was refused with bbox_too_large_for_zoom. Now:

GET https://plantgeo.aevani.com/api/botanical-occurrences?bbox=-130,40,-110,50&zoom=8 -> 200
state aggregate, servingRung grid-0.25, supportId grid-0.25, counts matched 2289 returned 500

200 with servingRung grid-0.25 exactly as expected -- the coarser rung now answers a bbox that
used to hit the fine-rung ceiling. PASS.

### 1b. Small bbox at zoom 12 -> detail

GET https://plantgeo.aevani.com/api/botanical-occurrences?bbox=-122.9,47,-122.85,47.05&zoom=12
-> 200
state detail, servingRung detail, counts matched 1 returned 1

200 with servingRung detail exactly as expected. PASS.

### 1c. Huge bbox -170,10,-50,72 -> still refused

GET https://plantgeo.aevani.com/api/botanical-occurrences?bbox=-170,10,-50,72&zoom=8 -> 400
{"error":"Invalid botanical-occurrences query","reason":"bbox_too_large_for_zoom","detail":"no published rung answers a bbox wider than 1600 square degrees"}

Still the refusal error shape at the true extreme -- rung selection widened the servable band, it
did not remove the ceiling. PASS.

### 2. servingRung present on every answer

Checked all three success-path proxy answers from this checkpoint (the wide bbox above, the small
zoom-12 bbox above, and the standard baseline bbox=-123,47,-122.8,47.2&zoom=8 used in every prior
checkpoint): every one carries a top-level servingRung field (grid-0.25, detail, and grid-0.05
respectively). The one 400 refusal case has its own distinct error/reason/detail shape and is not
an "answer" under the client contract, so it correctly does not carry servingRung. No
contract_mismatch risk observed. PASS.

## Verdict

PASS -- all four services (plantgeo-main, plantgeo-parquet-api, plantgeo-job-executor,
plantgeo-martin) redeployed cleanly to 2271e394. plantgeo-parquet-api boot-time region-binding
check did not crash-loop. The standard probe set is unchanged from the pre-push baseline aside
from one transient burn-severity 503/read_timed_out on the very first post-deploy request, which
resolved on immediate retry with an identical shape and is read as a cold-start-adjacent slow read
rather than a contract or data regression. drought and burn-severity are unchanged despite their
sources moving behind shims. The botanical rung-select replacement of the flat bbox ceiling works
exactly as specified: the formerly-refused wide bbox now answers 200 with servingRung grid-0.25, a
small zoom-12 bbox answers servingRung detail, a genuinely huge bbox still refuses with
bbox_too_large_for_zoom, and every success-path answer carries servingRung.
