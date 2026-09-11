---
type: verification-evidence
track: parquet_reader_cutover_acceptance_20260901
observed_on: 2026-09-11
source_revision: fa202230958fb55521963e886eb031be5fc266c4
status: partial
---

# Current public reader routes and capability operation budget

This packet records bounded read-only production GETs and implementation inspection. It ran no
tests, deployment, service restart, database operation, publication or object-store mutation.
The public origin is `https://plantgeo-main-production.up.railway.app` from `docs/deployment.md`.
The initial sandbox connection failed; the same bounded GET succeeded with network access approved.

The separate [deployment identity receipt](deployment-identities-20260911.json) records both
frontend deployment `4fca7553-c5b0-4358-ba8b-907941be6c45` and Parquet API deployment
`9b65d7b8-40e0-4dac-9b74-1744b2ffd8fc` as SUCCESS at local source revision `fa20223`, observed
18:03:28 UTC. The public readiness answer itself exposed no commit; it reported ready with
configuration, database and Redis checks true at 17:59:17.605 UTC.

## Retained observations

[The machine packet](public-reader-observations-20260911.json) retains the exact request URL,
selected day, bbox, zoom, timestamp, first/repeat label, HTTP status, TTFB, total transfer time,
response bytes, curl result, allowlisted response headers and SHA-256 for each response. Small
response bodies are deduplicated by hash inside the packet. Two large MTBS bodies are retained
losslessly in the checksum-named `.json.gz` files referenced by its
`compressed_response_body_files_by_sha256` map. Hashes address uncompressed response bytes.
TLS certificate dumps and cookies are excluded. The checked-in probe and normalization scripts
document the exact capture and consolidation mechanics.

Neither first observation nor repeat proves a cold or warm cache. No cache was reset, no service
was restarted and no object-store operation instrumentation was attached. These HTTP observations
cannot establish GET/LIST counts, private day-row TTFB, browser request-to-paint, cancellation
completion, or pixel conservation.

The first capability observation returned HTTP 200, TTFB 0.340 s, total 3.031 s, 29,441 bytes;
the repeat returned HTTP 200, TTFB 0.287 s, total 0.384 s, 29,324 bytes. Both reported coverage
available. Their generation timestamps differ, so this is not a controlled cache comparison.
The later response's coverage generation was `2026-09-11T17:56:08.391769Z`.

## Public-to-private route matrix

Public routes use GET `/api/trpc/<procedure>` with a SuperJSON `input` containing `date`, `bbox`
and `zoom`. The main packet probes all twelve families below twice at zoom 0, 9 and 13, over
Colorado bbox `-105.5,39.5,-105.0,40.0`. These are representative coarse/middle/detail reads;
z5 and each climate/soil variant remain outside this runtime packet.

| Product and selected day | Public procedure | Private operation and physical lane |
| --- | --- | --- |
| Fire detections, September 9 | `wildfire.getFireDetections` | `/window`, `fire-detections`; explicit day makes first=last and suppresses live multi-day carry |
| Water gauges, September 11 | `environmental.getStreamflow` | Deployed `fa20223`: `/window` for explicit today, `water-gauges`; candidate correction: explicit day uses `/day`, only omitted day may use a two-day window |
| Weather, September 11 | `wildfire.getWeatherForBbox` | Deployed `fa20223`: `/window` for explicit today, `weather-observations`; candidate correction: explicit day uses `/day`, only omitted day may use a two-day window |
| Vegetation, August 31 | `environmental.getVegetationIndex` | `/window`, `vegetation`, 30-day window ending on selected day |
| Drought, September 11 | `environmental.getDroughtClassification` | `/release`, `drought`, as-of selected day with named served release |
| Burn severity, September 11 | `environmental.getBurnSeverity` | Coverage plus bounded `/release` probes, `burn-severity`, at most 12 release reads |
| Sensors, September 9 | `environmental.getSensorStations` | `/day`, `sensors` |
| Fire perimeters, September 4 | `environmental.getFirePerimeters` | `/release`, `fire-perimeters`, static reference snapshot |
| Evacuation zones, September 10 | `environmental.getEvacuationZones` | `/release`, `evacuation-zones`, static reference snapshot |
| Watersheds, August 7 | `environmental.getWatershedBoundaries` | `/release`, `watersheds`, static reference snapshot |
| Soil moisture/surface, September 2 | `environmental.getSoilField` | `/day`, `soil-field-moisture-0-7cm` |
| Mean air temperature, September 6 | `environmental.getClimateField` | `/day`, `climate-field-air-temperature-mean` |
| Slider catalogue | `environmental.getSliderCapabilities` | `/coverage`, no bbox/day/zoom; includes all registered physical rungs |

