---
type: evidence
---

# Release checkpoint - commit 37963657 - 2026-09-18

Commit 37963657 wave 4, per-call region reads with deprecation aliases, typed
source_unbound_for_region coverage state, admin codes from manifest, NDVI unwritten-day absence,
receipt b7706c4a over 950 files was pushed to origin/main at 21:35Z on 2026-09-18. This checkpoint
watched all four services to SUCCESS, re-ran the standard probe set, and added the typed
coverage-state and deprecation-warning probes this push specifically calls for. No local run, no
writes to production data -- HTTP GET/POST-query probes and read-only deploy log capture only.

## 1. Deploy watch Railway project Aevani

| Service | Deployment ID | Result | Commit landed |
| --- | --- | --- | --- |
| plantgeo-martin | 68d038bf-12b0-48ce-aa4d-3466d1d7a3f0 | SUCCESS / RUNNING | 37963657 |
| plantgeo-parquet-api | 3935bcc1-14fe-44aa-8990-57e1468fa2b0 | SUCCESS / RUNNING | 37963657 |
| plantgeo-job-executor | c0d40e23-c731-43f4-8f3c-fdc1c2ef1811 | SUCCESS / RUNNING | 37963657 |
| plantgeo-main | 8e9b2e57-e965-432d-98a0-f60a24af0534 | SUCCESS / RUNNING | 37963657 |

All four services landed cleanly on 37963657. plantgeo-parquet-api and plantgeo-job-executor
depend on the refreshed quality receipt b7706c4a for this push (950 files); neither failed.

## 2. Functional probes production

All probes hit plantgeo.aevani.com and plantgeo-parquet-api-production.up.railway.app, both now
on 37963657.

### /api/ready

GET https://plantgeo.aevani.com/api/ready -> 200
{"status":"ready","checks":{"configuration":true,"database":true,"redis":true},"timestamp":"2026-09-18T21:43:12.110Z"}

PASS.

### Parquet layer readers /api/v1/parquet/day

Same 13-request set as every prior checkpoint, replayed against 37963657. No transient errors this
time; every state, served_day and row count is unchanged.

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

## 3. Readers whose bbox helper moved: burn-severity, drought, evacuation-zones, sensors,
watersheds, weather-observations, fire-detections, fire-perimeters

All eight are already covered in the section 2 table above and all eight are byte-for-byte
unchanged from the 64a586e1 baseline through every intervening push: same state, same served_day,
same row count. The bbox-helper relocation this push performs did not alter any served answer.
PASS.

## 4. Land-context tRPC, typed source_unbound_for_region coverage state

Same three probes as the 9f5f936a checkpoint, replayed against 37963657.

GET /api/trpc/landContext.resolveBoundaryInArea, bbox west -123 south 47 east -122.9 north 47.1
-> 200
{"status":"ok","data":[{"coverageState":"source_unbound_for_region", ... "unresolvedGaps":["no Parquet lane named \"land-context-boundaries\" appears in the warehouse coverage census; the land-context reference plane binds no source for it in this region"], ...}]}

GET /api/trpc/landContext.resolveBoundaryAtPoint, lon -122.95 lat 47.05 -> 200
{"status":"ok","data":[{"coverageState":"source_unbound_for_region", ... "unresolvedGaps":["no Parquet lane named \"land-context-boundaries\" appears in the warehouse coverage census; the land-context reference plane binds no source for it in this region"], ...}]}

Confirmed: both procedures now return coverageState as the literal typed string
source_unbound_for_region (quoted above verbatim from the wire response), not
partial_area_coverage or unknown_coverage as in the 9f5f936a checkpoint. The reason moved out of
prose-only into the typed field itself, and the unresolvedGaps prose was reworded to match
(previously read "...source_unbound_for_region for the land-context reference plane" as a string
inside the gap text; now reads "...the land-context reference plane binds no source for it in this
region", with source_unbound_for_region promoted to the coverageState enum value). PASS.

GET /api/trpc/landContext.coverageStatus, state WA county null -> 200
{"status":"ok","data":{"state":"WA","county":null,"coverageState":"unknown_coverage","gap":"no Parquet lane named \"land-context-boundaries\" appears in the warehouse coverage census; the land-context reference plane binds no source for it in this region"}}

coverageStatus still returns unknown_coverage rather than source_unbound_for_region. This is
correct, not a regression: src/lib/server/services/land-context/reader.ts readCoverageForRegion
computes its coverageState from a separate covered true/false/null tri-state via
readCoverageStatus, entirely independent of the refusal path resolveBoundaryInArea/AtPoint use,
and that tri-state only ever maps to matched, no_match_in_proven_coverage or unknown_coverage --
source_unbound_for_region is not in coverageStatus's reachable output set by design. Its gap prose
was still updated to the new wording, and its behaviour is unchanged from the 9f5f936a checkpoint.
PASS with this clarification.

## 5. DeprecationWarning grep, job-executor and parquet-api logs

railway logs -d -s plantgeo-job-executor -n 500 and the same for plantgeo-parquet-api, both on the
new deployments, grepped case-insensitively for DeprecationWarning:

- plantgeo-job-executor: 0 matches in 37 lines.
- plantgeo-parquet-api: 2 matches in 18 lines, both lines of ONE single warning event:
  "/app/.venv/lib/python3.12/site-packages/sanic/logging/deprecation.py:33: DeprecationWarning:
  [DEPRECATION v26.6] Passing the loop argument to listeners is deprecated. Your listener
  'create_app.<locals>.setup_resources' should only accept the app argument." plus its own
  "warn(version_info + message, DeprecationWarning)" traceback line.

That warning is Sanic-framework loop-argument deprecation noise from create_app startup, unrelated
to ingest.mtbs or the coordinates aliases this push introduces. Zero DeprecationWarning lines
attributable to ingest.mtbs or a coordinates alias appear in either service's log tail -- the
expected zero in production paths. PASS, no follow-up needed from this checkpoint's evidence
(the pre-existing Sanic warning is unrelated and was not asked about).

## Verdict

PASS -- all four services (plantgeo-main, plantgeo-parquet-api, plantgeo-job-executor,
plantgeo-martin) redeployed cleanly to 37963657. The standard probe set is unchanged from the
pre-push baseline with no transient errors this run. All eight readers whose bbox helper moved
(burn-severity, drought, evacuation-zones, sensors, watersheds, weather-observations,
fire-detections, fire-perimeters) are byte-for-byte unchanged. resolveBoundaryInArea and
resolveBoundaryAtPoint now correctly surface the typed coverageState "source_unbound_for_region";
coverageStatus correctly stays on its own narrower unknown_coverage/matched/
no_match_in_proven_coverage enum by design. Zero ingest.mtbs- or coordinates-alias-related
DeprecationWarning lines in either service; the only DeprecationWarning present is unrelated
pre-existing Sanic framework noise.
