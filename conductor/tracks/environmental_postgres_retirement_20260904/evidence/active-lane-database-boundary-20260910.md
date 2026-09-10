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
archive planners/reconcilers. The two materialized-view refresh jobs remain active for now: a separate
consumer audit found `forecast_summary_for_cell` reading `agri.mv_forecast_ml_daily_serving` and
regional strategy AI context reading `geo.mv_strategy_recommendations_regional`. They are not
reader-free, and their consumer retirement is separate work. Community demand/vote operations use
operational tables independently of those views.

Direct climate and soil writers publish environmental observations directly into Parquet, but still
read their fixed support definitions from `agri.spatial_cell`. That small dimension read is a remaining
migration dependency, distinct from the avoided historical observation export and from operational
publication locks. A future bucket-pinned support artifact must preserve exact IDs, coordinates,
coverage fractions, order, and grid validation before that lookup can be removed.