The route matrix distinguishes the deployed baseline from the candidate's explicit-today
water/weather correction. Public September 11 probes observed deployed `fa20223`, while service
inspection shared the candidate working tree with its code author. Baseline observation is not
runtime proof of the candidate correction.

The private prefix is `/api/v1/parquet`; its origin is the server-only
`AGRI_PARQUET_SERVICE_URL`, recorded as `http://plantgeo-parquet-api.railway.internal:8080` in
the deployment contract. No public Parquet origin was created. `resolveZoomTier` selects 0 below
5, 5 below 9, 9 below 13, and 13 at/above 13. Private windows cap at 31 days and 120,000 rows;
the TypeScript transport caps row payloads at 16 MiB and coverage at 4 MiB. These bounds do not
turn a refusal or truncation into accepted completeness.

## Runtime findings and limits

- All 72 main-matrix transport responses were HTTP 200. Most envelope readers returned `ready`
  with the explicit selected day and `truncated=false`; empty viewports remain positive evidence
  of an answered partition, not positive-data or rendering proof. September 6 mean temperature
  returned one feature at each sampled rung. Drought returned non-empty release geometry.
- Colorado is outside the current MTBS source snapshot extent. The reader correctly returned
  `truncated=true` beside the full replacement descriptor and its declared bbox
  `[-125,42,-111,49]`; those responses are not complete coverage acceptance.
- Watersheds z13 first observation returned `upstream_unavailable`, fault HTTP 503, after
  14.610 s total. Its repeat answered `ready`, empty, `truncated=false`, after 13.171 s. Both
  public transport statuses were 200. The packet preserves this intermittency.
- Additional source-extent MTBS requests returned 746 rows at z0 and 747 at z9, both
  `truncated=false`. This is an observed count discrepancy requiring the multiscale owner's
  conservation review; different supports/clipping may explain it. It is not established data loss.
  Full-extent z13 returned explicit payload refusal, `Upstream response exceeded the byte limit`.
  Curl completed normally; this is the application's upstream bound, not the probe's download cap.
  One additional Boise z13 request returned explicit `upstream_unavailable` HTTP 503. These two
  bounded detail observations do not establish that every z13 viewport is unservable.
- Additional Boise bbox `-116.5,43.3,-115.8,43.9` z9 requests returned four water rows and nine
  soil moisture features, identically in first/repeat samples. Weather answered an empty selected
  day. Historical August 6 mean temperature over Boise was empty at all three sampled rungs;
  that sample does not prove historical positive-data serving.
- Current public capability authority is `availability` for time-bearing products and `census`
  for fire-perimeters, evacuation-zones and watersheds. Soil-survey is withheld
  `lane_never_written`. Drought advertises September 11 read-through with September 3 source
  ceiling; the bounded release carry deliberately distinguishes these edges. Shortwave radiation
  advertises latest May 31 with June 24 source ceiling. Soil's latest/ceiling is September 2,
  and most climate families' latest/ceiling is September 6.

## Capability operation budgets from current implementation

Pure registry inspection at this source revision reports 30 registered lanes: 26 time-bearing
lanes, four static lookups (evacuation-zones, fire-perimeters, soil-survey, watersheds), and no
live `SNAPSHOT_PRODUCTS`. This supersedes older comments about 18 census registrations or
snapshot monthly serving reads.

| Path | Current operation budget | Evidence boundary |
| --- | --- | --- |
| Python cold build, matching rollup entries for every time-bearing lane | One rollup GET + 26 uncached pointer GETs; zero generation GETs for rollup hits | Pointer-document digest validates each rollup entry; no time-bearing LIST/data-part reads |
| Python cold build, absent/empty rollup, valid indexes | One rollup attempt + 26 pointer GETs + 26 bounded generation GETs | Index checksum and semantic validation; no time-bearing LIST/data-part reads |
| Stale rollup entry | Pointer probe then full per-lane fallback | May add a second pointer GET; must not advertise the ideal-hit count |
| Python warm payload within 120 seconds | No coverage rebuild I/O | `_CoveragePayloadCache`; concurrent cold callers share one builder |
| Full-index reuse within 60 seconds | Reuse held verified index | `AvailabilityCoverageReader`; generation bytes at most 8 MiB retained per key, 32-key cap |
| Full-index rebuild, same small generation key | Pointer GET; generation bytes reusable | Generation still revalidated semantically; oversized generations are not retained |
| TypeScript same-day cache within 300 seconds | No private coverage request | `getParquetWarehouseCoverage`; stale same-day reads may be returned during revalidation |
| Static lookups under either authority policy | Explicit census work | Normally one logical root listing per static lane; pagination can add physical LIST calls |

