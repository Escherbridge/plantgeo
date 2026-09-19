---
type: evidence
---

# Release checkpoint - hotfix 5672af3f - 2026-09-19

PASS -- all four gate services carry hotfix
`5672af3f70c3a4841b54caec6286e0338f8d6c48` and its parent `a83a9afd`, the two commits that close the
weather-observations retention-loss path and the drought forced-republication stranding path. Main
moved three times under this watch (the ML session and the web half of the same hotfix), so the four
services did NOT settle on one sha and were not expected to: `plantgeo-job-executor` and
`plantgeo-parquet-api` sit on `31acd231`, `plantgeo-main` and `plantgeo-martin` on `769451b3`, and
BOTH of those shas contain `5672af3f` as an ancestor (verified with `git merge-base --is-ancestor`,
section 1.3). **No gate service is on `3a548034`**, the revision that carried the data-loss path.

The probe that matters answered clean. The `weather-observations` lane ran a full scheduled turn at
15:30Z on the hotfix revision: `exit_code=0`, `outcome="complete"`, the new `recovery_phase` key
present on the terminal report naming `state="probed"`, retention `attempted=90 / retained=90 /
failed=0`, row arithmetic reconciling exactly (`existing_rows=1341` + `incoming_rows=90` =
`merged_rows=1431`, `updated_rows=0`, `rows_updated=0`), and NO `recovery_degraded`, NO
`probe_budget_exhausted`, NO `no_retained_capture` anywhere in production. Wall time went DOWN, not
up: 28.55 s against 30.05 s for the pre-hotfix 14:30Z turn and 29.19 s for 13:30Z, with the new
bounded probe phase costing 0.594 s of it. Full quotation in section 3.

The `drought` lane ran its ordinary `45 * * * *` walk at 15:47:37Z and selected and filled exactly as
it did before the hotfix -- `first_day="2025-07-29"`, `selected_weeks=[]`, `target_forced=false`,
`status="completed"`, `"incomplete":0` at every rung -- and `DroughtForwardConfigError` appears zero
times in production, which is correct: the new refusal is reachable only from an operator
`--target-day`.

Read-only throughout: no local run, no Railway variable written, no secret printed, no lane armed, no
commit. Every background process started for this watch was reaped (section 6).

plantgeo-ml (the fifth service, owned by another session) is INFORMATIONAL ONLY and not part of this
gate: its `5672af3f` deployment `fd7ceac5-122c-4b06-b3c0-24d4fe42b77d` resolved SKIPPED with
`skippedReason: "No changes to watched files"` -- the expected outcome for a Python-platform push --
and it has since deployed SUCCESS on `769451b3` under its own session's work. No action was taken on
it.

## 0. What this hotfix removed, and what a healthy turn now looks like

### 0.1 What was live and is now gone

Two paths shipped in wave 10 and were live in production from `3a548034` (13:14Z) until `5672af3f`
(14:49Z) -- about ninety-five minutes.

**B2, weather-observations: a transient store fault could destroy a poll's readings.** The wave-10
repair probe was placed ahead of the poll's retention, which is CORRECT (retention overwrites the
very checkpoint keys the probe reads), but it ran there unwrapped and outside the turn's deadline:

```python
owed = await asyncio.to_thread(_days_owed_a_recovery, store, tuple(tables))
recoveries = await asyncio.to_thread(_recover_owed_days, checkpoints, owed, points, now=fetched_at)
```

Neither call was inside a try, and neither took a deadline. So one transient object-store 5xx unwound
through `run()` to `main()`'s catch-all, and a poll of a feed with NO archive was neither published
NOR retained -- the instants it held in memory were gone permanently. Separately, the probe's roughly
150 object reads (one `list_partition_keys` per zoom tier per selected day plus one checkpoint GET per
sample point per owed day) were unbounded, so a slow bucket could spend the whole window, leave every
day `time_budget_exhausted`, and trip this lane's exit-1 breaker.

**B3, drought: a forced republication could strand a published release.** `--target-day` forced a
republication at ANY age, while `write_partition` retracts the day's completion marker as it uploads
`part-0` and writes the new marker last. A fault in between therefore left a previously PUBLISHED
release sitting `incomplete` -- and outside `DROUGHT_BACKLOG_SCAN_WEEKS` no scheduled turn ever
censuses that day again, so the damage was permanent and invisible.

### 0.2 What replaced them

