---
type: evidence
---

# Active executor database boundary — 2026-09-10

Repository-only audit against the active lane names read from Railway configuration by the parent
task. No database access or configuration mutation was used for this audit. The user requires
no local database for this task and no further environmental ingestion into PostgreSQL.
This does not imply that operational scheduler metadata must stop using PostgreSQL.

## Environmental PostgreSQL writes in the supplied active set

| Executor lane | Code path and destination |
| --- | --- |
| `jobs-firms-archive` | `ingest/commands.py` binds `FeatureWriter` into `ArchiveWalkContext`; `archive_walk.py` calls `run_source_backfill`; `ingest/writer.py` writes `geo.features`. |
| `jobs-streamflow-archive` | Same archive worker path, for historical USGS observations. |
| `mtbs-forward` | `data ingest-mtbs` persists MTBS records to `geo.features` and maintains geometry history; see `ingest/mtbs.py`. |
| `vegetation-catch-up` | `catch_up_vegetation_publication` calls `_prepare_forward` and `register_governed_forward_plane`, promoting environmental observations/series/cells/source releases before export. It is not merely queue acknowledgement. |
| `jobs-matview-refresh` | Derived environmental/forecast PostgreSQL writes: refreshes `geo.mv_layer_feature_stats`, `geo.mv_layer_hourly_activity`, `geo.mv_drought_release_index`, `geo.mv_feature_observation_day`, `geo.mv_drought_observation_day`, `geo.watershed_rollup`, `agri.mv_forecast_ml_daily_serving`; also writes operational refresh state. |
| `jobs-strategy-mv-refresh` | Derived PostgreSQL writes: refreshes `geo.mv_strategy_recommendations_{coarse,regional,detail}`; also writes operational refresh state. |

The four archive maintenance lanes author/reconcile work for the PostgreSQL ingestion path and
should be paused with it: `maintenance-firms-archive-plan-gaps`,
`maintenance-firms-archive-reconcile`, `maintenance-streamflow-archive-plan-gaps`, and
`maintenance-streamflow-archive-reconcile`. Their writes are operational, but their purpose
is obsolete once the corresponding PostgreSQL source ingesters stop.

## Active exporters still reading environmental PostgreSQL

| Executor lane | Remaining dependency / migration limit |
| --- | --- |
| `parquet-fire-detections` | Historical PostgreSQL FIRMS exporter through 2026-08-24; direct writer starts 2026-08-25. |
| `parquet-water-gauges` | Historical PostgreSQL gauge exporter through 2026-09-01; direct writer starts 2026-09-02. |
| `parquet-fire-perimeters` | Exports `geo.features`; its source watermark also reads `geo.geometry`. |
| `parquet-signal` | Reads `agri.signal_observation`, `spatial_cell`, `source_release`, and `data_source`. |
| `parquet-soil-survey` | Reads the SSURGO geometry/data path and PostgreSQL source watermark; layer remains dark. |
| `vegetation-catch-up` | Reads raw and governed PostgreSQL vegetation, and can promote it before exporting. |

`maintenance-validate-streams` requires a separate retired-reader decision; it is not a source
fetch/ingestion lane. `parquet-calendar` creates its calendar without an environmental DB source.
Direct provider-to-Parquet writers must remain distinguishable from these legacy exporters.
Their scheduler definitions/runs/attempts/leases and publication coordination are operational
PostgreSQL use, not environmental ingestion.

## Configuration discrepancy

`postgres-fire-perimeters`, `postgres-geometry-repair`, and `soilgrids-cache-warm` remain registered
but are not in the supplied live active set. Registry prose claiming the perimeter PostgreSQL
producer is active is stale relative to that configuration. Do not reactivate it to feed the
remaining exporter. No claim is made about other HTTP/manual writers outside this executor audit.

## Deliberate pause and replacement sequence

1. Remove the four source/promotion writers above from `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES`,
   together with their four archive maintenance lanes. Retire the two materialized-view lanes
   when stopping derived environmental PostgreSQL computation. Record the prior/next active
   lists and the operator decision; no blanket database-write ban is inferred.
2. Wait for old executor processes/fenced leases to end. Do not requeue or supersede their failed
   ingestion work merely to turn scheduler status green.
3. Preserve direct Parquet producers. Missing historical coverage remains explicit until bounded
   direct-to-Parquet archive acquisition replaces the retired database-staging path.
4. Use remaining PostgreSQL exporters only for explicitly authorized, bounded migration of frozen
   data, then stop their recurring schedules. Do not resume environmental PostgreSQL ingestion.
5. Preserve existing database/R2 data. Drops require the retirement specification's coverage proof,
   reader removal, and archived snapshot packet; pausing a schedule is not permission to drop data.

Replacement gaps: FIRMS and water history need direct archive writers; perimeter direct publication
still refuses invalid source geometry; signal needs a bounded historical Parquet migration or
explicit retirement; soil survey needs its missing migration/low-zoom representation; vegetation
needs discharge or replacement of its frozen historical promotion/export dependency. The current
Parquet point/UI work does not itself close those producer/history gaps.

## Cutoff scope refinement

The planned first cutoff pauses the four environmental source/promotion writers and four associated
archive planners/reconcilers. The two materialized-view refresh jobs remain in that prepared list.
The earlier consumer audit found `forecast_summary_for_cell` reading
`agri.mv_forecast_ml_daily_serving` and regional strategy AI context reading
`geo.mv_strategy_recommendations_regional`. Commit `6e2a112` subsequently removed the regional
strategy context consumer; retaining the strategy refresh job must not be justified by that removed
reader. Forecast refresh still has its separate consumer. Community demand/vote operations use
operational tables independently of those views.

