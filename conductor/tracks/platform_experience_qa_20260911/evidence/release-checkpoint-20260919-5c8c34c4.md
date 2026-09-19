---
type: evidence
---

# Release checkpoint - commit 5c8c34c4 - 2026-09-19

Commit 5c8c34c4, the final push of this monitoring run, carries three commits pushed to
origin/main by 00:49Z on 2026-09-19: 1986300a (wave-7 agri fixes: registry-keyed boot refusal so a
drought-to-mtbs source mismatch is refused, NDVI promoter re-read rules), b79101c6 (a DIFFERENT
session's hard cut: method/ml and method/monte_carlo removed from agri-data-service into a new
services/plantgeo-ml-service -- that new Railway service does not exist yet and is out of scope
for this checkpoint), and 5c8c34c4 itself (web slug guard). Receipt bcf5e713 over 854 files. This
checkpoint watched the four EXISTING services to SUCCESS, re-ran the standard probe set, and
verified the method/ removal did not break plantgeo-parquet-api or plantgeo-job-executor. No local
run, no writes to production data -- HTTP GET/POST-query probes and read-only deploy log capture
only.

## 1. Deploy watch Railway project Aevani

| Service | Deployment ID | Result | Commit landed |
| --- | --- | --- | --- |
| plantgeo-martin | d75608e3-ed11-4818-b1ca-dea9f2a81df7 | SUCCESS / RUNNING | 5c8c34c4 |
| plantgeo-parquet-api | 3e266966-746e-46df-ae77-53a09331a03e | SUCCESS / RUNNING | 5c8c34c4 |
| plantgeo-job-executor | d93918c5-d528-405d-a979-2289314e4d79 | SUCCESS / RUNNING | 5c8c34c4 |
| plantgeo-main | 632fea65-c0f8-4d1f-9e83-b2ebab8a29ba | SUCCESS / RUNNING | 5c8c34c4 |

All four services landed cleanly on 5c8c34c4. plantgeo-parquet-api and plantgeo-job-executor now
build their images without method/ (method/ml and method/monte_carlo were removed in b79101c6);
neither failed, so no build-log capture was needed for that failure mode. Both also depend on the
refreshed quality receipt bcf5e713 for this push (854 files).

## 2. Functional probes production

All probes hit plantgeo.aevani.com and plantgeo-parquet-api-production.up.railway.app, both now
on 5c8c34c4.

### /api/ready

GET https://plantgeo.aevani.com/api/ready -> 200
{"status":"ready","checks":{"configuration":true,"database":true,"redis":true},"timestamp":"2026-09-19T00:55:42.733Z"}

PASS.

### Parquet layer readers /api/v1/parquet/day

Same 13-request set as every prior checkpoint, replayed against 5c8c34c4. Every state, served_day
and row count is unchanged. No transient errors this run.

| Layer | Day | HTTP | state | served_day | Row count | Shape match |
| --- | --- | --- | --- | --- | --- | --- |
| burn-severity | 2026-09-11 | 200 | published | 2026-09-11 | 2 | match, byte-identical, see section 3 |
| burn-severity governed-absence day | 2026-08-31 | 200 | governed_absence | 2026-08-31 | n/a | match |
| fire-detections | 2026-09-12 | 200 | published | 2026-09-12 | 2 | match |
| fire-perimeters | 2026-09-04 | 200 | published | 2026-09-04 | 5 | match |
| water-gauges | 2026-09-14 | 200 | published | 2026-09-14 | 38 | match |
| weather-observations | 2026-09-07 | 200 | published | 2026-09-07 | 4 | match |
| soil-field-vpd | 2026-09-05 | 200 | published | 2026-09-05 | 58 | match |
| drought | 2026-09-08 | 200 | published | 2026-09-08 | 2 | match, byte-identical, see section 3 |
| vegetation | 2026-09-07 | 200 | published | 2026-09-07 | 0 | match, same zero-row bbox/day as before; vegetation reader unaffected by the Monte Carlo removal |
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

## 3. /api/v1/parquet/coverage still 14 rows, drought and burn-severity byte-identical

