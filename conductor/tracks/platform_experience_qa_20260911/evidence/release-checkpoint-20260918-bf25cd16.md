---
type: evidence
---

# Release checkpoint - commit bf25cd16 - 2026-09-18

Commit bf25cd16 wave 6, land-context added to the platform layer vocabulary as an explicit unbound
row, one layerBindingInRegion rule, typed SourceRegistry with isinstance conformance at boot,
drought_intensity_class rename in the payload protocol, NDVI promoter waiting_for_writer, receipt
00867d06 over 956 files was pushed to origin/main at 23:50Z on 2026-09-18. This checkpoint watched
all four services to SUCCESS, re-ran the standard probe set, and added the layer_bindings,
drought-shape, land-context and job-executor-log probes this push specifically calls for. No local
run, no writes to production data -- HTTP GET/POST-query probes and read-only deploy log capture
only.

## 1. Deploy watch Railway project Aevani

| Service | Deployment ID | Result | Commit landed |
| --- | --- | --- | --- |
| plantgeo-martin | 148c34d2-a21e-4abf-b51d-116c8bb2cf4d | SUCCESS / RUNNING | bf25cd16 |
| plantgeo-parquet-api | 7d7951b4-52be-4779-88fe-de5d7fae9460 | SUCCESS / RUNNING | bf25cd16 |
| plantgeo-job-executor | e37d95a5-1068-439c-a4ea-71dd770abb0e | SUCCESS / RUNNING | bf25cd16 |
| plantgeo-main | d3be6897-c427-43c0-834d-8d4946cb9c1e | SUCCESS / RUNNING | bf25cd16 |

All four services landed cleanly on bf25cd16. plantgeo-parquet-api now enforces the stricter
typed SourceRegistry isinstance conformance check at boot; it did NOT crash-loop and reached
RUNNING, so no RegionBindingNotServableError line needed capturing. plantgeo-parquet-api and
plantgeo-job-executor also depend on the refreshed quality receipt 00867d06 for this push (956
files); neither failed.

## 2. Functional probes production

All probes hit plantgeo.aevani.com and plantgeo-parquet-api-production.up.railway.app, both now
on bf25cd16.

### /api/ready

GET https://plantgeo.aevani.com/api/ready -> 200
{"status":"ready","checks":{"configuration":true,"database":true,"redis":true},"timestamp":"2026-09-18T23:56:33.663Z"}

PASS.

### Parquet layer readers /api/v1/parquet/day

Same 13-request set as every prior checkpoint, replayed against bf25cd16. Every state, served_day
and row count is unchanged. No transient errors this run.

| Layer | Day | HTTP | state | served_day | Row count | Shape match |
| --- | --- | --- | --- | --- | --- | --- |
| burn-severity | 2026-09-11 | 200 | published | 2026-09-11 | 2 | match |
| burn-severity governed-absence day | 2026-08-31 | 200 | governed_absence | 2026-08-31 | n/a | match |
| fire-detections | 2026-09-12 | 200 | published | 2026-09-12 | 2 | match |
| fire-perimeters | 2026-09-04 | 200 | published | 2026-09-04 | 5 | match |
| water-gauges | 2026-09-14 | 200 | published | 2026-09-14 | 38 | match |
| weather-observations | 2026-09-07 | 200 | published | 2026-09-07 | 4 | match |
| soil-field-vpd | 2026-09-05 | 200 | published | 2026-09-05 | 58 | match |
| drought | 2026-09-08 | 200 | published | 2026-09-08 | 2 | match, byte-identical, see section 3 |
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

## 3. /api/v1/parquet/coverage layer_bindings now 14 rows, plus drought payload-rename check

GET https://plantgeo-parquet-api-production.up.railway.app/api/v1/parquet/coverage -> 200
coverage_schema_version 3, layer_bindings.length 14 (13 from the prior checkpoint plus land-context).
The new row, quoted verbatim from the wire response:

{"layer":"land-context","binding":"unbound","source":null,"reason":"no_source_bound_in_region"}

Confirmed: land-context appears exactly once, binding is the literal string "unbound", reason is
the literal string "no_source_bound_in_region", source is null. Every other 13 rows are unchanged
from the 0320a745 checkpoint (same layer/binding/source/reason values). PASS.

Drought reader payload-rename check. This push renames a field to drought_intensity_class inside
the Python payload protocol. Diffed the served drought response byte-for-byte against the
0320a745 checkpoint response for the identical request (layer=drought, day=2026-09-08, same bbox):
IDENTICAL. The row-level keys served on the wire remain area_id, valid_date, dm_category,
source_url, ingested_at, geom -- dm_category is still the wire name; no drought_intensity_class
key appears anywhere in the response. The payload-protocol rename is an internal Python-side name
and did NOT reach the served JSON shape. Had the wire key changed this would be reported here as a
REGRESSION; it did not. PASS.

## 4. Land-context tRPC, still typed source_unbound_for_region

GET /api/trpc/landContext.resolveBoundaryInArea, bbox west -123 south 47 east -122.9 north 47.1
-> 200
{"status":"ok","data":[{"coverageState":"source_unbound_for_region", ... "unresolvedGaps":["no Parquet lane named \"land-context-boundaries\" appears in the warehouse coverage census; the land-context reference plane binds no source for it in this region"], ...}]}

Unchanged from the 37963657 and 0320a745 checkpoints: coverageState is still the typed literal
source_unbound_for_region. The new land-context row in /api/v1/parquet/coverage (section 3) and
the tRPC procedure's own refusal now describe the same fact from two different surfaces -- a
Parquet-coverage census view and a per-call region-read view -- and they agree. PASS.

## 5. job-executor logs: promotion lane still shadow, no new startup errors

railway logs -d -s plantgeo-job-executor -n 500 on the new deployment. The startup
plantgeo_job_executor_inventory event still lists vegetation-ndvi-governed-plane-promotion with
active false. The first captured tick (23:51:13Z) still carries that lane_id with state shadow,
run_id null, run_status null -- evaluated, never executed, matching every prior checkpoint in this
series. No waiting_for_writer state string appears in this log tail; the promoter still never
attempts a run so that intermediate state this push adds was not observed exercised, which is
consistent with the lane staying inactive rather than a missing feature.

The only error/exception/CRITICAL-matching line in the tail is the same pre-existing INFO-level
coverage_rollup_probe_deferred entry for climate-field-shortwave-radiation seen in every prior
checkpoint (AvailabilityUnavailableError, the known NASA POWER outage). No CRITICAL lines, no
traceback, no unhandled exception, nothing new. PASS.

## Verdict

PASS -- all four services (plantgeo-main, plantgeo-parquet-api, plantgeo-job-executor,
plantgeo-martin) redeployed cleanly to bf25cd16. plantgeo-parquet-api's stricter typed
SourceRegistry boot check did not crash-loop. The standard probe set is unchanged from the
pre-push baseline. /api/v1/parquet/coverage now carries 14 layer_bindings rows, with land-context
correctly reporting binding "unbound" and reason "no_source_bound_in_region". drought is
byte-identical to the prior checkpoint and the drought_intensity_class payload-protocol rename did
NOT leak into the served wire shape (dm_category unchanged). Land-context tRPC still answers the
typed coverageState source_unbound_for_region, now corroborated by the new coverage-endpoint row.
The vegetation-ndvi-governed-plane-promotion lane remains registered, inactive, evaluated every
tick in state shadow, never executed, with no new startup errors.
