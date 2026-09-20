---
type: evidence
---

# Release checkpoint - wave 11 c377d349 - 2026-09-19

PASS -- all four gate services carry wave 11
`c377d349ca0b55e8cd75c65a811aa55b09c9b000`. `plantgeo-job-executor` and `plantgeo-parquet-api` are
live ON `c377d349` itself; `plantgeo-main` and `plantgeo-martin` are live on `d6ef9921`, which
CONTAINS it (verified with `git merge-base --is-ancestor`, section 1.4). Main moved five times under
this watch -- `8e2abb29`, `0e0e26c2`, `cc6c6868`, `d6ef9921`, all from the ML session and the docs
commit for the previous checkpoint -- so the four services did NOT settle on one sha and were not
expected to. **No gate service is on a revision older than `5672af3f`**; the oldest revision any gate
service served at any point during this watch was `769451b3`, which contains `5672af3f`.

**This is the push that returned main to green.** Main was RED before it: the lane contract's section
3 was amended earlier the same day so that a deterministic product writes the literal
`quantile = "point"` with `ensemble_size = 1` (`layer-lanes.md:208-213`), the ML service -- which
WRITES the fire-risk stream -- implemented it, and agri's reader copy still declared a `float64`. Both
`test_ml_schema_parity` fire-risk assertions therefore failed on clean `origin/main`. `202ac24d`
closed the divergence by deriving `DETERMINISTIC_PROVENANCE_FIELDS` in
`warehouse/schemas/fire_risk.py` from the shared six by substituting that one field, and `c377d349`
re-swept and recorded the result:

```
All four Python gates PASS: 4627 passed, 89 skipped, 1 xfailed, 0 failed.
Delta against the first pass is exactly +2 passed / -2 failed on an unchanged 4717 collected --
the two test_ml_schema_parity.py assertions flipping green, and nothing else moving.
```

The same push carries the wave-10 review's should-fixes: the promotion-reporting rename
(`434bf6b5`), the served-region guard's visibility (`096d8b08`) and the weather support-grid
discriminator (`9f433f99`).

Three of the four things this wave changed are NOT observable from a read-only production pass, and
section 6 states that plainly rather than implying coverage: the NDVI promotion turn (the lane is
still shadow and was deliberately not armed), the region guard's inert path (it only opens on a
deployment whose census fails to decode, and production's decodes), and the weather
`foreign_support_grid` path (it only opens when an operator moves `INGEST_BBOX` or the sample
spacing). What IS observable answered clean.

Read-only throughout: no local run, no Railway variable written, no secret printed, no lane armed, no
commit. Every background process started for this watch was reaped (section 7).

plantgeo-ml (the fifth service, owned by another session) is INFORMATIONAL ONLY and not part of this
gate: its `c377d349` deployment `1110b184` resolved SKIPPED with `skippedReason: "No changes to
watched files"` -- the expected outcome, since wave 11's agri-side change touches nothing under that
service's watch patterns. It has since deployed SUCCESS on `d6ef9921` under its own session's work.
No action was taken on it.

## 0. What this wave changed, and what a healthy observation looks like

### 0.1 Promotion reporting -- a RENAME, not a re-tuning

The staleness bound was renamed, and the arithmetic behind it is unchanged. The constant
`VEGETATION_PROMOTION_STALE_CEILING_WINDOWS` became
`VEGETATION_PROMOTION_STALE_CEILING_LAG_ALLOWANCES` and the accessor
`vegetation_promotion_publication_window_days()` became
`vegetation_promotion_declared_lag_days()`, both in
`services/agri-data-service/src/agri_data_service/execution/lane_specs.py`. The value is still read
at call time from `LANE_REGISTRY` and never copied:

```python
def vegetation_promotion_declared_lag_days() -> int:
    """Return the vegetation lane's REGISTERED `publication_lag_days`, read at call time.
    ...
    """
    lag_days = _registration("vegetation")[0]
```

The rename exists because the old name claimed a provider fact the code never measured. The docstring
now says so directly -- "It is also not a provider observation: nothing in this call opens a socket"
-- and the allowance count is still `2` (STYLE-REVIEW-W10 S1).

The second half is a report-shape change. All SIX terminal statuses of
`execution/vegetation_partition_promotion.py` now share one core shape that always includes a
`reason`, because `failed` was previously the one terminal status with no `reason` key and a log
consumer keying on it saw shape drift (STYLE-REVIEW-W10 S2/N3):

```python
# Stated rather than omitted so `reason` is present on all six terminal statuses and a log
# consumer keying on it sees one shape.
report["reason"] = "at_least_one_day_was_promoted_or_confirmed_unchanged"
```

**What a healthy observation looks like:** the NDVI lane is still shadow, so no turn can be observed
at all. The observable claim is negative and structural -- the job executor still registers 14 lanes
with 12 active, `vegetation-ndvi-governed-plane-promotion` is still `active: false`, and NO OTHER
lane's terminal report gained or lost a key against the `5672af3f` checkpoint. Sections 4 and 4.2.

### 0.2 Region guard -- stays OPEN, but can no longer pass unnoticed

`assertServedRegionMatchesBundle` learned the served region from a coverage-census decode, so a
deployment whose census never decodes -- the documented pre-bootstrap state, ~28 s against an 8 s
timeout -- guarded nothing for the life of the process, silently. `learnServedRegion` now owns the
attempt, spends at most one census per `REGION_LEARNING_RETRY_MS` (60 s), and when none has decoded it
logs, at `src/lib/server/services/parquet-plane-client.ts:960`:

```
Parquet region guard inert: no census has stated a region; row reads proceed unguarded
```