These are code-derived budgets, not current production operation counts. The exact gate-9
sentence “one pointer plus one generation GET per lane” predates the rollup optimization.
Zero historical time-bearing LIST/data-part reads remains the correct invariant. Whole-catalogue
zero LIST is not an implemented invariant because static lookup census is intentionally retained.
The transitional `census_until_bootstrap` setting also still exists in code: missing unbootstrapped
time-bearing indexes can census under that setting; malformed, corrupt or lost bootstrapped
indexes withhold. Public authority fields prove the answer's declared authority, not the current
private setting or Boto operation count. A separate CLI configuration read retained in the
deployment identity receipt reports configured `PARQUET_COVERAGE_AUTHORITY=availability`;
configured state remains distinct from an in-process request trace.

## Focused tests for the final integrated sweep

No test was executed by this evidence lane. Root's single integrated sweep can use the original
installed Python runtime with current service cwd and `PYTHONPATH=src`, explicitly removing the
four database-test environment variables described in `docs/testing.md`.

- `tests/parquet_ops/test_availability_coverage.py`: ExplodingListing no-LIST tripwire, all-rung
  intersection, source ceiling, missing/corrupt/stale index withholding, missing bootstrap
  distinction, pointer TTL, immutable-byte cache and oversized generation behavior.
- `tests/parquet_ops/test_coverage_rollup.py`: matching rollup uses one pointer probe and no full
  index read; missing/stale/corrupt rollup fallback and equivalence to full index answers.
- `tests/interface/test_parquet_routes.py`: real HTTP builder no-LIST path, coverage single-flight,
  four-state routes, route scope/parameter validation and typed refusal transport mapping.
- `tests/parquet_ops/test_coverage_census.py`, `test_parquet_envelopes.py`,
  `test_duckdb_admission.py`, `test_wire_agreement.py`: explicit static census, bounded envelopes,
  worker admission/cancellation ownership and frozen wire agreement.
- `tests/test_agent_parquet_tools.py`: selected-day states, bounded windows, explicit refuses,
  `test_temporal_neighbours_carry_the_real_gap_each_side`, and retired SQL tripwires.
- `tests/test_agent_parquet_reads.py`: real local DuckDB selected-day/spatial behavior,
  `test_the_neighbour_statement_returns_one_row_per_side_with_its_real_gap`,
  `test_a_radius_smaller_than_every_cell_answers_nothing_rather_than_widening`, and read-only SQL.

The TTL and rollup tests count their configured reader/storage seams; a passing unit result is
not a live Boto trace. Existing operator tool `scripts/coverage_rollup_status.py --json` is GET-only
without `--rebuild`: it probes one rollup and one pointer per lane. It establishes current rollup
freshness, not an HTTP cache trace. Never add `--rebuild` to this read-only evidence request.

## Safe replay commands

From repository root, choose new output directories for each run. The probe also defaults to a
new timestamped directory and refuses any existing output directory. The historical selected days
and bboxes are intentional; obtain fresh deployment identity before associating a replay with a
release. Replays are first/repeated observations, never controlled cold/warm measurements.

```powershell
$receipt = 'conductor/tracks/parquet_reader_cutover_acceptance_20260901/evidence'
$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
& "$receipt/public-reader-probe-20260911.ps1" -OutputDirectory "$receipt/replay-$stamp-main"
python "$receipt/normalize-public-reader-probes-20260911.py" --input-directory "$receipt/replay-$stamp-main"
& "$receipt/public-reader-probe-20260911.ps1" -Supplemental -OutputDirectory "$receipt/replay-$stamp-supplemental"
python "$receipt/normalize-public-reader-probes-20260911.py" --input-directory "$receipt/replay-$stamp-supplemental"
```

The main replay is 72 GETs; the supplemental replay is 12. The final Boise MTBS z13 GET is an
additional one-off observation fully specified by its URL in the retained packet. Capability and
readiness observations are also retained there and are not replayed by these row scripts.

Remaining owner evidence is controlled cold/warm private operation instrumentation, the missing
product/variant/rung and historical-positive samples, browser selected/painted day and neighbour
interaction, request-to-paint and cancellation, and conservation review. Any runtime configuration
change, restart, deployment or publication remains outside this read-only packet. Rollback must
name a verified database-compatible deployment or reverse only this branch's reader changes;
the historical `2b4cfef..HEAD` range predates retirement and is not current rollback authority.
