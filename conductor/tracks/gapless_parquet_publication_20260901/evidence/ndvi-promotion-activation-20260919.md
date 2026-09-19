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

---

# Re-activation after c922509d

Second owner-authorised activation attempt, same service/variable/scope as above. Repo read-only;
the only mutation was `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES`, set and then restored.

## Verdict: ROLLED-BACK (again) — the fix was real but did not touch the raise site

The W8-F fix (commit `c922509d` on `main`, live) genuinely re-pointed DAY SELECTION at the Parquet
availability index, and the pre-check proves it works. The first tick still failed with the
**identical** `ValueError`, because that error was never raised by day selection: it is raised by
`_corpus_digest` inside the register verb the promoter wraps. The rollback-record section above
mis-attributes the raise to `settled_through`; that attribution is CORRECTED below.

## Pre-check (before any mutation)

1. **Deployed commit is `c922509d`.** `c922509d3feec94fb11793553383da2f7d0abd41` is `origin/main`
   HEAD, committed `2026-09-18T21:31:59-06:00` = `2026-09-19T03:31:59Z`. The job-executor's active
   deployment `2729f604-5d11-4acd-adff-e7dc192b9947` was created `2026-09-19T03:32:35.020Z`
   (`SUCCESS`, instance `RUNNING`); its build image annotation reads
   `org.opencontainers.image.created: 2026-09-19T03:32:36Z`. All four plantgeo services
   (`plantgeo-main`, `plantgeo-martin`, `plantgeo-job-executor`, `plantgeo-parquet-api`) share that
   exact `createdAt`, i.e. one push, `SUCCESS` everywhere. `git status --porcelain` on
   `execution/vegetation_partition_promotion.py` and `execution/lane_specs.py` is clean, so the code
   read here is the code deployed.

2. **What `default_promotion_days` selects.** Read with the module's OWN reader
   (`read_lane_availability()` -> `availability_days_at_base_rung()`), executed against production
   credentials via `railway run --service plantgeo-job-executor` (read-only; no secret printed):

   ```
   today_utc: 2026-09-19
   index_row_count: 1495
   state_counts: {'published': 1219, 'governed_absence': 276}
   generation_present: True        pointer_present: True
   published_newest_5: ['2026-09-08','2026-09-09','2026-09-10','2026-09-11','2026-09-12']
   DEFAULT_MAX_DAYS: 1
   SELECTED_DAYS: ['2026-09-12']
      verdict 2026-09-12 IndexedDay(state='published', absence_reason=None)
   ```

   **Expected day list: `['2026-09-12']`** — one day, stated `published` at the base rung by a
   checksum-bound generation. The lane spec passes no `--max-days`, so the parser default
   `DEFAULT_MAX_DAYS = 1` applies.

   This is explicitly NOT the freshness-yardstick trap: the index is not empty, so the turn could
   not have gone green vacuously through `no_days_promoted`. The expected outcome was
   `status: completed` with a promotion receipt for 2026-09-12.

## Activation

- Rollback value recorded verbatim before mutation — byte-identical to the twelve-lane value in the
  section above.
