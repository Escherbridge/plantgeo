---
type: evidence
---

# NDVI governed-plane promotion lane activation — 2026-09-19

Owner-authorised production change. Service: `plantgeo-job-executor`, Railway project "Aevani",
environment `production`. Variable: `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES`. No repo source file
touched; the app was never run locally.

## Verdict: ROLLED-BACK

The first live tick of `vegetation-ndvi-governed-plane-promotion` raised an uncaught `ValueError`,
not one of the two documented graceful exit-0 outcomes (`waiting_for_writer` /
`no_days_promoted:all_days_absent`). Per the activation task's rollback rule, the lane was
deactivated and the variable restored to its pre-activation value within ~6 minutes of first
observing the failure.

## Rollback value (recorded before any mutation)

```
PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES=burn-severity-direct-forward,climate-nasa-power-direct-forward,drought-direct-forward,evacuation-zones-direct-forward,fire-detections-direct-forward,sensors-direct-forward,soil-era5-land-direct-forward,vegetation-sentinel2-ndvi-direct-forward,water-gauges-direct-forward,watersheds-direct-forward,weather-observations-direct-forward,fire-perimeters-direct-forward
```

## New value set (activation attempt)

Appended `vegetation-ndvi-governed-plane-promotion` using the parser's separator (comma,
`_comma_tokens` in `execution/lane_specs.py:639-640`), never reordering or dropping the existing
twelve entries:

```
PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES=burn-severity-direct-forward,climate-nasa-power-direct-forward,drought-direct-forward,evacuation-zones-direct-forward,fire-detections-direct-forward,sensors-direct-forward,soil-era5-land-direct-forward,vegetation-sentinel2-ndvi-direct-forward,water-gauges-direct-forward,watersheds-direct-forward,weather-observations-direct-forward,fire-perimeters-direct-forward,vegetation-ndvi-governed-plane-promotion
```

Set via `railway variables --service plantgeo-job-executor --set ...` (Railway CLI 5.45.2, MCP
Railway tools were not present in this session's tool surface, so the CLI was used as the
documented fallback).

## Activation deployment

- Deployment id: `532d23be-a9d8-4138-838f-77f2ba45fa96`, triggered automatically by the variable
  write, `SUCCESS`, started `2026-09-19T02:40:18Z` (Railway auto-redeploys this service on env var
  change; no manual `railway redeploy` was needed).
- Startup inventory line confirmed the lane armed:
  ```
  {"active":true, ..., "lane_id":"vegetation-ndvi-governed-plane-promotion",
   "command":["python","-m","agri_data_service.execution.vegetation_partition_promotion"],
   "schedule":"25 * * * *","phase_offset_seconds":1500, ...}
  ```
  and the tick-started line showed `active_lane_count=13` (was 12 before).

## First tick

Leader acquired at `2026-09-19T02:41:19Z`. First bucket for the promotion lane's `25 * * * *`
schedule was `2026-09-19T02:25:00+00:00` (coalesced catch-up: the container started after :25 past
the hour, so `coalesce_latest` ran the current-hour bucket immediately on leader acquisition rather
than waiting for the next :25 wall-clock tick).

Confirmed via a direct query against `agri.job_work_item`/`agri.job_run`/`agri.job_definition` on
`plantgeo-spatiotemporal-db` (Railway `connect`, since the Railway MCP tools were not exposed to
this session):

```
work_item_id:      220400d2-159e-41f8-bdd5-23e95f5964b5
shard_key:          2026-09-19T02:25:00+00:00
status:              retry_wait
last_error_class:    scheduled_command_exit
last_error_summary:  lane 'vegetation-ndvi-governed-plane-promotion' command exited with status 1;
                      stderr: nothing captured
logical_run_key:    plantgeo.executor.vegetation-ndvi-governed-plane-promotion:2026-09-19T02:25:00+00:00
definition_name:    plantgeo.executor.vegetation-ndvi-governed-plane-promotion
```

Railway log line (the only line the process emitted for this run — no stack trace, stderr empty):

```
2026-09-19T02:41:29.306149698Z [INFO]  error="ValueError: no vegetation observations exist at or before 2026-09-12" status="failed"
2026-09-19 02:41:29 [warning  ] job_work_item_failed attempt_number=1 disposition=retry_wait failure_class=scheduled_command_exit max_attempts=5 shard_key=2026-09-19T02:25:00+00:00
```

- **run_id / attempt**: work item `220400d2-159e-41f8-bdd5-23e95f5964b5`, attempt 1 of 5 (retry_wait,
  would have retried with 30s initial backoff had the lane stayed active).
- **exit code**: 1 (non-zero, `scheduled_command_exit`).
- **promotion report (per-day statuses)**: NONE produced. The command raised before emitting the one
  terminal JSON report `execution/AGENTS.md` describes (`Turn reports` section); no `promoted` /
  `unchanged` / `absent` / `not_yet_indexed` / `waiting_for_writer` breakdown reached stdout, and no
  promotion-receipt object was written to the store (no `write_promotion_receipt` log line, no key
  named in the tail).
- **error**: `ValueError: no vegetation observations exist at or before 2026-09-12`, raised out of
  the default day-selection path (`run_vegetation_promotion` defaulting to
  `pipeline/direct/vegetation/forward.py::settled_through(today)`, per `execution/AGENTS.md`
  §Lane activation).

## Why this is a rollback, not an accepted outcome

`execution/AGENTS.md` names exactly three terminal states for this lane: `completed` (exit 0),
`waiting_for_writer` (exit 0, every day `not_yet_indexed`), and `no_days_promoted` (non-zero,
`all_days_absent` or `no_indexed_day_promoted`, still a structured JSON report). This run produced
none of those — it is an unhandled `ValueError` with no JSON report at all, which the activation
task's own rollback rule (step 4) requires reverting on sight. This also matches the exact risk the
implementing agent flagged in `.omc/ultrapilot-20260918/W2-D.md` §"Predicted breakage": item 2,
"Postgres source freeze interaction not verified" — `agri.vegetation` was frozen by the
`postgres-vegetation` lane retirement (owner call 2026-09-04), and `settled_through()`'s day
selection still queries Postgres directly rather than the Parquet-side availability index the
per-day content-SHA promotion decision itself reads. The per-partition promotion logic
(`day_partition_content_sha256`, receipt idempotency) was never reached; the failure is upstream, in
day selection.

## Rollback confirmation

- Variable restored: `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` back to the twelve-lane rollback value
  above (verified via `railway variables --json`).
- Rollback deployment: `540b24d5-5ef0-4ae9-a28a-3d07c72df0f5`, auto-triggered by the variable
  write, `SUCCESS`, started `2026-09-19T02:46:44Z`.
- Startup inventory in the rollback deployment confirms `"active":false` for
  `vegetation-ndvi-governed-plane-promotion` and `active_lane_count` back to 12.

## Follow-up needed before a second activation attempt

`run_vegetation_promotion`'s default day-selection (`settled_through(today)`) must be re-pointed at
a source that still has data — either the Parquet availability index the promotion's own per-day
content-SHA check already reads, or an explicit `--day` argument bypassing `settled_through`
entirely — before this lane can be re-armed. This is a code change in
`services/agri-data-service/src/agri_data_service/execution/vegetation_partition_promotion.py`
and/or `pipeline/direct/vegetation/forward.py`, out of scope for this ops task.