Both phases now run inside `_repair_owed_days`, which is defined to return a `RecoveryPhase` on every
path including `except Exception`, and which may spend at most `RECOVERY_PROBE_BUDGET_SHARE = 0.25` of
what is LEFT of the turn's budget -- "a quarter covers the probe's ordinary cost and leaves three
quarters to the writes, which are the half of the turn that has no second chance." The retention call
itself got the same wrapper one step later (`_retain_current_poll`). `recover_weather_day` gained a
per-point deadline and a FOURTH verdict, `probe_budget_exhausted`, so an unfinished search is never
mis-reported as a loss:

```python
# An unfinished search is not an empty bucket: `no_retained_capture` is the word the forward
# writer prints as "lost, not owed", and it is only true of a grid that was actually walked.
state: WeatherRecoveryState = "probe_budget_exhausted" if unprobed else "no_retained_capture"
```

None of this is swallowed. A degraded probe gets its own event, is carried on the terminal report, and
costs the turn its `complete` through `_bucket_verdict(..., recovery_degraded=...)` -- but never its
exit code, because dropping readings to protect the backup of them is the failure the guard exists to
prevent.

On the drought side `_selected_release_weeks` now computes `scan_first_day` once and refuses a target
older than it, naming `backfill.py` as the correct tool for an older release. The ORDINARY scheduled
walk is arithmetically unchanged -- the same `max(lane.history_floor, settled_through -
timedelta(weeks=DROUGHT_BACKLOG_SCAN_WEEKS - 1))` expression, merely hoisted out of the `if`.

### 0.3 The reporting change a reader will notice first

**`recovered_days` is GONE from the terminal report and `recovery_phase` has replaced it.** The old
key was a bare list of per-day recovery events; the new one is a single object that also states what
the probe did NOT reach, because "two spellings of one fact drift." On a healthy turn that owed no
repair it reads:

```
recovery_phase={"days_deferred":[],"days_owed":[],"days_recovered":[],"state":"probed"}
```