- Variable set `2026-09-19T03:48:21Z` (`railway variables --set`, Railway CLI 5.45.2; no Railway MCP
  tools in this session's surface again).
- Activation deployment: **`48797397-883f-4682-bea8-ba8cd945ef64`**, auto-triggered by the variable
  write, created **`2026-09-19T03:48:22.872Z`**, `SUCCESS` with instance `RUNNING` observed at
  `03:49:21Z`.
- Startup inventory in that deployment: 14 lanes registered, **13 active**,
  `{"lane_id":"vegetation-ndvi-governed-plane-promotion","active":true,"schedule":"25 * * * *",
  "phase_offset_seconds":1500,"catch_up_policy":"coalesce_latest"}`, and
  `plantgeo_job_executor_tick_started active_lane_count=13 observed_at=2026-09-19T03:48:52.105416+00:00`.

## First tick — verbatim

Leader acquired `2026-09-19T03:48:52Z`; `coalesce_latest` immediately picked up the work item still
sitting in `retry_wait` from the FIRST activation, shard `2026-09-19T02:25:00+00:00`, so this ran as
**attempt 2 of 5** rather than as a fresh bucket:

```
2026-09-19T03:48:56.116759724Z [INFO]  error="ValueError: no vegetation observations exist at or before 2026-09-12" status="failed"
2026-09-19 03:48:56 [warning  ] job_work_item_failed           attempt_number=2 disposition=retry_wait failure_class=scheduled_command_exit max_attempts=5 shard_key=2026-09-19T02:25:00+00:00
2026-09-19 03:48:56 [error    ] plantgeo_job_executor_tick_unhealthy failing_lanes=['vegetation-ndvi-governed-plane-promotion'] incomplete_lanes=[] operator_actions=[]
```

- **exit**: 1, `failure_class=scheduled_command_exit`, stderr empty.
- **turn report**: none. No `completed` / `waiting_for_writer` / `no_days_promoted` JSON reached
  stdout — the exception escaped `main()`'s `except` as the one-line `{"status":"failed",...}` form.
- **writes**: none. Verified after the fact —
  `load_promotion_receipt(store, day=2026-09-12, kind="observed")` returns `None`, so no receipt was
  written for the day the turn selected, and `_corpus_digest` raises at
  `vegetation_ndvi_plane.py:674` BEFORE `_register_source_release` (line 676), so no governed-plane
  row was written either. No generation other than the read-only availability read was touched.

Unsafe by the activation rule (uncaught exception, work item failed, non-zero exit outside the
documented governed-absence case), so the variable was restored on sight.

## CORRECTED root cause — the raise is in the register verb, not in day selection

`grep` for the message text finds exactly one raise site, and it is not `settled_through`:

```
services/agri-data-service/src/agri_data_service/execution/vegetation_ndvi_plane.py:398
    raise ValueError(f"no vegetation observations exist at or before {cutoff_day.isoformat()}")
```

It lives in `_corpus_digest(session, *, cutoff_day)`, which executes the `_CORPUS_DIGEST` statement
against Postgres `agri.vegetation` (`layer_name=SOURCE_LAYER_NAME`) and raises when
`payload_checksum IS NULL` — i.e. when that frozen table has no rows at or before the cutoff. It is
called TWICE from `register_governed_forward_plane` (lines 674 and 703, the before/after corpus
comparison), which is precisely the verb `promote_vegetation_day_partition` wraps.

So the chain on this attempt was: Parquet index -> `default_promotion_days` -> `2026-09-12`
(the fix working) -> partition read OK -> `register_governed_forward_plane(cutoff_day=2026-09-12)`
-> `_corpus_digest` -> frozen Postgres -> `ValueError`.

The two attempts produced the same cutoff day by coincidence, which is what made the fix look
inert: `settled_through(today) = today - publication_lag_days` gave `2026-09-12` on 2026-09-19, and
the Parquet index's newest `published` day is ALSO `2026-09-12`. Same number, two different sources.
Re-reading the original log line supports this — it already said `2026-09-12`, never `2026-09-19`,
which was the clue that the raise was downstream of day selection all along.

## Follow-up needed before a third activation attempt

1. **The real blocker**: `register_governed_forward_plane`'s corpus digest reads frozen Postgres
   `agri.vegetation`. Until that whole-corpus digest is either re-sourced from Parquet or removed
   from the per-partition promotion path, this lane cannot complete a turn, regardless of day
   selection. This is the same "the governed plane still assumes a live Postgres source" gap, one
   layer deeper than W8-F patched.
2. **Stale work item trap**: shard `2026-09-19T02:25:00+00:00` is still in `retry_wait` at attempt
   2 of 5. Any future re-activation will run that pending item IMMEDIATELY on leader acquisition —
   before any fresh `25 * * * *` bucket — so the next attempt must either expect that replay or
   settle the work item first, and must not read its instant failure as a fresh-tick failure.

## Rollback confirmation

- Variable restored `2026-09-19T03:50:32Z`; re-read of `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` matches
  the twelve-lane rollback value byte for byte.
- Rollback deployment: **`ffc0e895-e727-4625-b6b1-7c8192a5005d`**, created
  **`2026-09-19T03:50:33.628Z`**, `SUCCESS`, instance `RUNNING`.
- Startup inventory in the rollback deployment: `active: 12`,
  `vegetation-ndvi-governed-plane-promotion active=False`,
  `plantgeo_job_executor_tick_started active_lane_count=12
  observed_at=2026-09-19T03:51:10.392517+00:00`, no `tick_unhealthy` line.
- Total window with the lane armed: `03:48:21Z` -> `03:50:32Z` (~2 minutes).
