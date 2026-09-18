---
type: evidence
---

# Release checkpoint - commit 5325e600 - 2026-09-18

Commit 5325e600 botanical current-pointer plus Next.js proxy, receipt 1de73dfa over 932 files was
pushed to origin/main at 19:56Z on 2026-09-18. This checkpoint watched all four services to
SUCCESS on that commit, re-ran the standard functional probe set, and added botanical-specific
pointer/tool probes. No local run, no writes to production data -- HTTP GET/POST-query probes only.

## 1. Deploy watch Railway project Aevani

| Service | Deployment ID | Result | Commit landed |
| --- | --- | --- | --- |
| plantgeo-martin | 96cf020f-1a9c-4919-b04f-25f43d6a5cbc | SUCCESS / RUNNING | 5325e600 |
| plantgeo-parquet-api | 128271ef-f778-4099-bb16-b763eb632768 | SUCCESS / RUNNING | 5325e600 |
| plantgeo-job-executor | af8775dc-1fe3-4398-a1d4-f7f0b10445c6 | SUCCESS / RUNNING | 5325e600 |
| plantgeo-main | 8121c0c3-faba-425c-9ecb-fdb4598229e2 | SUCCESS / RUNNING | 5325e600 |

All four services landed cleanly on 5325e600. plantgeo-parquet-api and plantgeo-job-executor
depend on the refreshed quality receipt 1de73dfa for this push (932 files); neither failed, so no
build-log capture was needed for that failure mode.

## 2. Functional probes production

All probes hit plantgeo.aevani.com and plantgeo-parquet-api-production.up.railway.app, both now
on 5325e600.

### /api/ready

GET https://plantgeo.aevani.com/api/ready -> 200
{"status":"ready","checks":{"configuration":true,"database":true,"redis":true},"timestamp":"2026-09-18T20:03:32.535Z"}

PASS.

### Parquet layer readers /api/v1/parquet/day

Same 13-request set as the prior checkpoints, replayed against 5325e600. Every state, served_day
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

## 3. Botanical-specific probes for this push

### 1. Next.js frontend proxy, /api/botanical-occurrences

GET https://plantgeo.aevani.com/api/botanical-occurrences?bbox=-123,47,-122.8,47.2&zoom=8 -> 200
(bbox is 0.04 sq degrees, under the zoom-8 ceiling)

Response is state aggregate with releaseSetId starting 956c0be7, publishedAt
2026-09-13T13:18:49.937501+00:00, 36 cells matched. The pointer object:

pointer object fields: generationId 956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4,
manifestChecksum 6fecc4a911d4ee5b357cc17fd56d29e4898d13e6a553773e23275e14ae704110,
manifestKey botanical-occurrences slash 956c0be7 dots slash manifest.json,
pointerKind legacy_current_json, pointerSchemaVersion 1, pointerWrittenAt null,
publishedAt 2026-09-13T13:18:49.937501+00:00.

pointerKind is legacy_current_json exactly as expected -- the bucket has only current.json until
the owner advances the pointer -- and generationId starts 956c0be7. No 503. PASS.
This is the first checkpoint where the frontend own bbox proxy has been probed directly; it
resolves the pointer server-side via botanical-occurrences-client.ts rather than requiring the
caller to already know release_set_id, matching the route own documented contract at
src/app/api/botanical-occurrences/route.ts.

### 2. parquet-api /current endpoint

GET https://plantgeo-parquet-api-production.up.railway.app/api/v1/botanical-occurrences/current -> 200
{"generation_id": "956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4", "manifest_key": "botanical-occurrences/956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4/manifest.json", "manifest_sha256": "6fecc4a911d4ee5b357cc17fd56d29e4898d13e6a553773e23275e14ae704110", "pointer_kind": "legacy_current_json", "pointer_schema_version": 1, "pointer_written_at": null, "product": "botanical-occurrences", "published_at": "2026-09-13T13:18:49.937501+00:00", "qc_policy_version": "botanical-qc-v1", "release_set_id": "956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4", "state": "current", "taxonomy_recipe_version": "source-names-v1"}

pointer_kind is legacy_current_json, state is current, NOT a 503 pointer_missing. Matches the
frontend proxy pointer block exactly, same generation_id, same manifest_sha256. PASS.

### 3. The four botanical agent tools, POST /api/v1/agent-tools/call

All four tools that belong to this plane answer without a 503, including the new pointer-resolver
tool this push introduces:

- botanical_occurrence_current_release, arguments empty object -> 200, state current,
  release_set_id 956c0be7 dots, pointer_kind legacy_current_json, the tool-surface twin of the
  /current endpoint above.
- botanical_occurrences_in_region, release_set_id 956c0be7 dots, bbox around
  47.0,-122.9/-122.8,47.2 -> 200, state detail, exact.count 1, one Populus trichocarpa
  PreservedSpecimen record, catalog_number V239148.
- botanical_occurrence_spatial_neighbours, same release_set_id, point at the record found above,
  radius_meters 5000 -> 200, state detail, exact.count 1, distance_m 0.0 to itself, correctly
  carried in the substitutes array with substitute true next to, not instead of, the exact result.
- botanical_occurrence_temporal_neighbours, same release_set_id and bbox, window_start
  2013-01-01, window_end 2013-03-01 -> 200, state detail, exact.count 1, the same record reported
  in the overlapping array with overlap true, signed_days 0.

One transient argument-name miss during probing (event_start/event_end is the query-schema
parameter name; the temporal tool own schema is window_start/window_end) surfaced a correct
400 invalid_tool_arguments -- that is validation working as designed, not a service fault, and the
retry with the right argument names answered 200.

No 503 anywhere in this section.

## Verdict

PASS -- all four services (plantgeo-main, plantgeo-parquet-api, plantgeo-job-executor,
plantgeo-martin) redeployed cleanly to 5325e600. The standard probe set is unchanged from the
pre-push baseline. The botanical current-pointer and Next.js proxy work introduced by this push
answer correctly end to end: the frontend proxy, the parquet-api /current endpoint, and all four
agent tools agree on pointer_kind legacy_current_json and release_set_id 956c0be7 dots, with no
503 on any of them.
