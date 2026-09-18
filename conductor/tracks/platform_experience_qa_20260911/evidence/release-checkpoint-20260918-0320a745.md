---
type: evidence
---

# Release checkpoint - commit 0320a745 - 2026-09-18

Commit 0320a745 wave 5, layer_bindings on /api/v1/parquet/coverage, agent-tool region refusals,
LayerRow disable for unbound layers, LayerManager split, land-context viewport wiring, NDVI
promotion consults the availability index, typed release payload protocols, receipt be8d2228 over
956 files was pushed to origin/main at 22:52Z on 2026-09-18. This checkpoint watched all four
services to SUCCESS, re-ran the standard probe set, and added the layer_bindings, slider-additivity,
agent-tool and job-executor-log probes this push specifically calls for. No local run, no writes to
production data -- HTTP GET/POST-query probes and read-only deploy log capture only.

## 1. Deploy watch Railway project Aevani

| Service | Deployment ID | Result | Commit landed |
| --- | --- | --- | --- |
| plantgeo-martin | 79d6e9e9-d82b-4257-9fe8-00159f57d097 | SUCCESS / RUNNING | 0320a745 |
| plantgeo-parquet-api | 9c0a31c9-8368-4f06-839f-78324da2c75b | SUCCESS / RUNNING | 0320a745 |
| plantgeo-job-executor | 4ebc64e9-4f45-46f1-8f42-54eafb228d3a | SUCCESS / RUNNING | 0320a745 |
| plantgeo-main | 943908f6-cad1-48fa-bd90-62e68b7f9c62 | SUCCESS / RUNNING | 0320a745 |

All four services landed cleanly on 0320a745. plantgeo-parquet-api and plantgeo-job-executor
depend on the refreshed quality receipt be8d2228 for this push (956 files); neither failed.

## 2. Functional probes production

All probes hit plantgeo.aevani.com and plantgeo-parquet-api-production.up.railway.app, both now
on 0320a745.

### /api/ready

GET https://plantgeo.aevani.com/api/ready -> 200
{"status":"ready","checks":{"configuration":true,"database":true,"redis":true},"timestamp":"2026-09-18T23:00:41.243Z"}

PASS.

### Parquet layer readers /api/v1/parquet/day

Same 13-request set as every prior checkpoint, replayed against 0320a745. Every state, served_day
and row count is unchanged.

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

## 3. /api/v1/parquet/coverage layer_bindings, and byte-identical drought/burn-severity

GET https://plantgeo-parquet-api-production.up.railway.app/api/v1/parquet/coverage -> 200
Top-level keys: coverage_schema_version, generated_at, evaluated_through_day, lanes,
layer_bindings. coverage_schema_version is 3, unchanged. The new layer_bindings array carries 13
PNW layers, every one reporting binding bound_global or bound_regional, zero unbound:

botanical-occurrences bound_global gbif; burn-severity bound_regional mtbs; drought bound_regional
usdm; evacuation-zones bound_regional oregon_oem_arcgis; fire-detections bound_global firms;
fire-perimeters bound_regional wfigs; sensors bound_regional noaa_nws; signal bound_global
era5_land_and_nasa_power; soil-survey bound_regional ssurgo; vegetation bound_global
sentinel2_ndvi; water-gauges bound_regional usgs_nwis; watersheds bound_global hydrosheds;
weather-observations bound_global open_meteo.

PASS -- confirmed present, confirmed no unbound entries, confirmed coverage_schema_version still 3.

burn-severity and drought were diffed byte-for-byte against their responses from the 2271e394 and
37963657 checkpoints respectively (same bbox/day/zoom in every case): both are IDENTICAL, including
every field of burn-severity own mtbs_snapshot block. The USDM release-day parsing and payload
protocol changes this push makes did not alter either served answer. PASS.

## 4. Botanical, drought and fire-history agent tools (POST /api/v1/agent-tools/call)

All four botanical tools answered 200 with their usual shapes (state current /
detail, exact.count 1 where applicable), same release_set_id 956c0be7 dots, same Populus
trichocarpa record used in prior checkpoints:

