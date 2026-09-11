---
type: evidence
slug: reader-behavior-inventory-20260911
status: local-authoring
---

# Reader and agent behavior inventory — September 11

This packet inventories checked-in readers before the September 11 reader fixes and describes
the local changes. It contains no production trace, deployment, object-store mutation, or release
acceptance. The final integrated check and independent review belong to the owning track packet.

## Product, selected-day and zoom routes

All map readers below resolve one physical rung through `resolveZoomTier`: map zooms 0–<5 use
z0, 5–<9 use z5, 9–<13 use z9, and >=13 use z13. Nonfinite/negative zooms refuse. Private
row/byte/timeout budgets remain in `parquet-plane-client.ts`; they are not widened here.

| Public procedure / product | Reader operation and day meaning | Spatial and response limits |
| --- | --- | --- |
| `wildfire.getFireDetections` | `getParquetFireDetections` -> day window; named day forces exactly one partition, omitted day permits 1–10 days | Map hook supplies bbox and zoom; route still accepts omitted bbox. Per-day terminal states and upstream truncation are retained. |
| `environmental.getStreamflow` | `getParquetWaterGauges` -> exact named publisher day; omitted date permits yesterday/today with six-hour freshness | Required bbox/zoom; newest observation per gauge/cell. A publisher day is not inferred from UTC timestamp. |
| `wildfire.getWeatherForBbox` | `getParquetWeatherObservations` -> exact named day; omitted date permits yesterday/today with three-hour freshness | Required bbox/zoom; newest observation per coordinate. |
| `environmental.getVegetationIndex` | `getParquetVegetation` -> 30-day trailing window ending on selected day | Required bbox/zoom; newest observation per cell, with every day result retained. |
| `environmental.getDroughtClassification` | `getParquetDrought` -> newest release at/before selected day; carry bound is 14 days at live edge, six days once a newer release proves historical cadence | Bbox optional at route; supplied context/map bbox is propagated. One additional latest-release check may be made. |
| `environmental.getEvacuationZones`, `getFirePerimeters`, `getWatershedBoundaries` | Latest release at/before selected day; fire perimeter observations are additionally bounded by selected day | Bbox optional at route; one selected rung; snapshot day stays distinct from observation/release dates. |
| `environmental.getBurnSeverity` | Matching-rung availability enumerates published MTBS releases; <=12 release reads, including a possible current replacement probe | Bbox propagated; complete current replacement suppresses old cohorts; historical gaps, capture extent, row/release budgets retain truncation. |
| `environmental.getSensorStations` | Exact selected day | Bbox optional at route; sensor/cell identity groups own measurements; unlocated rows remain undrawable. |
| `environmental.getSoilField`, `getClimateField` | Exact selected day for declared depth/signal/variant | Required bbox. Climate zoom is required; soil zoom defaults to z13 when omitted. GeoJSON preserves source row/cell caps. |
| `wildfire.getWeatherForPoint` | Named date now reaches exact-day weather; omitted day keeps live freshness | Fixed +/-0.25-degree box clipped to WGS84, z13; nearest sample carries actual distance and publisher day. |

Optional-bbox compatibility routes remain distinct from the required-bbox map-call evidence.
Their existence is not proof that every caller supplies a viewport; production traces must verify
the actual map requests. Static soil-survey/SoilGrids and the legacy `getMetricAtDate` exception
are separate contracts and were not repointed by this slice.

## Confirmed defects corrected

1. Water/weather checked whether the resolved date equalled UTC today rather than whether a date
   was supplied. Explicit today therefore used a live two-day window and freshness filtering.
   Named dates now use exact partition reads; only omitted dates retain live-window semantics.
2. Regional weather discarded a selected today before invoking its point reader. Every supplied
   date now reaches the bbox reader; point-weather also accepts a supplied date at its public route.
3. Climate/soil adapted governed absence, unwritten day/lane, and published-empty results into the
   same unavailable collection. Both now include discriminated `parquet` metadata with terminal
   state, served day and governed-absence evidence. Published-empty results remain published and
   keep upstream truncation. Empty contour geometry does not erase source cells or publication.