A subsequent repository-only strategy audit found no connected runtime reader of the three strategy
views. `jobs/strategy_mv_refresh.py` remains their refresh writer; the TypeScript materialized-view
references are tests or comments. The SQL reader `geo.strategy_recommendations_tiles()` still exists
in `drizzle/0000_baseline.sql` and selects all three tiers, but `infra/martin/martin.yaml` sets
`auto_publish: false` and does not register it. `src/components/map/layers/StrategyLayer.tsx` returns
when that source is absent; `src/lib/map/sources.ts` does not declare it. The unused `strategySource`
export in `src/lib/map/layers/strategy-layer.ts` names a strategy tile route that is not implemented
under `src/app/api/tiles`. These dormant definitions are not evidence of an active consumer.

This is repository evidence, not a live zero-reader certificate: deployed Martin overrides, older
app processes, external SQL consumers, and database dependencies were not queried. The prepared
eight-lane cutoff remains unchanged pending independent review and full zero-reader proof. Adding
`jobs-strategy-mv-refresh` would be a separately reviewed ninth pause once that proof is accepted;
pausing it would preserve the existing materialized views, not authorize dropping them.

Direct climate, soil, and vegetation writers publish environmental observations directly into Parquet, but still
read their fixed support definitions from `agri.spatial_cell`. That small dimension read is a remaining
migration dependency, distinct from the avoided historical observation export and from operational
publication locks. A future bucket-pinned support artifact must preserve exact IDs, coordinates,
coverage fractions, order, and grid validation before that lookup can be removed.


## Scheduled gap repair scope

The surviving direct schedules do not establish complete historical gap repair. Their executable
selection windows are bounded as follows (source paths are relative to
`services/agri-data-service/src/agri_data_service/`):

| Direct producer | Scheduled repair scope | Source |
| --- | --- | --- |
| Climate NASA POWER | Latest 400 days, clipped to product history floor; reconsider governed absences within 14 days. | `pipeline/direct/climate/forward.py`: `CLIMATE_BACKLOG_SCAN_DAYS`, `CLIMATE_ABSENCE_RECHECK_DAYS`, `_publish_product`. |
| Soil ERA5-Land | Latest 400 days, clipped to product history floor; reconsider governed absences within 14 days. | `pipeline/direct/soil/forward.py`: `SOIL_BACKLOG_SCAN_DAYS`, `SOIL_ABSENCE_RECHECK_DAYS`, `_publish_product`. |
| Vegetation Sentinel-2 | Latest 400 days, clipped to the direct ownership floor; reconsider governed absences within 14 days. Historical backfill and parity remain manual and retain the PostgreSQL adapter. | `pipeline/direct/vegetation/forward.py`: `VEGETATION_BACKLOG_SCAN_DAYS`, `VEGETATION_ABSENCE_RECHECK_DAYS`, `history_floor`; `execution/job_executor_service.py`: `VEGETATION_DIRECT_LANE_ID` specification. |
| Drought | Latest 60 settled release weeks; reconsider absences within 8 weeks. | `pipeline/direct/drought/forward.py`: `DROUGHT_BACKLOG_SCAN_WEEKS`, `DROUGHT_ABSENCE_RECHECK_WEEKS`. |
| Burn severity | Whole explicitly governed release set, with bounded pending releases per turn. | `pipeline/direct/burn_severity/forward.py`: `governed_release_days()` census. |
| Fire detections | Five settled days; deeper source archive gaps are outside this scheduled window. | `pipeline/direct/fire_detections.py`: `FIRE_DIRECT_DEFAULT_LOOKBACK_DAYS`, `FIRE_DIRECT_MAX_LOOKBACK_DAYS`. |
| Sensors | Rolling NWS retention, at most seven day buckets; missed ticks can recover only while observations remain available upstream. Expired history and false-absence corrections are separate work. | `execution/job_executor_service.py`: `SENSORS_DIRECT_LANE_ID` specification; `pipeline/direct/sensors/forward.py`. |
| Water gauges and weather observations | Current source poll merged into publisher-day buckets; no scheduled historical gap-authoring path. | `pipeline/parquet/water_gauges_forward.py`: `fetch_streamflow_gauges` call and `_owned_publisher_tables`; `execution/job_executor_service.py`: `WEATHER_OBSERVATIONS_DIRECT_LANE_ID` specification. |
| Watersheds and evacuation zones | Current source-version polling; cannot reconstruct uncaptured source versions between polls. | `execution/job_executor_service.py`: `WATERSHEDS_DIRECT_LANE_ID` and `EVACUATION_ZONES_DIRECT_LANE_ID` specifications. |

The retained generic `parquet-*` jobs do run `parquet-gap-fill` (see
`execution/job_executor_service.py::_parquet_spec`), but the historical environmental exporters
listed above still read frozen PostgreSQL data. Their schedules do not replace upstream archive
acquisition. The eight-lane cutoff remains unchanged by this scope clarification.

Direct publication retains its physical checks; that does not prove equivalent historical coverage
or source-completeness validation across every lane. `maintenance-validate-streams` still runs
PostgreSQL-oriented validation (`ingest/validation/queries.py`, including feature observation-day
queries), so its retained schedule is not a Parquet-wide quality certification.

Fixed-support dimension reads remain in `pipeline/direct/climate/support.py::load_nasa_power_support`,
`pipeline/direct/soil/support.py`, and `pipeline/direct/vegetation/support.py`; each selects the
ordered lattice from `agri.spatial_cell`. These are additional environmental dimension dependencies
alongside the separately retained operational locks and scheduler metadata.