GET https://plantgeo-parquet-api-production.up.railway.app/api/v1/parquet/coverage -> 200
coverage_schema_version 3, layer_bindings.length 14, unchanged from the bf25cd16 checkpoint. The
land-context row, quoted verbatim:

{"layer":"land-context","binding":"unbound","source":null,"reason":"no_source_bound_in_region"}

drought and burn-severity were diffed byte-for-byte against their bf25cd16-checkpoint responses
for the identical requests (layer=drought day=2026-09-08, layer=burn-severity day=2026-09-11, same
bbox): both IDENTICAL, including burn-severity own mtbs_snapshot block. Wave-7's registry-keyed
boot refusal (which refuses a drought-to-mtbs source mismatch) and the method/ removal did not
alter either served answer. PASS.

## 4. Botanical agent tools and vegetation reader, Monte Carlo removal check

All four botanical agent tools (POST /api/v1/agent-tools/call) still answer 200:
botanical_occurrence_current_release, botanical_occurrences_in_region,
botanical_occurrence_spatial_neighbours, botanical_occurrence_temporal_neighbours -- same
release_set_id 956c0be7 dots used throughout this series.

The vegetation Parquet reader (section 2 table) still answers 200, state published, served_day
2026-09-07, 0 rows for the standard probe bbox/day -- identical to every prior checkpoint. Monte
Carlo (method/monte_carlo, used by fire_detections.py per the codebase) and the ML methods
(method/ml, used by the recommendation/analog-ensemble/conformal-calibration models) left
agri-data-service entirely in b79101c6, moving to a not-yet-deployed plantgeo-ml-service. No
served endpoint in this checkpoint's probe set depends on either module: the fire-detections
Parquet reader answered 200 with its usual 2 rows (section 2), and no botanical, drought,
burn-severity, or any other probed endpoint touches recommendation/ensemble/calibration code.
Confirmed: no served endpoint regressed from the Monte Carlo / ML extraction. PASS.

## 5. job-executor startup log: no ImportError, promotion lane still inactive, forecast-module check

railway logs -d -s plantgeo-job-executor -n 500 on the new deployment (36 lines returned).
Case-insensitive grep for ImportError, ModuleNotFoundError, agri_data_service.method, and "no
module named": ZERO matches. The method/ removal did not break the job-executor image or its
startup import graph.

The startup plantgeo_job_executor_inventory event still lists vegetation-ndvi-governed-plane-
promotion with active false, identical field-for-field to every prior checkpoint in this series.

Grepped case-insensitively for "forecast" across the full log tail: ZERO matches. No lane in this
startup window logged a warning about a missing or relocated forecast module -- either no lane in
the active registry references agri_data_service.method.ml's forecast code at startup, or any such
reference is lazy-imported only at run time and this window captured no run of that lane. Nothing
in this log tail contradicts a clean removal.

The only error/exception/CRITICAL-matching line in the tail is the same pre-existing INFO-level
coverage_rollup_probe_deferred entry for climate-field-shortwave-radiation seen in every checkpoint
this session (AvailabilityUnavailableError, the known NASA POWER outage). No CRITICAL lines, no
traceback, no unhandled exception. plantgeo-parquet-api's own log tail (19 lines) was also grepped
for the same ImportError/ModuleNotFoundError/method patterns: zero matches. PASS.

## Verdict

PASS -- all four existing services (plantgeo-main, plantgeo-parquet-api, plantgeo-job-executor,
plantgeo-martin) redeployed cleanly to 5c8c34c4. The method/ml and method/monte_carlo removal
(b79101c6, a different session's hard cut into the not-yet-deployed plantgeo-ml-service) did not
break either Python image: zero ImportError/ModuleNotFoundError in either service's startup log,
zero forecast-module warnings, and every probed endpoint -- including the fire-detections and
vegetation readers, drought and burn-severity (byte-identical to the prior checkpoint), the
14-row /api/v1/parquet/coverage layer_bindings with land-context still unbound, and all four
botanical agent tools -- answers correctly. The vegetation-ndvi-governed-plane-promotion lane
remains registered and inactive. This is the last watch of the 2026-09-18/19 monitoring run; no
regression was introduced by this final push.