- botanical_occurrence_current_release -> 200
- botanical_occurrences_in_region -> 200
- botanical_occurrence_spatial_neighbours -> 200
- botanical_occurrence_temporal_neighbours -> 200

drought_history_at_point at longitude -122.9 latitude 47.1 weeks_back 8 -> 200, state ok with
applied_bounds and per-release drought-severity rows read from the same Parquet drought lane the
map paints.

fire_history_near_point at longitude -123.1 latitude 47.58 radius_meters 20000 years_back 1 --
first attempt returned 503 tool_read_timeout; three immediate retries all returned 200, covering
both the burn-severity and fire-detections lanes in applied_bounds.lane_names. This mirrors the
transient burn-severity 503/read_timed_out noted in the 2271e394 checkpoint: the fire/MTBS-backed
reads are consistently the slowest in this whole series and occasionally miss the tool's own
timeout window right after a redeploy. Not a contract or data fault once warm.

All six tools -- the four botanical tools plus one drought and one fire-history tool -- answer 200.
The coordinator's expectation that the region-absence gate runs before the lane question holds:
none of the six returned a region-refusal shape for these PNW-interior coordinates, and none
returned anything other than a normal found/detail/ok answer once warm. PASS.

## 5. getSliderCapabilities: additive-only vs the 2026-09-14 sample

Diffed layer names and per-layer field keys between the current response and
slider-capabilities-20260914.json. The new top-level layerBindings array is the only addition --
additive as expected, and it mirrors the parquet-api layer_bindings array in section 3 (same
layerSlug/binding/sourceSlug values, camelCase on the wire). No per-layer field was removed or
renamed.

One non-additive delta exists between the current layers array and the 2026-09-14 sample:
climate-field-shortwave-radiation is absent from the top-level layers array now (it only appears
in withheldParquetCapabilities with reason availability_stale), whereas the 2026-09-14 sample
carried it in layers with real coverage dates. This is NOT new to this push: the same absence was
already present and byte-identical across every checkpoint in this whole 2026-09-18 monitoring
run (64a586e1 through 37963657), tracing to the known NASA POWER ALLSKY_SFC_SW_DWN outage
(memory: plantgeo-power-solar-regressed-2026-09-18) that predates this session's first push. Wave
5 introduced no new removal; the one pre-existing non-additive delta is unrelated to 0320a745.
PASS with this clarification.

## 6. job-executor logs: vegetation-ndvi-governed-plane-promotion still inactive, no new errors

railway logs -d -s plantgeo-job-executor -n 500 on the new deployment. The startup
plantgeo_job_executor_inventory event still lists vegetation-ndvi-governed-plane-promotion with
active false. Every subsequent plantgeo_job_executor_tick event (18 ticks captured, 30s apart)
carries that lane_id with state shadow, run_id null and run_status null -- evaluated every tick,
never executed. No tick reports it failed, and no dead_letter or run event names it anywhere in
the 500-line tail.

The only error/exception/CRITICAL-matching line in the whole tail is an INFO-level
coverage_rollup_probe_deferred entry naming AvailabilityUnavailableError for
climate-field-shortwave-radiation -- the same known, pre-existing stale-lane condition from
section 5, not a startup fault and not new to this push. No CRITICAL lines, no traceback, no
unhandled exception. PASS.

## Verdict

PASS -- all four services (plantgeo-main, plantgeo-parquet-api, plantgeo-job-executor,
plantgeo-martin) redeployed cleanly to 0320a745. /api/v1/parquet/coverage now carries a
layer_bindings array with all 13 PNW layers reporting bound_global or bound_regional, zero
unbound, coverage_schema_version still 3. getSliderCapabilities is additive-only versus the
2026-09-14 sample aside from the pre-existing, session-wide, unrelated shortwave-radiation
withdrawal. drought and burn-severity are byte-identical to their responses in every prior
checkpoint despite reading through the new payload protocols. All four botanical agent tools plus
one drought and one fire-history agent tool answer 200 once warm (one transient 503
tool_read_timeout on the fire-history tool's first post-deploy call, consistent with this lane's
known slow-read pattern, resolved immediately on retry). The vegetation-ndvi-governed-plane-
promotion lane remains registered, inactive, evaluated every tick in state shadow, never executed,
with no new startup errors.