`state="probed"` means the probe ran to completion and found nothing owed. The other four states a
reader may encounter are `no_day_to_repair` (the poll named no day at all),
`skipped_no_budget` (the turn's window was already spent before the probe began),
`failed` (a store fault, carrying `error_type` and `detail`), and -- not a state but a per-day verdict
inside `days_recovered` -- `probe_budget_exhausted`. A NEW standalone event,
`weather_observations_source_recovery_phase`, carries the same payload on the progress stream one line
after the fetch event.

**On a healthy turn you should see, and this checkpoint observed, exactly:** one
`weather_observations_source_recovery_phase` with `state="probed"` and all three lists empty; a
`weather_observations_source_retention` with `failed=0`; and a `weather_observations_forward_complete`
with `exit_code=0`, `outcome="complete"`, `recovery_phase` present, and NO `recovery_degraded` key
raised on stderr. If `recovery_degraded` or `probe_budget_exhausted` ever DOES appear, it is the new
machinery reporting rather than a fault: it means a repair window closed, not that a reading was lost.

## 1. Deploy watch, Railway project Aevani, environment production

### 1.1 The 5672af3f push

| Service | Deployment ID | Result | Commit | Created (UTC) | First observed SUCCESS (UTC) |
| --- | --- | --- | --- | --- | --- |
| plantgeo-martin | afa13008-7394-494e-aef8-fdb3478f12d3 | SUCCESS | 5672af3f | 2026-09-19T14:49:03.036Z | 2026-09-19T14:50:44Z (already SUCCESS at first observation) |
| plantgeo-job-executor | 34f0d42c-b583-43cd-83a9-8118702ba849 | SUCCESS | 5672af3f | 2026-09-19T14:49:03.037Z | 2026-09-19T14:50:44Z (already SUCCESS at first observation) |
| plantgeo-parquet-api | 4020e27b-60db-4282-a19c-4bbe030cfbab | SUCCESS | 5672af3f | 2026-09-19T14:49:03.037Z | 2026-09-19T14:51:49Z (DEPLOYING at 14:50:44Z) |
| plantgeo-main | a4a17c82-f474-4278-a947-b50cfee9ae9b | SUCCESS | 5672af3f | 2026-09-19T14:49:03.037Z | 2026-09-19T14:55:05Z |
| plantgeo-ml (NOT in the gate) | fd7ceac5-122c-4b06-b3c0-24d4fe42b77d | SKIPPED, "No changes to watched files" | 5672af3f | 2026-09-19T14:49:03.535Z | n/a |

All FOUR gate services fired their watch patterns on the `5672af3f` push; none SKIPPED. Polled
`railway deployment list -s <service> --json` every ~60 s from 14:50:44Z; the poller printed
`ALL_GATE_SERVICES_RESOLVED 2026-09-19T14:55:05Z` and exited on its own -- roughly 6.0 minutes from
deployment creation, well inside the 25-minute give-up window. plantgeo-main was BUILDING at 14:50:44Z,
14:51:49Z and 14:52:54Z, DEPLOYING at 14:54:28Z, SUCCESS at 14:55:05Z.

### 1.2 Main moved twice more; where the four services actually settled

Between 15:05Z and 15:33Z the branch advanced four more times (the web half of the same hotfix, then
three ML-session commits). Final settled state, read at 15:49Z:

| Service | Live deployment | Result | Commit landed | Created (UTC) | Carries 5672af3f |
| --- | --- | --- | --- | --- | --- |
| plantgeo-job-executor | 79d9865e-ee8d-4d0a-966d-66f15975f82d | SUCCESS | 31acd231 | 15:11:50Z | YES |
| plantgeo-parquet-api | 0efe627c (SUCCESS on 31acd231) | SUCCESS | 31acd231 | 15:11:50Z | YES |
| plantgeo-main | aa1a3830 | SUCCESS | 769451b3 | 15:33:24Z | YES |
| plantgeo-martin | acf3b259 | SUCCESS | 769451b3 | 15:33:24Z | YES |
| plantgeo-ml (NOT in the gate) | 82f0495d | SUCCESS | 769451b3 | 15:33:24Z | YES |

The two Python services legitimately SKIPPED `96831d8b` and `769451b3` with `skippedReason: "No
changes to watched files"` -- both are docs/web/ML-service commits that touch nothing under their
watch patterns (`infra/job-executor/**`, `services/agri-data-service/**`, `scripts/warm-soilgrids.mjs`,
`package.json`, `package-lock.json`). They took `31acd231` because it registers two agri lanes under
`services/agri-data-service/**`. This split is correct, not a fault.

### 1.3 Ancestry proof

```
$ git merge-base --is-ancestor 5672af3f 769451b3  ->  769451b3 contains 5672af3f: YES
$ git merge-base --is-ancestor 5672af3f 31acd231  ->  31acd231 contains 5672af3f: YES
```

No gate service is on `3a548034`.

### 1.4 plantgeo-main built clean through data-boundary and lint

Build log for the `96831d8b` build (`6e163df6-5fa8-4d06-aec0-eeaddcf0eb3d`), the first main build
carrying the whole web half. The Dockerfile runs the gates sequentially at `Dockerfile:65-70`:

```
RUN npm run check:data-boundary   # build  6/10
RUN npm run type-check            # build  7/10
RUN npm run lint                  # build  8/10
RUN npm test                      # build  9/10
RUN npm run build                 # build 10/10
```

Railway retained 12,216 lines of that build, beginning mid-way through stage 7, so stage 6's own
output has aged out of the retained window. Its PASS is nonetheless proven rather than assumed: Docker
`RUN` steps are sequential and a non-zero exit aborts the build, and stages 7, 8, 9 and 10 all ran to
completion and the deployment is SUCCESS. Quoted from the retained window:

```
> plantgeo@0.1.0 type-check
> tsc --noEmit
[INFO] [build  7/10] RUN npm run type-check
[INFO] [build  8/10] RUN npm run lint
...
✖ 555 problems (0 errors, 555 warnings)
[INFO] [build  9/10] RUN npm test
...
[INFO]  Test Files  221 passed (221)
[INFO]       Tests  2930 passed (2930)
[INFO]    Duration  158.06s
[INFO] [build 10/10] RUN npm run build
...
[1/1] Healthcheck succeeded!
```

`tsc --noEmit` emitted no diagnostics. ESLint reports **0 errors** and 555 warnings -- warnings do not
fail the gate and the count is the pre-existing React-compiler/`no-unused-expressions` population, not
new output from this push. This matters because the web half added a `no-restricted-syntax` rule to
`eslint.config.mjs` scoped to `src/hooks/useViewportProxiedLayers.ts` that BANS returning a raw
react-query observer result from that module, plus type-only annotations on five viewport reads; a
type-only import was caught failing exactly this gate before it shipped (see `372e9d64`, "verifier
fixes for the live-viewport-read conversion"). Both the new lint rule and the new type annotations
passed in the image that is serving.

## 2. Functional probes, production

### 2.1 /api/ready

GET https://plantgeo.aevani.com/api/ready -> 200, on the `5672af3f` main image:

```
{"status":"ready","checks":{"configuration":true,"database":true,"redis":true},"timestamp":"2026-09-19T14:55:45.890Z"}
```

and again on the final `769451b3` main image:

```
{"status":"ready","checks":{"configuration":true,"database":true,"redis":true},"timestamp":"2026-09-19T15:49:56.973Z"}
```

PASS. Identical shape to the 3a548034 baseline. The build's own healthcheck against `/api/ready` also
succeeded (section 1.4).

### 2.2 Selected-day reads against the 3a548034 baseline

Direct parquet-api, the baseline recipe
(`.../api/v1/parquet/day?layer=<L>&kind=observed&zoom=5&day=<D>&bbox=-124%2C47%2C-122%2C49`):

| Layer | Day | HTTP | state | requested_day | served_day | Rows | truncated | vs baseline |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| drought | 2026-09-08 | 200 | published | 2026-09-08 | 2026-09-08 | 2 | false | match |
| burn-severity | 2026-09-11 | 200 | published | 2026-09-11 | 2026-09-11 | 2 | false | match |

burn-severity still carries its own `mtbs_snapshot` block; drought's top-level keys are exactly
`requested_day, rows, served_day, state, truncated`.

Through plantgeo-main's tRPC readers, on the FINAL `769451b3` image:

| Procedure | Input | HTTP | state | requestedDay | servedDay | Rows |
| --- | --- | --- | --- | --- | --- | --- |
| environmental.getDroughtClassification | bbox=-124,47,-122,49 date=2026-09-08 zoom=5 | 200 | ready | 2026-09-08 | 2026-09-08 | 2 |
| environmental.getBurnSeverity | bbox=-124,47,-122,49 date=2026-09-11 zoom=5 | 200 | ready | 2026-09-11 | 2026-09-11 | 2 |

Both match the baseline row-for-row. `region_identity_mismatch` does not appear in either body.

### 2.3 Four further layer reads -- does any panel report differently?

| Procedure | HTTP | state | requestedDay | servedDay | Rows | vs 3a548034 baseline |
| --- | --- | --- | --- | --- | --- | --- |
| environmental.getStreamflow | 200 | ready | 2026-09-19 | 2026-09-19 | 38 | identical |
| environmental.getSensorStations | 200 | ready | 2026-09-19 | 2026-09-19 | 18 | identical |
| environmental.getEvacuationZones | 200 | ready | 2026-09-19 | 2026-09-16 | 0 | identical |
| wildfire.getFireDetections | 200 | not_generated (`day_not_written`) | 2026-09-19 | n/a | n/a | identical |

**No layer panel reports differently on the wire.** That is the whole of what a read-only server probe
can say about it, and it is worth saying precisely why. The "thirteen surfaces now withhold rather than
publish a retained frame when the map container is collapsed" change is entirely CLIENT-side: every
hook in `src/hooks/useViewportProxiedLayers.ts` now returns a `LiveViewportRead` whose fields are all
gated on the enablement composed for that hook's own observer, rather than a raw react-query result:

```ts
isFetching: isAnswerLive && query.isFetching === true,
isLoading: isAnswerLive && query.isLoading === true,
isSuccess: isAnswerLive && query.isSuccess === true,
isShowingRetainedAnswer: isAnswerLive && query.isPlaceholderData === true,
```

The trap it closes is that "TanStack leaves `isPlaceholderData` TRUE on a DISABLED `keepPreviousData`
observer, so an ungated read makes a hidden layer report itself permanently mid-load." Reproducing
either the old or the new behaviour requires a mounted hook with a collapsed map container -- a React
state transition. A server probe issues one independent request per call and can never hold a retained
frame, so the browser-side half is covered by its unit tests
(`src/__tests__/hooks/useViewportProxiedLayers.test.ts`, 226 new lines, inside the 2,930 that passed in
the build) and by the new `no-restricted-syntax` build gate, NOT by this checkpoint. Treat the panel
behaviour as unexercised in production, not as a known failure.

### 2.4 Coverage -- one change, correctly attributed elsewhere

GET https://plantgeo-parquet-api-production.up.railway.app/api/v1/parquet/coverage -> 200

```
coverage_schema_version = 3
layer_bindings length   = 16      <- was 14 at the 3a548034 baseline
region_slug             = "pnw"
region_display_name     = "Pacific Northwest"
evaluated_through_day   = 2026-09-19
```

land-context row, verbatim and unchanged:

```json
{"layer": "land-context", "binding": "unbound", "source": null, "reason": "no_source_bound_in_region"}
```

The two NEW bindings are `fire-risk` and `weather-forecast`. **They are not from this hotfix.** They
arrive with `31acd231` ("feat(agri): register fire-risk and weather-forecast lanes written by the ML
service"), the ML session's commit that the two Python services took at 15:11:50Z. The 14 baseline
layers are all still present with unchanged bindings. Recorded here so a future reader does not
attribute the 14 -> 16 move to the retention hotfix.

## 3. THE PROBE THAT MATTERS: weather-observations, one full scheduled turn on the hotfix

The 15:30:00Z bucket, dispatched on deployment `79d9865e-ee8d-4d0a-966d-66f15975f82d`
(commit `31acd231`, which contains `5672af3f`). Complete event chain, verbatim:

```
2026-09-19T15:30:12.497707688Z [INFO]  days_seen=["2026-09-19"] days_selected=["2026-09-19"]
  event="weather_observations_forward_fetch" fetched_at="2026-09-19T15:30:04.517060+00:00"
  observations_written=90 points_sampled=98 points_unavailable=8
  run_id="weather-observations-direct-forward-20260919T153004Z"

2026-09-19T15:30:13.091219987Z [INFO]  days_deferred=[] days_owed=[] days_recovered=[]
  event="weather_observations_source_recovery_phase"
  run_id="weather-observations-direct-forward-20260919T153004Z" state="probed"

2026-09-19T15:30:33.069403702Z [INFO]  event="weather_observations_source_retention"
  run_id="weather-observations-direct-forward-20260919T153004Z"
  source_checkpoints_attempted=90 source_checkpoints_failed=0 source_checkpoints_retained=90

2026-09-19T15:30:33.069409412Z [INFO]  absence_overturned=null actual_z13_rows=1431 attempt=1
  bytes=20838 day="2026-09-19" detail="2026-09-19: derived z9 98 rows in 1 part(s), z5 98 rows in
  1 part(s), z0 6 rows in 1 part(s); 2026-09-19: availability extended: the generation now covers
  2026-09-19 at every required rung" event="weather_observations_forward_attempt"
  incoming_rows_verified=90 outcome="written" parts=1 rows=1431
  run_id="weather-observations-direct-forward-20260919T153004Z"
  tier_statuses={"0":"data","5":"data","9":"data","13":"data"}

2026-09-19T15:30:33.069412322Z [INFO]  absence_overturned=null actual_z13_rows=1431 added_rows=90
  attempts=1 bytes=20838 day="2026-09-19" detail="2026-09-19: derived z9 98 rows in 1 part(s), z5 98
  rows in 1 part(s), z0 6 rows in 1 part(s); 2026-09-19: availability extended: the generation now
  covers 2026-09-19 at every required rung" event="weather_observations_forward_checkpoint"
  existing_rows=1341 incoming_rows=90 incoming_rows_verified=90 merged_rows=1431
  namespace="layer=weather-observations/kind=observed" outcome="written" parts=1 rows=1431
  run_id="weather-observations-direct-forward-20260919T153004Z" updated_rows=0

2026-09-19T15:30:33.069415632Z [INFO]  absences_overturned=[] actual_z13_rows=1431
  availability_extended=1 availability_ladder_incomplete=0 availability_not_bootstrapped=0
  availability_quarantined_standing=0 availability_reindex_owed=0 availability_retry_claim_failed=0
  availability_retry_owed=0 availability_skipped_unchanged=0 bytes=20838 days=1 days_unwritten=0
  days_written=1 event="weather_observations_forward_complete" exit_code=0 incoming_rows=90
  incoming_rows_verified=90 merged_rows=1431 outcome="complete" outcomes={"written":1} parts=1
  recovery_phase={"days_deferred":[],"days_owed":[],"days_recovered":[],"state":"probed"}
  rows=1431 rows_added=90 rows_updated=0
  run_id="weather-observations-direct-forward-20260919T153004Z" source_checkpoints_attempted=90
  source_checkpoints_failed=0 source_checkpoints_retained=90 unwritten=[]
```

Each requirement, answered against that text:

**(a) `exit_code=0` and publishes as before, row arithmetic reconciles.** `exit_code=0`,
`outcome="complete"`, `outcomes={"written":1}`, `days_written=1`, `days_unwritten=0`, `unwritten=[]`.
`existing_rows=1341` + `incoming_rows=90` = `merged_rows=1431` = `rows=1431` = `actual_z13_rows=1431`,
with `added_rows=90`, `updated_rows=0` and `rows_updated=0`. The 90 readings landed once each and
nothing already published was rewritten. `incoming_rows_verified=90` equals `incoming_rows`, and all
four rungs are `data`.

**(b) The new `recovery_phase` key is present and names its state.** On the terminal report:
`recovery_phase={"days_deferred":[],"days_owed":[],"days_recovered":[],"state":"probed"}`. The
separate progress event `weather_observations_source_recovery_phase` carries the identical payload at
15:30:13.091Z. `state="probed"` is the correct answer here: 2026-09-19 was already `data` at z0/z5/z9/z13
from the 14:30Z poll, so `_days_owed_a_recovery` returned nothing owed and the probe completed within
budget having found nothing to repair. The old `recovered_days=[]` key is GONE, as intended.

**(c) Retention reports every checkpoint attempted and retained, zero failures.**
`source_checkpoints_attempted=90`, `source_checkpoints_retained=90`, `source_checkpoints_failed=0`,
on both the `weather_observations_source_retention` event and the terminal report. Attempted equals
`observations_written=90` from the fetch event, so every accepted point was checkpointed and every
checkpoint was proven retained. No `weather_observations_source_retention_failed` event was emitted.

**(d) No `recovery_degraded`, no `probe_budget_exhausted`.** Both strings appear ZERO times across
every production log captured on all three services (section 5). `_report_bucket_incomplete` printed
nothing to stderr, which is the correct silence for a verdict with no unwritten day, no retention
failure and no degraded probe.

**(e) Wall time has not grown.** Measured fetch-start to terminal-report on the same lane:

| Turn | Revision | `fetched_at` | terminal report | Wall |
| --- | --- | --- | --- | --- |
| 13:30Z | 3a548034 (pre-hotfix) | 13:30:08.185227Z | 13:30:37.378810Z | **29.19 s** |
| 14:30Z | 3a548034 (pre-hotfix) | 14:30:05.094518Z | 14:30:35.144086Z | **30.05 s** |
| 15:30Z | 31acd231 (post-hotfix) | 15:30:04.517060Z | 15:30:33.069416Z | **28.55 s** |

The post-hotfix turn is 1.5 s FASTER than the immediately preceding pre-hotfix turn and 0.6 s faster
than the one before that -- inside ordinary provider variance (the three turns also fetched 83, 76 and
90 readings respectively), and certainly not material growth. The new bounded probe phase is directly
measurable as the gap between the fetch event and the recovery-phase event: 15:30:12.498Z ->
15:30:13.091Z = **0.594 s**, i.e. the two `list_partition_keys` tier listings for the one selected day.
Because that day came back `data` at every tier, no owed day existed and NONE of the per-point
checkpoint GETs were issued -- which is the ordinary shape, and why the probe's quarter-share of the
budget went almost entirely unspent.

## 4. The drought lane, ordinary scheduled walk at `45 * * * *`

The 15:45:00Z bucket, dispatched 15:47:37Z, verbatim:

```
2026-09-19T15:47:37.360268998Z [ERRO]  event="drought_forward_started" first_day="2025-07-29"
  history_floor="2022-08-09" layer="drought" namespace="layer=drought/kind=observed/"
  run_id="drought-forward:ab2e616b-aded-4c56-a86e-bebabbb95f41" selected_weeks=[]
  settled_through="2026-09-15" target_day=null target_forced=false

2026-09-19T15:47:38.148445071Z [INFO]  availability_extended=0 availability_ladder_incomplete=0
  availability_not_bootstrapped=0 availability_quarantined_standing=0 availability_reindex_owed=0
  availability_retry_claim_failed=0 availability_retry_owed=0 availability_skipped_unchanged=0
  days_published=0 first_day="2025-07-29" history_floor="2022-08-09" layer="drought"
  namespace="layer=drought/kind=observed/" remaining_window_backlog=[] results=[]
  run_id="drought-forward:ab2e616b-aded-4c56-a86e-bebabbb95f41" settled_through="2026-09-15"
  status="completed" target_day=null target_forced=false
  tier_status_counts={"z0":{"absent":2,"conflict":0,"data":58,"incomplete":0,"missing":0},
                      "z13":{"absent":2,"conflict":0,"data":58,"incomplete":0,"missing":0},
                      "z5":{"absent":2,"conflict":0,"data":58,"incomplete":0,"missing":0},
                      "z9":{"absent":2,"conflict":0,"data":58,"incomplete":0,"missing":0}}
```

It selects and fills exactly as before. Compare the last PRE-hotfix walk, the 14:45Z bucket on
`3a548034` (deployment `8b49729d`, dispatched 14:47:47Z):

```
2026-09-19T14:47:47.336315708Z [ERRO]  event="drought_forward_started" first_day="2025-07-29"
  history_floor="2022-08-09" ... selected_weeks=[] settled_through="2026-09-15"
  target_day=null target_forced=false
  -> status="completed" days_published=0 remaining_window_backlog=[] results=[]
     tier_status_counts={"z0":{"absent":2,"conflict":0,"data":58,"incomplete":0,"missing":0}, ...}
```

Field for field identical: same `first_day="2025-07-29"`, same `settled_through="2026-09-15"`, same
empty `selected_weeks`, same `status="completed"`, same `days_published=0`, same 58-`data`/2-`absent`
census at every one of the four rungs. `first_day` is the load-bearing value here -- it IS
`scan_first_day`, the expression the hotfix hoisted out of the `if config.target_day is None` branch
(`max(lane.history_floor, settled_through - timedelta(weeks=DROUGHT_BACKLOG_SCAN_WEEKS - 1))`), so its
being unchanged at `2025-07-29` is the direct evidence that the refactor did not move the scheduled
walk's window by a single week.

`target_day=null` and `target_forced=false` confirm the scheduled path never touches the new refusal.
**`DroughtForwardConfigError` appears ZERO times in production**, which is the required outcome: the
new bound is reachable only from an operator `--target-day` older than the census horizon, and no
operator ran one.

`"incomplete":0` at every rung is worth naming separately -- it is the direct census answer to the
defect B3 described. No drought release is sitting unfinished.

Note on log level: `drought_forward_started` is tagged `[ERRO]` by Railway because this lane writes
that line to stderr. The payload is a normal start event; the tag is a stream classification, not a
fault. Same pre-hotfix.

## 5. Log sweep

`railway logs` on the live deployment of each service, filtered case-insensitively.

| Pattern | plantgeo-job-executor | plantgeo-main | plantgeo-parquet-api |
| --- | --- | --- | --- |
| Traceback | 0 | 0 | 0 |
| **recovery_degraded** | **0** | 0 | 0 |
| **probe_budget_exhausted** | **0** | 0 | 0 |
| **DroughtForwardConfigError** | **0** | 0 | 0 |
| no_retained_capture | 0 | 0 | 0 |
| region_identity_mismatch | 0 | 0 | 0 |
| contract_mismatch | 0 | 0 | 0 |
| CRITICAL | 0 | 0 | 0 |
| ImportError | 0 | 0 | 0 |
| DeprecationWarning | 0 | 0 | 2 (pre-existing) |

**No lane newly reports `incomplete`.** Every occurrence of the string in the job-executor window was
enumerated and every one is a zero or an empty list:

```
      8  "incomplete":0                     (drought tier_status_counts, 4 rungs x 2 turns)
      9  availability_ladder_incomplete=0
      7  consecutive_incomplete_buckets":0
    100  incomplete_lanes=[]
      0  outcome":"incomplete"
```

Across 50 `plantgeo_job_executor_tick` events: `failed=true` count is **0**, `incomplete_lanes=[]` on
every one. The most recent tick shows all twelve active lanes at `run_status: "succeeded"`:

```
burn-severity-direct-forward              succeeded
climate-nasa-power-direct-forward         succeeded
drought-direct-forward                    succeeded
evacuation-zones-direct-forward           succeeded
fire-detections-direct-forward            succeeded
fire-perimeters-direct-forward            succeeded
sensors-direct-forward                    succeeded
soil-era5-land-direct-forward             succeeded
vegetation-sentinel2-ndvi-direct-forward  succeeded
water-gauges-direct-forward               succeeded
watersheds-direct-forward                 succeeded
weather-observations-direct-forward       succeeded
```

The startup inventory on the hotfix revision lists **14 lanes, 12 active**, matching
`active_lane_count=12` and `lane_count=14` -- unchanged from the 3a548034 baseline, with
`vegetation-ndvi-governed-plane-promotion` (`25 * * * *`) still `active: false` and `mtbs-forward`
(`55 7 * * 2`) still `active: false`. No lane was armed by this push.

The two plantgeo-parquet-api DeprecationWarnings are the SAME pre-existing Sanic notice documented in
the 37963657, 0503ccd8 and 3a548034 checkpoints:

```
/app/.venv/lib/python3.12/site-packages/sanic/logging/deprecation.py:33: DeprecationWarning:
[DEPRECATION v26.6] Passing the loop argument to listeners is deprecated. Your listener
'create_app.<locals>.setup_resources' should only accept the app argument.
```

No new warning class was introduced by this push.

## 6. Read-only discipline and process hygiene

- No local run of the application, no `npm run dev`, no local container.
- No Railway variable read for its value, written, or printed. No secret in this document.
- No lane armed, no operator `--target-day` or `--recover-day` issued, no production mutation.
- Two background pollers were started: the deploy watch (exited 0 on its own after printing
  `ALL_GATE_SERVICES_RESOLVED`) and a lane-log poller. The lane poller was pinned to job-executor
  deployment `34f0d42c`, which went REMOVED when the service redeployed onto `31acd231` at 15:11:50Z;
  it was identified as watching a dead deployment, killed explicitly (`kill -9` on its shell and its
  `sleep` child) and confirmed gone from the process table before the watch was re-pointed at the live
  deployment `79d9865e`. No `railway` child process of this session remains.

## Verdict

PASS. All four gate services carry hotfix `5672af3f70c3a4841b54caec6286e0338f8d6c48`:
plantgeo-job-executor and plantgeo-parquet-api on `31acd231`, plantgeo-main and plantgeo-martin on
`769451b3`, both shas proven to contain the hotfix; none is on `3a548034`, and every SKIP in the
sequence is a legitimate "No changes to watched files" on a commit outside the skipping service's
watch patterns. plantgeo-main built clean through `check:data-boundary` (stage 6/10, proven by the
sequential completion of stages 7-10 and a SUCCESS deployment; its own output has aged out of the
retained log window), `tsc --noEmit` with no diagnostics, ESLint at **0 errors** / 555 pre-existing
warnings including the new `no-restricted-syntax` gate on the viewport-read module, 2,930 tests passed
and a succeeding `/api/ready` healthcheck.

The weather-observations lane completed its 15:30Z scheduled turn with `exit_code=0`,
`outcome="complete"`, the new `recovery_phase={"days_deferred":[],"days_owed":[],"days_recovered":[],
"state":"probed"}` present on the terminal report and on its own new progress event, retention
`90/90/0`, row arithmetic reconciling exactly (1341 + 90 = 1431, `rows_updated=0`), no
`recovery_degraded` and no `probe_budget_exhausted` anywhere, and a wall time of 28.55 s against 30.05 s
and 29.19 s for the two pre-hotfix turns -- the bounded probe phase itself costing 0.594 s. The drought
lane ran its ordinary `45 * * * *` walk field-for-field identically to the pre-hotfix turn, with
`first_day="2025-07-29"` proving the hoisted `scan_first_day` expression did not move the window,
`target_forced=false`, `"incomplete":0` at every rung, and zero `DroughtForwardConfigError` in
production. `/api/ready` 200 on both images; drought 2026-09-08 and burn-severity 2026-09-11 reads are
row-for-row identical to the 3a548034 baseline on both the direct parquet-api path and the plantgeo-main
tRPC path; four further layer reads are identical to the baseline; the log sweep is clean on all three
services apart from the one pre-existing Sanic DeprecationWarning, with zero `Traceback`, zero
`recovery_degraded`, zero `probe_budget_exhausted`, zero `DroughtForwardConfigError`, and no lane
newly reporting `incomplete`.

UNEXERCISED (not failed): the DEGRADED paths this hotfix exists for. `recovery_phase` states `failed`,
`skipped_no_budget` and the per-day `probe_budget_exhausted` verdict, the `recovery_degraded`
`incomplete` bucket, and the `weather_observations_source_retention_failed` event all require a
genuinely faulting or genuinely slow object store, which a read-only pass cannot and must not induce in
production; they are covered by the eleven new unit tests in the receipt (4,565 passed / 89 skipped /
1 xfailed) and by code reading. Also unexercised: the drought `--target-day` refusal itself, which is
operator-only by construction; and the client-side half of the web change, the thirteen surfaces that
now withhold a retained frame on a collapsed map container, which needs a mounted React hook and is
covered by `src/__tests__/hooks/useViewportProxiedLayers.test.ts` and the new lint gate.

ATTRIBUTED ELSEWHERE, not a regression: coverage `layer_bindings` moved 14 -> 16 with the addition of
`fire-risk` and `weather-forecast`, which arrive with the ML session's `31acd231`, not with this
hotfix. The 14 baseline layers are unchanged.

plantgeo-ml (informational, another session's service, not in this gate): deployment
`fd7ceac5-122c-4b06-b3c0-24d4fe42b77d` on `5672af3f` resolved SKIPPED, `skippedReason: "No changes to
watched files"`; it has since deployed SUCCESS (`82f0495d`) on `769451b3` under that session's own
work. No action taken on it.