4. Weather context dropped the publisher day. It now carries `observedDay`, and regional gauge/
   weather and point-weather return proximity with requested day, observation day, real signed
   day offset, absolute day distance, geodesic metres and exact search bbox. Missing publisher
   dates yield null temporal distance rather than reusing the observation instant's UTC date.
5. Regional HTTP cancellation never reached context assembly. The exact route signal now reaches
   Parquet fire/perimeter/MTBS/gauge/weather/drought readers. Abort results reject instead of becoming
   partial model evidence. Shared capability reads remain shielded. Non-Parquet fanout members
   retain their own bounds, so assembly may still wait for them after the Parquet sockets stop.
6. Context adapters distinguished missing detail rungs, but assembly classified those exceptions
   as generic failures. It now reconciles missing-rung evidence with capability coverage, keeping
   `rung_not_written` distinct where the selected day is otherwise published.

## Existing Python agent neighbour contract — read-only inspection

`agent/tools.py::query_observation_temporal_neighbors` asks `warehouse.lane_evidence` for the
checksummed availability artifact, refuses any unproven lane, intersects published days across
all lanes behind a surface, and returns at most one before and one after within at most +/-180
days. Rows carry their own `observed_day`, signed `day_offset` and absolute `distance_days`.
Governed-absence days are terminal evidence but are not presented as measured neighbours.
`warehouse.fold_availability_index` restricts to selectable days at/below the source ceiling.
There is no historical data-part scan or listing fallback in this capability path.

`query_signal_neighbors_in_time` carries the same own-day/distance semantics plus nearest-cell
metres, with radius capped at 50 km, a +/-180-day search bound, 250-cell admission and 80 output
rows. Its data scan admits only the newest 120 published partitions. Thus a broad query may omit
the whole before side; it reports narrowed/scanned bounds, and missing sides describe those
actual scanned days, not all history. SQL chooses temporal distance first and spatial distance
as the tie-breaker. This is an existing explicit limit, not an all-history nearest proof.

`query_feature_value_near_point` reads the named partition with a maximum 50 km radius and 50
returned features. `features_truncated` is conservative at the cap. Point distances are geodesic;
polygon distances use the centroid and include exact point containment. Polygon candidate
selection is the enclosing bbox rather than a circle and is labelled accordingly. Unsupported
spatial support refuses; no global environmental fallback is introduced.

Remaining PostgreSQL `_fetch` paths inspected are the community intervention feature path,
`signal_coverage_audit` governance ledger, materialized-plane readiness, and gated forecast model
read. No PostgreSQL environmental observation fallback was found in these paths. The static
SoilGrids and regional perimeter-as-latest semantics remain explicitly disclosed limitations.

The Next.js regional advisor does not call the Python temporal-neighbour tool. Its new proximity
metadata describes values already returned; it does not perform additional before/after queries.
Therefore a claim that every regional UI layer has temporal-neighbour retrieval is not established
by this change. The Python feature tool's selected-partition rule for release/snapshot products
also remains distinct from the map's carry-forward/cumulative MTBS rules and needs owner review
before claiming map/agent parity for those products.

## Verification handoff

Regression additions are in `parquet-trpc-readers.test.ts`, `parquet-climate-field.test.ts`,
`parquet-context-readers.test.ts`, `regional-temporal-context.test.ts`,
`wildfire-cancellation.test.ts`, and `regional-intelligence-gates.test.ts`.
No tests were executed in this parallel authoring slice; the root task owns the one integrated
reader/type/lint/boundary test sweep and independent review. Python read-only contract targets
are `test_agent_parquet_reads.py`, `test_agent_parquet_tools.py`, and
`test_agent_signal_time_tools.py`; no Python files were changed here.

## Integrated sweep failure triage

The root's saved `python-readers-20260911.out` reports 260 passing tests and two failures.
Read-only inspection found test-oracle defects rather than a reason to change runtime readers:

- `test_expired_concurrent_callers_share_one_failed_refresh_and_none_receive_stale_evidence`
  used `sleep(0.05)` to imply that four thread-pool tasks were simultaneously inside the cache.
  The saved result made three listing calls instead of two. `CoverageCache.get` shares a failure
  with callers whose nonblocking acquire failed while the refresh was active; a later caller
  correctly retries. The test now holds the failing listing until a proxy around the real lock
  observes all three other callers fail nonblocking acquisition. It retains exact listing-count
  and shared-exception identity assertions, and separately proves a later request retries.
  A five-second event timeout prevents deadlock without asserting wall-clock performance.
- `test_the_window_summary_answers_exactly_what_the_dropped_matview_answered` compared a
  weighted binary64 aggregate with exact equality. Its ten contributing rows produced
  `4.720000000000001` in DuckDB versus `4.72` in the independently ordered Python reference:
  one unit in the last place. Only `mean_value` now permits eight ULPs with zero relative
  tolerance, allowing bounded accumulation-order roundoff. Minimum/maximum values, counts,
  dates and identities remain exact; existing spherical-versus-spheroidal distance tolerance
  remains unchanged. Oracle controls admit one ULP but reject 16 ULPs, changed counts, and even
  one-ULP changes to an unaggregated minimum. No value is rounded in production.

Only the two reader-focused Python tests and their evidence/docs were changed during this
triage. No tests were rerun by the author; the root owns the integrated rerun after review fixes.
These local oracle repairs establish no production capability timing, source publication,
global nearest-neighbour guarantee, or full Python release receipt.

The frontend sweep also exposed one invalid regional MTBS test fixture: it labelled a result
`upstream_unavailable` but omitted the required `fault` object. The real reader maps timeout
errors to `{ fault: { kind: "timeout", message } }`. The fixture now uses that actual contract;
the cancellation guard is unchanged, and this non-abort failure must still reach the advisor
as `read_failed` rather than absence. The separate valid aborted-result regression continues
to require `CLIENT_CLOSED_REQUEST`.

## Python feature temporal-semantics correction after review

The later review identified a local behavior gap in the generic point tool: its exact partition
selection could misreport release/static and rolling map populations as an unwritten selected
day. The reader lane was extended to correct that behavior in `agent/tools.py` and its focused
tests; this supersedes the earlier read-only Python scope and parity limitation statement above.

The bounded correction is an early typed `unsupported_feature_temporal_semantics` refusal for
`burn-severity`, `evacuation-zones`, `fire-perimeters`, `soil-survey`, `watersheds`, and
`vegetation`. Existing pure lane registration governs release/static admission, with vegetation's
trailing 30-day semantics called out separately. The refusal states its requested day and the
required selection semantics and returns no feature list or day-state claim. It makes no object
listing, availability request, data-part scan or database query; no carry implementation or new
historical scan was introduced. This closes a false-absence defect while explicitly withholding
feature-tool capability for these six surfaces. It does not establish map/agent parity for them.

`test_agent_parquet_tools.py` now covers these six surfaces under published, governed-absence and
unwritten selected-partition fixtures, with storage access forbidden. The ordinary daily success
fixture uses weather observations and retains the row's own `observed_day`, `observed_at`, source,
temperature, geodesic distance and selected `served_day`. The existing exact water-gauge test
continues to pin the selected partition. The community intervention reader remains intentional
PostgreSQL; no environmental observation fallback was added.

The review also corrected model-facing wording: exact published-partition coverage does not
promise the map's carried/cumulative/rolling answerability, and a governed absence must be stated
with its recorded reason rather than described as measured zero or no activity. A daily weather
absence fixture retains the upstream failure reason and verifies that warning without scanning
data parts.

No tests were run during this correction pass. Root owns the integrated correction sweep and
independent review. Next.js temporal-neighbour value retrieval is being authored in a separate
reader lane and is not claimed by this Python correction.