plus an exported `servedRegionGuardStatus()` reporting `armed: false` with the failed-attempt count.
The guard still stays OPEN rather than failing closed, "because refusing every read on a cold start
turns a rare static misconfiguration into a certain outage."

**What a healthy observation looks like:** on a healthy deployment the census decodes, the guard arms,
and that line NEVER appears. Its presence in production would be a finding, not a feature. It appears
ZERO times on every service (sections 2.4 and 5).

### 0.3 Weather recovery -- a changed grid is OWED, not lost

Every retained checkpoint key is bound to `weather_support_sha256(points)`, whose inputs -- `INGEST_BBOX`
and the sample spacing -- are read at call time, so moving the grid relocates every retained body and
`--recover-day` then told the operator the bucket was "lost, not owed" about data sitting on disk.
`WeatherRecoveryState` gained a FIFTH member, and `record_support_witness` writes one grid-independent
object per day so `recover_weather_day` can read it FIRST and return the new verdict without walking a
grid whose keys cannot exist:

```python
WeatherRecoveryState = Literal[
    "complete_capture",
    "partial_capture",
    "no_retained_capture",
    "probe_budget_exhausted",
    "foreign_support_grid",
]
```

The refusal now speaks three answers apart, all three reachable only from an operator `--recover-day`
(`pipeline/direct/weather_observations/forward.py:988-1009`): **OWED** when the witness names a
different grid ("INGEST_BBOX or the weather sample spacing has changed since, so the bucket is OWED,
not lost"), **UNKNOWN** when no witness survives ("The bucket is UNKNOWN, not provably lost: check
INGEST_BBOX and the weather sample spacing before concluding"), and **LOST** only when the grid it was
polled with was actually walked and held nothing.

**What a healthy observation looks like:** the grid has not changed, so the correct observation is an
ordinary turn with NONE of those states -- `recovery_phase` naming `state="probed"`, retention
attempted equal to retained with zero failures, and row arithmetic reconciling. Section 3.

### 0.4 Fire-risk schema -- the reader now matches the writer

`fire_risk.py` declares `DETERMINISTIC_PROVENANCE_FIELDS` locally, derived from the shared tuple by
substituting `quantile`, with `FIRE_RISK_POINT_QUANTILE = "point"`. It stays LOCAL because fire-risk
is the only slug in `FORECAST_ORIGINATED_STREAMS` and a shared `DETERMINISTIC_*` name "would advertise
a mode `forecast_stream_schema()` cannot be asked for." The shared `FORECAST_PROVENANCE_FIELDS` keeps
`quantile` a `float64` for the drawn lanes (Analog Ensemble, Monte Carlo) and is untouched.

**What a healthy observation looks like:** nothing publishes fire-risk yet, so there is no row to read.
The observable claim is that the parquet-api booted clean, the stream registry did not refuse at
startup, and `/api/v1/parquet/coverage` still answers with `layer_bindings` intact. Sections 2.3 and 2.4.

## 1. Deploy watch, Railway project Aevani, environment production

### 1.1 The c377d349 push

| Service | Deployment ID | Result | Commit | Created (UTC) | Note |
| --- | --- | --- | --- | --- | --- |
| plantgeo-job-executor | 47a9cad6-e88f-4d14-8861-d64bf4db911d | **SUCCESS (live)** | c377d349 | 2026-09-19T16:33:40.312Z | still serving at 17:34:28Z |
| plantgeo-parquet-api | 2329414a-6fb0-42f2-bac2-2472a4b02c39 | **SUCCESS (live)** | c377d349 | 2026-09-19T16:33:40.312Z | still serving at 17:34:28Z |
| plantgeo-martin | 510c6fcb-551f-4baf-a744-a6bd6e8db939 | RUNNING, later REMOVED | c377d349 | 2026-09-19T16:33:40.312Z | observed RUNNING at 16:33:57Z, superseded 16:34:38Z |
| plantgeo-main | 0bec5c1b-e63c-4abd-9e00-ddafce180fe5 | REMOVED while building | c377d349 | 2026-09-19T16:33:40.311Z | superseded by `8e2abb29` before it finished |
| plantgeo-ml (NOT in the gate) | 1110b184 | SKIPPED, `"No changes to watched files"` | c377d349 | 2026-09-19T16:33:40.887Z | n/a |

All FOUR gate services fired their watch patterns on the `c377d349` push; none SKIPPED. Polled
`railway status --json` every ~60 s from a background job starting 16:34:46Z; the poller ran its full
25 rounds, printed `WATCH_DONE` and exited 0 on its own at 16:58:51Z -- well inside the 25-minute
give-up window. The two Python services were SUCCESS on `c377d349` at first observation (16:33:57Z,
~17 s after deployment creation).

**Martin landed `c377d349` directly and served it.** Main did not: its `c377d349` build was still
INITIALIZING when `8e2abb29` superseded it 58 seconds later. Main therefore never served `c377d349`
itself -- it served `8e2abb29`, then `0e0e26c2`, then `d6ef9921`, each of which contains `c377d349`.
Stated plainly because the distinction matters to anyone re-reading this table.

### 1.2 Main moved four more times; where the four services settled

Between 16:34Z and 16:46Z the branch advanced four more times (one docs commit -- the previous
checkpoint itself -- and three ML-session commits). Final settled state, read at 17:34:28Z:

| Service | Live deployment | Result | Commit landed | Created (UTC) | Contains c377d349 |
| --- | --- | --- | --- | --- | --- |
| plantgeo-job-executor | 47a9cad6-e88f-4d14-8861-d64bf4db911d | SUCCESS | **c377d349** | 16:33:40Z | is c377d349 |
| plantgeo-parquet-api | 2329414a-6fb0-42f2-bac2-2472a4b02c39 | SUCCESS | **c377d349** | 16:33:40Z | is c377d349 |
| plantgeo-main | 706524d2-cfc3-46d9-9923-8c73717cdbfa | SUCCESS | d6ef9921 | 16:46:07Z | YES |
| plantgeo-martin | 27b97151-a965-4433-ae89-2a7200d56f63 | SUCCESS | d6ef9921 | 16:46:07Z | YES |
| plantgeo-ml (NOT in the gate) | e74a2053-a4f4-4c41-a6e4-f77d96d2b7ff | SUCCESS | d6ef9921 | 16:46:07Z | YES |

### 1.3 Every SKIP in the sequence is legitimate, and named

The two Python gate services SKIPPED every commit after `c377d349`, all with the same reason:

```
plantgeo-job-executor   cc6c6868 -> 'No changes to watched files'
plantgeo-job-executor   0e0e26c2 -> 'No changes to watched files'
plantgeo-job-executor   8e2abb29 -> 'No changes to watched files'
plantgeo-parquet-api    cc6c6868 -> 'No changes to watched files'
plantgeo-parquet-api    0e0e26c2 -> 'No changes to watched files'
plantgeo-parquet-api    8e2abb29 -> 'No changes to watched files'
plantgeo-ml             c377d349 -> 'No changes to watched files'
plantgeo-ml             8e2abb29 -> 'No changes to watched files'
```

This is correct, not a fault. `8e2abb29` is the previous checkpoint's own docs commit; `0e0e26c2`,
`cc6c6868` and `d6ef9921` are ML-service commits (`services/plantgeo-ml-service/**` and its Docker
toolchain). None touches `infra/job-executor/**`, `services/agri-data-service/**`,
`scripts/warm-soilgrids.mjs`, `package.json` or `package-lock.json`, so neither Python service had
anything to rebuild -- and both therefore remain on `c377d349`, which is exactly the revision this
gate wanted them on. Symmetrically, `plantgeo-ml` SKIPPED `c377d349` because wave 11's agri-side
change touches nothing under its watch patterns.

### 1.4 Ancestry proof

```
$ git merge-base --is-ancestor c377d349 8e2abb29  ->  8e2abb29 contains c377d349: YES
$ git merge-base --is-ancestor c377d349 0e0e26c2  ->  0e0e26c2 contains c377d349: YES
$ git merge-base --is-ancestor c377d349 cc6c6868  ->  cc6c6868 contains c377d349: YES
$ git merge-base --is-ancestor c377d349 d6ef9921  ->  d6ef9921 contains c377d349: YES
$ git merge-base --is-ancestor 5672af3f c377d349  ->  c377d349 contains 5672af3f: YES
$ git merge-base --is-ancestor 5672af3f d6ef9921  ->  d6ef9921 contains 5672af3f: YES
```

**No gate service is, or was at any point in this watch, on a revision older than `5672af3f`.** The
oldest revision observed serving during the watch was `769451b3` (plantgeo-main, until 16:34:38Z), and
`769451b3` contains `5672af3f` -- proven in the previous checkpoint, section 1.3.

### 1.5 plantgeo-main built clean

Build log for the settled `d6ef9921` deployment (`706524d2`). The Dockerfile runs the gates
sequentially at `Dockerfile:65-70`. Railway retained 500 lines beginning inside stage 9, so stages
6 (`check:data-boundary`), 7 (`type-check`) and 8 (`lint`) have aged out of the retained window --
the same retention behaviour recorded in the `5672af3f` checkpoint. Their PASS is proven rather than
assumed: Docker `RUN` steps are sequential, a non-zero exit aborts the build, and stages 9 and 10 both
ran to completion with a SUCCESS deployment and a succeeding healthcheck. Quoted from the retained
window:

```
[build  9/10] RUN npm test
      Test Files  221 passed (221)
           Tests  2933 passed (2933)
[build 10/10] RUN npm run build
====================
Starting Healthcheck
====================
[1/1] Healthcheck succeeded!
```

2,933 tests against 2,930 at the `5672af3f` checkpoint -- the three added by wave 11's web half. This
matters because `check:data-boundary` is the gate that has previously failed a plantgeo-main image on
a bare URL in a `src/**` comment while the backends deployed fine, so it is never assumed away.

## 2. Functional probes, production

### 2.1 /api/ready

GET https://plantgeo.aevani.com/api/ready -> 200 on every image observed. On the settled `d6ef9921`
image:

```
{"status":"ready","checks":{"configuration":true,"database":true,"redis":true},"timestamp":"2026-09-19T17:33:10.538Z"}
```

PASS. Identical shape to the `5672af3f` baseline. The build's own healthcheck against `/api/ready`
also succeeded (section 1.5).

### 2.2 Selected-day reads against the 5672af3f checkpoint

Direct parquet-api on `c377d349`, the baseline recipe
(`.../api/v1/parquet/day?layer=<L>&kind=observed&zoom=5&day=<D>&bbox=-124%2C47%2C-122%2C49`):

| Layer | Day | HTTP | state | requested_day | served_day | Rows | truncated | vs 5672af3f |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| drought | 2026-09-08 | 200 | published | 2026-09-08 | 2026-09-08 | 2 | false | **match** |
| burn-severity | 2026-09-11 | 200 | published | 2026-09-11 | 2026-09-11 | 2 | false | **match** |

burn-severity still carries its own `mtbs_snapshot` block; drought's top-level keys are exactly
`requested_day, rows, served_day, state, truncated` -- unchanged.

Through plantgeo-main's tRPC readers, on the settled `d6ef9921` image:

| Procedure | HTTP | state | requestedDay | servedDay | Rows | vs 5672af3f |
| --- | --- | --- | --- | --- | --- | --- |
| environmental.getDroughtClassification | 200 | ready | 2026-09-08 | 2026-09-08 | 2 | identical |
| environmental.getBurnSeverity | 200 | ready | 2026-09-11 | 2026-09-11 | 2 | identical |

`region_identity_mismatch` does not appear in either body.

### 2.3 Four further layer reads

| Procedure | HTTP | state | requestedDay | servedDay | Rows | vs 5672af3f |
| --- | --- | --- | --- | --- | --- | --- |
| environmental.getStreamflow | 200 | ready | 2026-09-19 | 2026-09-19 | 38 | identical |
| environmental.getSensorStations | 200 | ready | 2026-09-19 | 2026-09-19 | 18 | identical |
| environmental.getEvacuationZones | 200 | ready | 2026-09-19 | 2026-09-16 | 0 | identical |
| wildfire.getFireDetections | 200 | not_generated (`day_not_written`) | 2026-09-19 | n/a | n/a | identical |

**No layer panel reports differently on the wire.** All six reads were run twice: once at 16:47Z on
the `cc6c6868`-era image and again at 17:33Z on the settled `d6ef9921` image, with identical results
both times. The parquet-api booted clean and did not refuse at startup -- Sanic reached `Worker ready
[92]` at 16:34:39Z with no registry exception -- so the fire-risk schema change did not cost the
service its start.

### 2.4 Coverage -- 16 bindings, unchanged

GET https://plantgeo-parquet-api-production.up.railway.app/api/v1/parquet/coverage -> 200

```
coverage_schema_version = 3
layer_bindings length   = 16      <- same 16 as the 5672af3f checkpoint
region_slug             = "pnw"
region_display_name     = "Pacific Northwest"
evaluated_through_day   = 2026-09-19
```

All sixteen bindings, verbatim:

```
botanical-occurrences  -> bound_global    | gbif
burn-severity          -> bound_regional  | mtbs
drought                -> bound_regional  | usdm
evacuation-zones       -> bound_regional  | oregon_oem_arcgis
fire-detections        -> bound_global    | firms
fire-perimeters        -> bound_regional  | wfigs
fire-risk              -> unbound         | no_source_bound_in_region
land-context           -> unbound         | no_source_bound_in_region
sensors                -> bound_regional  | noaa_nws
signal                 -> bound_global    | era5_land_and_nasa_power
soil-survey            -> bound_regional  | ssurgo
vegetation             -> bound_global    | sentinel2_ndvi
water-gauges           -> bound_regional  | usgs_nwis
watersheds             -> bound_global    | hydrosheds
weather-forecast       -> unbound         | no_source_bound_in_region
weather-observations   -> bound_global    | open_meteo
```

`layer_bindings` is 16 rows, `region_slug` is `pnw`, and **land-context is still `unbound` with
`reason: "no_source_bound_in_region"`** -- all three as required. `fire-risk` is likewise still
`unbound`, which is the consistent reading: agri now declares the deterministic provenance set for a
stream nothing has published yet. The 14 -> 16 move happened at `31acd231` and belongs to the ML
session, not to this push.

**`Parquet region guard inert` appears ZERO times.** The census decodes, so the guard arms, which is
the correct observation. Six tRPC reads plus three repeat drought reads were driven through
plantgeo-main specifically to exercise `learnServedRegion` on the live image, and the line is absent
from that deployment's log and from every other service (section 5).

## 3. The weather-observations turn, in full

The 17:30:00Z bucket, dispatched on deployment `47a9cad6-e88f-4d14-8861-d64bf4db911d`
(commit `c377d349`). This is the FIRST weather turn on wave 11 -- the 16:30Z turn ran on the
pre-wave-11 `31acd231` image, because job-executor cut over at 16:33:40Z. Complete event chain,
verbatim:

```
2026-09-19T17:30:31.150132784Z  days_seen=["2026-09-19"] days_selected=["2026-09-19"]
  event="weather_observations_forward_fetch" fetched_at="2026-09-19T17:30:23.014181+00:00"
  observations_written=90 points_sampled=98 points_unavailable=8
  run_id="weather-observations-direct-forward-20260919T173023Z"

2026-09-19T17:30:31.651992137Z  days_deferred=[] days_owed=[] days_recovered=[]
  event="weather_observations_source_recovery_phase"
  run_id="weather-observations-direct-forward-20260919T173023Z" state="probed"

2026-09-19T17:30:51.653723560Z  event="weather_observations_source_retention"
  run_id="weather-observations-direct-forward-20260919T173023Z"
  source_checkpoints_attempted=90 source_checkpoints_failed=0 source_checkpoints_retained=90

2026-09-19T17:30:52.807885939Z  absence_overturned=null actual_z13_rows=1614 attempt=1
  bytes=22933 day="2026-09-19" detail="2026-09-19: derived z9 98 rows in 1 part(s), z5 98 rows in
  1 part(s), z0 6 rows in 1 part(s); 2026-09-19: availability extended: the generation now covers
  2026-09-19 at every required rung" event="weather_observations_forward_attempt"
  incoming_rows_verified=90 outcome="written" parts=1 rows=1614
  run_id="weather-observations-direct-forward-20260919T173023Z"
  tier_statuses={"0":"data","5":"data","9":"data","13":"data"}

2026-09-19T17:30:52.807890069Z  absence_overturned=null actual_z13_rows=1614 added_rows=90
  attempts=1 bytes=22933 day="2026-09-19" detail="2026-09-19: derived z9 98 rows in 1 part(s), z5 98
  rows in 1 part(s), z0 6 rows in 1 part(s); 2026-09-19: availability extended: the generation now
  covers 2026-09-19 at every required rung" event="weather_observations_forward_checkpoint"
  existing_rows=1524 incoming_rows=90 incoming_rows_verified=90 merged_rows=1614
  namespace="layer=weather-observations/kind=observed" outcome="written" parts=1 rows=1614
  run_id="weather-observations-direct-forward-20260919T173023Z" updated_rows=0

2026-09-19T17:30:52.807893969Z  absences_overturned=[] actual_z13_rows=1614
  availability_extended=1 availability_ladder_incomplete=0 availability_not_bootstrapped=0
  availability_quarantined_standing=0 availability_reindex_owed=0 availability_retry_claim_failed=0
  availability_retry_owed=0 availability_skipped_unchanged=0 bytes=22933 days=1 days_unwritten=0
  days_written=1 event="weather_observations_forward_complete" exit_code=0 incoming_rows=90
  incoming_rows_verified=90 merged_rows=1614 outcome="complete" outcomes={"written":1} parts=1
  recovery_phase={"days_deferred":[],"days_owed":[],"days_recovered":[],"state":"probed"}
  rows=1614 rows_added=90 rows_updated=0
  run_id="weather-observations-direct-forward-20260919T173023Z" source_checkpoints_attempted=90
  source_checkpoints_failed=0 source_checkpoints_retained=90 unwritten=[]
```

Each requirement, answered against that text:

**(a) A normal turn with NONE of the new refusal states.** `foreign_support_grid` does not appear.
Neither does `probe_budget_exhausted`, `recovery_degraded` or `no_retained_capture` -- all four are
ZERO across every production log on all three services (section 5). This is the correct observation:
the support grid has not changed, so the discriminator had nothing to discriminate and the OWED /
UNKNOWN / LOST fork was never entered.

**(b) The arithmetic reconciles.** `existing_rows=1524` + `incoming_rows=90` = `merged_rows=1614` =
`rows=1614` = `actual_z13_rows=1614`, with `added_rows=90`, **`updated_rows=0`** and `rows_updated=0`.
The 90 readings landed once each and nothing already published was rewritten.
`incoming_rows_verified=90` equals `incoming_rows`, all four rungs are `data`, `days_written=1`,
`days_unwritten=0`, `unwritten=[]`, `outcome="complete"`, `exit_code=0`.

**(c) Retention attempted equals retained, with zero failures.**
`source_checkpoints_attempted=90`, `source_checkpoints_retained=90`, **`source_checkpoints_failed=0`**,
on both the `weather_observations_source_retention` event and the terminal report. Attempted equals
`observations_written=90` from the fetch event, so every accepted point was checkpointed and every
checkpoint was proven retained. No `weather_observations_source_retention_failed` event was emitted.

**(d) `recovery_phase` names its state.**
`recovery_phase={"days_deferred":[],"days_owed":[],"days_recovered":[],"state":"probed"}` on the
terminal report, with the identical payload on the standalone
`weather_observations_source_recovery_phase` event at 17:30:31.652Z. `state="probed"` means the probe
ran to completion within budget and found nothing owed -- correct here, because 2026-09-19 was already
`data` at z0/z5/z9/z13 from the 16:30Z poll.

**(e) Wall time has not grown.** Measured fetch-start to terminal-report on the same lane:

| Turn | Revision | `fetched_at` | Wall | Probe phase | Readings | recovery state |
| --- | --- | --- | --- | --- | --- | --- |
| 15:30Z | 31acd231 (pre-wave-11) | 15:30:04.517Z | 28.55 s | 0.594 s | 90 | probed |
| 16:30Z | 31acd231 (pre-wave-11) | 16:30:03.910Z | 30.44 s | 0.500 s | 93 | probed |
| 17:30Z | **c377d349 (wave 11)** | 17:30:23.014Z | **29.79 s** | **0.502 s** | 90 | probed |

The wave-11 turn sits BETWEEN the two pre-wave-11 turns -- 1.24 s slower than one, 0.65 s faster than
the other -- which is ordinary provider variance, not growth. The probe phase (the gap between the
fetch event and the recovery-phase event) is 0.502 s against 0.594 s and 0.500 s: unchanged, as
expected, since the support-witness read replaces nothing in the ordinary no-day-owed path.

## 4. The job executor: 14 lanes, 12 active, the promotion lane still dark

### 4.1 Startup inventory on c377d349

The `plantgeo_job_executor_inventory` event emitted at container start (16:34:29.886Z) on deployment
`47a9cad6`, `mode: "active"`, `activation_variables: ["PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES"]`:

```
lane_count 14 | active 12

ACTIVE    burn-severity-direct-forward               55 8 * * *
ACTIVE    climate-nasa-power-direct-forward          40 * * * *
ACTIVE    drought-direct-forward                     45 * * * *
ACTIVE    evacuation-zones-direct-forward            35 * * * *
ACTIVE    fire-detections-direct-forward             15 * * * *
ACTIVE    fire-perimeters-direct-forward             10 * * * *
inactive  mtbs-forward                               55 7 * * 2
inactive  vegetation-ndvi-governed-plane-promotion   25 * * * *
ACTIVE    sensors-direct-forward                     20 * * * *
ACTIVE    soil-era5-land-direct-forward              50 * * * *
ACTIVE    vegetation-sentinel2-ndvi-direct-forward    5 * * * *
ACTIVE    water-gauges-direct-forward                15 * * * *
ACTIVE    watersheds-direct-forward                   0 3 * * *
ACTIVE    weather-observations-direct-forward        30 * * * *
```

**14 lanes registered, 12 active** -- unchanged from the `5672af3f` checkpoint. The promotion lane's
own entry, verbatim on the relevant fields:

```json
{
 "active": false,
 "lane_id": "vegetation-ndvi-governed-plane-promotion",
 "schedule": "25 * * * *",
 "command": ["python","-m","agri_data_service.execution.vegetation_partition_promotion"],
 "publication_lag_days": 7,
 "publication_lag_source": "pipeline/parquet/lane_registry.py vegetation contract",
 "writer_floor": "2026-09-06",
 "writer_ceiling": null,
 "source_watermark_parity": "not_evaluated"
}
```

`active: false` -- **the lane was NOT armed by this push and was not armed by this watch.** `mtbs-forward`
is likewise still `active: false`. `publication_lag_days: 7` and
`publication_lag_source: "pipeline/parquet/lane_registry.py vegetation contract"` are the registry
values the renamed `vegetation_promotion_declared_lag_days()` reads, and they are unchanged -- which is
the observable half of "the bound was renamed, not re-tuned."

Across 104 `plantgeo_job_executor_tick` events in the swept window: `lane_count=14` and
`active_lane_count=12` on every one, `incomplete_lanes=[]` on every one, and `failed=true` zero times.

### 4.2 No terminal report gained or lost a key

Every lane terminal report observed on `c377d349` was compared field-name-for-field-name against the
same report captured from deployment `79d9865e` (commit `31acd231`), the revision the `5672af3f`
checkpoint was taken on. Thirty-three distinct report shapes, **zero drift**:

| Report | baseline keys | post keys | Drift |
| --- | --- | --- | --- |
| weather_observations_forward_complete | 31 | 31 | none |
| weather_observations_forward_checkpoint | 18 | 18 | none |
| weather_observations_forward_attempt | 13 | 13 | none |
| weather_observations_forward_fetch | 8 | 8 | none |
| weather_observations_source_recovery_phase | 6 | 6 | none |
| weather_observations_source_retention | 5 | 5 | none |
| sensors_forward_complete | 28 | 28 | none |
| water_gauges_forward_complete | 28 | 28 | none |
| vegetation_forward_complete | 21 | 21 | none |
| climate_forward_complete | 17 | 17 | none |
| soil_forward_complete | 17 | 17 | none |
| evacuation_zones_forward_unchanged | 19 | 19 | none |
| evacuation_zones_forward_started | 10 | 10 | none |
| drought_forward_started | 10 | 10 | none |
| fire_detections_forward_started | 9 | 9 | none |
| fire_detections_forward_day_complete | 13 | 13 | none |
| fire_perimeters_forward_fetched | 11 | 11 | none |
| fire_perimeters_forward_resolved | 5 | 5 | none |
| fire_perimeters_forward_geometry_repaired | 3 | 3 | none |
| sensors_forward_attempt / _checkpoint / _fetch | 13 / 19 / 10 | 13 / 19 / 10 | none |
| water_gauges_forward_attempt / _checkpoint / _fetch | 13 / 19 / 11 | 13 / 19 / 11 | none |
| drought terminal (event-less) | 21 | 21 | none |
| vegetation terminal (event-less) | 20 | 20 | none |
| fire-detections terminal (event-less) | 20 | 20 | none |
| fire-perimeters terminal (event-less) | 31 | 31 | none |
| evacuation-zones terminal (event-less) | 18 | 18 | none |
| climate `product=all` terminal (event-less) | 16 | 16 | none |

One baseline event, `climate_forward_quota_pause` (7 keys), was NOT sampled post-push. It is a
conditional event, not a terminal report -- it fires only when the NASA POWER request budget is spent
mid-turn -- and its absence is the ordinary case, not a missing key. Recorded as not-sampled rather
than as a pass.

### 4.3 The drought lane, field for field

The 16:45:00Z bucket, dispatched 16:45:07Z on `c377d349`, verbatim:

```
event="drought_forward_started" first_day="2025-07-29" history_floor="2022-08-09"
  layer="drought" namespace="layer=drought/kind=observed/"
  run_id="drought-forward:ddcae2d4-34e3-4330-85d8-599a593862a9" selected_weeks=[]
  settled_through="2026-09-15" target_day=null target_forced=false

-> status="completed" days_published=0 remaining_window_backlog=[] results=[]
   availability_ladder_incomplete=0 availability_retry_owed=0 availability_reindex_owed=0
   tier_status_counts={"z0":{"absent":2,"conflict":0,"data":58,"incomplete":0,"missing":0},
                       "z5":{"absent":2,"conflict":0,"data":58,"incomplete":0,"missing":0},
                       "z9":{"absent":2,"conflict":0,"data":58,"incomplete":0,"missing":0},
                       "z13":{"absent":2,"conflict":0,"data":58,"incomplete":0,"missing":0}}
```

Field for field identical to the `5672af3f` checkpoint's 15:47Z walk: same `first_day="2025-07-29"`,
same `settled_through="2026-09-15"`, same empty `selected_weeks`, same `target_forced=false`, same
`status="completed"`, same `days_published=0`, same 58-`data`/2-`absent`/**0-`incomplete`** census at
every one of the four rungs. `DroughtForwardConfigError` appears zero times.

## 5. Log sweep

`railway logs` on the live deployment of each of the three platform services, filtered
case-insensitively. plantgeo-job-executor 718,514 bytes on `c377d349`; plantgeo-main 800 bytes on
`d6ef9921`; plantgeo-parquet-api 4,796 bytes on `c377d349`.

| Pattern | plantgeo-job-executor | plantgeo-main | plantgeo-parquet-api |
| --- | --- | --- | --- |
| Traceback | 0 | 0 | 0 |
| region_identity_mismatch | 0 | 0 | 0 |
| **Parquet region guard inert** | **0** | **0** | **0** |
| **foreign_support_grid** | **0** | **0** | **0** |
| recovery_degraded | 0 | 0 | 0 |
| probe_budget_exhausted | 0 | 0 | 0 |
| DroughtForwardConfigError | 0 | 0 | 0 |
| no_retained_capture | 0 | 0 | 0 |
| CRITICAL | 0 | 0 | 0 |
| ImportError | 0 | 0 | 0 |
| DeprecationWarning | 0 | 0 | 2 (pre-existing) |

**No lane newly reports `incomplete`.** Every occurrence of the string in the job-executor window was
enumerated and every one is a zero or an empty list:

```
     14  "availability_ladder_incomplete":0
     10  "consecutive_incomplete_buckets":0
      8  "incomplete":0                        (drought tier_status_counts, 4 rungs x 2 turns)
    104  "incomplete_lanes":[]
      0  outcome":"incomplete"
```

The two plantgeo-parquet-api DeprecationWarnings are the SAME pre-existing Sanic notice documented in
the 37963657, 0503ccd8, 3a548034 and 5672af3f checkpoints, and are byte-identical to the ones in the
pre-wave-11 deployment `0efe627c`:

```
/app/.venv/lib/python3.12/site-packages/sanic/logging/deprecation.py:33: DeprecationWarning:
[DEPRECATION v26.6] Passing the loop argument to listeners is deprecated. Your listener
'create_app.<locals>.setup_resources' should only accept the app argument.
```

No new warning class was introduced by this push.

**Attributed elsewhere, not a regression.** Two shortwave events appear on both services and are the
known NASA POWER upstream outage (`ALLSKY_SFC_SW_DWN` returning -999 from 2026-07-01 onward since
2026-09-18), NOT a wave-11 effect:

```
event="coverage_rollup_probe_deferred" layer="climate-field-shortwave-radiation"
  fault="AvailabilityUnavailableError"
event="availability_coverage_withheld" layer="climate-field-shortwave-radiation"
  code="availability_stale" reason="availability source ceiling 2026-06-24 precedes required 2026-09-03"
```

The ceiling of 2026-06-24 predates this push by three months. `climate-field-shortwave-radiation`
carries the verdict `coverage_withheld` in the boot tick's repair census; every other climate, soil
and drought product in that census is `complete`, `vegetation` is `repair_authorized` (a routine
gap-repair for 2026-09-01..2026-09-05, `outcome: "already_authored"`), and three lanes are
`no_repair_binding` / three `unreachable_by_forward_writer` by design.

## 6. What was NOT observable, stated plainly

This section exists so no reader mistakes silence for coverage. Three of the four things wave 11
changed CANNOT be exercised by a read-only production pass, and were not.

**6.1 The NDVI promotion turn was not observed, at all.** `vegetation-ndvi-governed-plane-promotion`
is `active: false` and was deliberately left that way. **No turn ran, so no promotion terminal report
of any status was produced, so the one-report-shape change and the `reason` key on all six terminal
statuses are UNOBSERVED in production.** What section 4.1 proves is strictly narrower: the lane is
still registered, still inactive, and still reads `publication_lag_days: 7` from the vegetation
registry contract. The rename's correctness and the six-status report shape are covered by
`tests/execution/test_vegetation_partition_promotion.py` (307 lines changed in `434bf6b5`) and by code
reading -- NOT by this checkpoint. Treat the promotion reporting as unexercised, not as verified.

**6.2 The region guard's inert path was not observed, and should not have been.** The correct
observation on a healthy deployment is the ABSENCE of `Parquet region guard inert`, and that is what
section 5 records. That absence proves the census decoded and the guard armed; it proves nothing about
the inert branch itself -- the rate-bounded log, the 60 s `REGION_LEARNING_RETRY_MS` back-off, the
failure memo, and `servedRegionGuardStatus()` reporting `armed: false` with an attempt count. Those
open only on a deployment whose coverage census fails to decode (the documented pre-bootstrap ~28 s
against an 8 s timeout), which a read-only pass cannot and must not induce in production. Covered by
unit tests and code reading.

**6.3 The `foreign_support_grid` path was not observed, and should not have been.** The support grid
has not changed, so `recover_weather_day` never reached the witness fork and the turn reported
`state="probed"` as an ordinary poll should. The three-way OWED / UNKNOWN / LOST refusal is reachable
ONLY from an operator `--recover-day` against a day whose witness names a different
`weather_support_sha256` -- which requires moving `INGEST_BBOX` or the sample spacing, a production
mutation this pass is forbidden to make. Also unexercised for the same structural reason:
`recovery_phase` states `failed` and `skipped_no_budget`, the per-day `probe_budget_exhausted`
verdict, the `recovery_degraded` incomplete bucket, and the
`weather_observations_source_retention_failed` event, all of which need a genuinely faulting or
genuinely slow object store.

**6.4 Fire-risk publishes nothing, so no row was read.** The deterministic provenance set
(`quantile = "point"`, `ensemble_size = 1`) is declared but unexercised on the wire: `fire-risk` is
`unbound` in coverage with `no_source_bound_in_region`, and no parquet day exists to read. What was
verified is that agri's reader copy no longer diverges from the ML writer -- proven by
`test_ml_schema_parity` flipping green in the push's own receipt -- and that declaring it cost the
parquet-api nothing at startup. The schema is NOT verified against a real published fire-risk
partition, because there is none.

**6.5 The client-side half of the web change is unexercised.** A server probe issues one independent
request per call and can never hold a retained frame, so the viewport-read liveness behaviour is
covered by `src/__tests__/hooks/useViewportProxiedLayers.test.ts` and the `no-restricted-syntax` build
gate (both inside the 2,933 tests that passed in the serving image), not by this checkpoint.

## 7. Read-only discipline and process hygiene

- No local run of the application, no `npm run dev`, no local container.
- No Railway variable read for its value, written, or printed. No secret in this document.
- No lane armed. No operator `--target-day`, `--recover-day` or promotion invocation. No production
  mutation of any kind. Nothing committed.
- Four background processes were started and all four were reaped. The deploy watch ran its 25 polls,
  printed `WATCH_DONE` and exited 0 on its own at 16:58:51Z. The weather-turn waiter re-resolved the
  live job-executor deployment on every round (so a mid-watch redeploy would have re-pointed rather
  than yielding stale data), found the 17:30Z turn at 17:32:07Z and exited 0. TWO log collectors were
  found running against the same output -- one tracked, and one launched with `&` that survived
  despite appearing dead -- and both process trees were killed explicitly (`kill -9` on each shell and
  its `sleep` child) and confirmed gone from the process table. A leftover shell from an earlier
  timed-out `grep` was reaped in the same pass. No `railway` child process of this session remains.
- Deployment re-pointing was exercised for real: plantgeo-main's `c377d349` deployment was REMOVED
  mid-build and plantgeo-main/martin moved four more times during the watch. Every probe in sections
  2 and 5 was taken against the deployment that was live at the time of the probe, and the settled
  `d6ef9921` image was re-probed at 17:33Z to confirm the readings had not moved.

## Verdict

**PASS.** All four gate services carry wave 11 `c377d349ca0b55e8cd75c65a811aa55b09c9b000`:
plantgeo-job-executor and plantgeo-parquet-api are live ON `c377d349` itself, plantgeo-main and
plantgeo-martin on `d6ef9921`, proven to contain it. No gate service is or was on a revision older
than `5672af3f`. Every SKIP in the sequence is a legitimate `"No changes to watched files"` on an
ML-service or docs commit outside the skipping service's watch patterns. plantgeo-main built clean
with 2,933 tests passing and a succeeding `/api/ready` healthcheck.

**This push returned main to green**: the cross-service fire-risk schema divergence -- agri declaring
a `float64` quantile while the ML service writes the string `"point"` per the same-day lane-contract
amendment -- is closed by `202ac24d`, and `c377d349`'s own receipt records the sweep at 4,627 passed /
89 skipped / 1 xfailed / **0 failed**, a delta of exactly +2 passed / -2 failed against the blocked
pass, being the two `test_ml_schema_parity` assertions flipping green and nothing else moving.

What is observable answered clean. The job executor still registers **14 lanes with 12 active**, with
`vegetation-ndvi-governed-plane-promotion` still `active: false`, and **no other lane's terminal
report gained or lost a single key** across 33 distinct report shapes compared against the `5672af3f`
checkpoint revision. The weather-observations lane completed its 17:30Z turn -- the first on wave 11 --
with `exit_code=0`, `outcome="complete"`, row arithmetic reconciling exactly (1524 + 90 = 1614,
`updated_rows=0`), retention `attempted=90 / retained=90 / failed=0`, `recovery_phase` naming
`state="probed"`, and NONE of `foreign_support_grid`, `recovery_degraded` or `probe_budget_exhausted`
anywhere in production; wall time 29.79 s sits between the two pre-wave-11 turns (28.55 s and 30.44 s).
The drought lane ran field-for-field identically with `"incomplete":0` at every rung. `/api/ready` 200;
drought 2026-09-08 and burn-severity 2026-09-11 reads are row-for-row identical to the checkpoint on
both the direct parquet-api and the plantgeo-main tRPC path; four further layer reads are identical;
`/api/v1/parquet/coverage` still answers 16 `layer_bindings` at `region_slug: "pnw"` with land-context
`unbound`; the parquet-api booted clean and its stream registry did not refuse. The log sweep is clean
on all three services apart from the one pre-existing Sanic DeprecationWarning, with **zero**
`Parquet region guard inert` -- the correct observation, since a healthy census decodes and arms the
guard.

**NOT OBSERVABLE (section 6), and not claimed as covered:** the NDVI promotion turn and therefore the
entire one-report-shape / `reason`-on-all-six-statuses change; the region guard's inert path, its
back-off, its memo and `servedRegionGuardStatus()`; the weather `foreign_support_grid` verdict and the
OWED / UNKNOWN / LOST refusal, along with the `failed`, `skipped_no_budget` and
`probe_budget_exhausted` recovery states; the fire-risk deterministic provenance set against a real
published partition, since nothing publishes fire-risk; and the client-side half of the web viewport
change. Each needs an armed lane, a faulting store, an operator mutation, a published stream or a
mounted React hook -- none of which a read-only production pass can or should produce. They are
covered by unit tests and code reading, and are recorded here as unexercised, not as verified.

**ATTRIBUTED ELSEWHERE, not a regression:** `climate-field-shortwave-radiation` reports
`availability_stale` with an availability ceiling of 2026-06-24, and is the known NASA POWER upstream
outage from 2026-09-18, three months older than this push. Coverage `layer_bindings` is 16 including
`fire-risk` and `weather-forecast`, both arriving with the ML session's `31acd231` and already present
at the `5672af3f` checkpoint.

**plantgeo-ml (informational, another session's service, not in this gate):** its `c377d349`
deployment `1110b184` resolved SKIPPED with `skippedReason: "No changes to watched files"`; its
`cc6c6868` build FAILED and its own session's follow-up `d6ef9921` then deployed SUCCESS
(`e74a2053-a4f4-4c41-a6e4-f77d96d2b7ff`), which is where it is live. No action was taken on it.
