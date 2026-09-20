---
type: evidence
track: platform_experience_qa_20260911
wave: 12
commit: 3b052766
base: d6ef9921
observed: 2026-09-19T18:05Z - 2026-09-19T18:35Z
verdict: PASS
---

# Release checkpoint -- wave 12, `3b052766`

Deploy watch for PlantGeo wave 12, pushed to `origin/main` as `3b052766ff946e685f2ef5766b4b40e2dbf0437b`
from the pre-wave base `d6ef9921`. Railway CLI only -- the Railway MCP was not authenticated for this
session, so every platform fact below comes from `railway status --json`, `railway logs` and direct
HTTP against the production hostnames.

**Verdict: PASS.** All four gate services carry `3b052766` exactly. No service is on a revision older
than the pre-wave base. Every sanity read is byte-identical to the pre-wave values. Two of the four
wave-12 changes were exercised in production; the other two are recorded in "Not observable" as
UNPROVEN rather than passing.

## 1. The gate -- four services on `3b052766`

Containment was proved rather than assumed, per the standing rule that `plantgeo-main`'s build has
twice been superseded mid-flight. On this wave no supersession happened: every gate service landed the
exact wave sha, so `git merge-base --is-ancestor 3b052766 <serving-sha>` is satisfied trivially and
identically for all four.

| Service | Deployment ID | Status | Serving sha | Contains `3b052766` | `skippedReason` |
| --- | --- | --- | --- | --- | --- |
| `plantgeo-martin` | `da94255a-de22-4fd6-a911-618a20ff1e81` | SUCCESS | `3b052766` | yes (exact) | none |
| `plantgeo-main` | `813f2c32-b6b8-49af-b9da-b8d7f11a9ff4` | SUCCESS | `3b052766` | yes (exact) | none |
| `plantgeo-job-executor` | `3c0e9a33-fdd3-45df-82d3-60730743d1df` | SUCCESS | `3b052766` | yes (exact) | none |
| `plantgeo-parquet-api` | `bb067e37-c2ca-4f31-b986-83825d1346d8` | SUCCESS | `3b052766` | yes (exact) | none |

**No `SKIPPED` deployment occurred on this wave.** All four are real builds that reached SUCCESS --
there is no "parked on an older-but-containing sha" case to explain here, because wave 12 touched
both the TypeScript tree (`src/lib/server/services/parquet-plane-client.ts`) and the Python tree
(`services/agri-data-service/**`), so every watched-file set matched.

**No service is older than `d6ef9921`.** The two services that were still serving a pre-wave image at
the start of the watch -- `plantgeo-main` on `d6ef9921` and `plantgeo-parquet-api` on `c377d349`,
both of which are ancestors of `3b052766` -- rolled forward during the watch and are recorded above at
their settled revisions.

Timeline: all four builds were dispatched at `2026-09-19T18:05:34.748Z`. `plantgeo-martin`,
`plantgeo-job-executor` and `plantgeo-parquet-api` reached SUCCESS by 18:08:06Z. `plantgeo-main`
(the Next.js image, which runs the full 2,937-test suite inside the build) reached SUCCESS between
18:10:32Z and 18:11:21Z. Five poll rounds, all terminal. **No build failed, and no build exhibited the
empty/8-second Dockerfile-parse-error signature.**

### 1.1 `plantgeo-ml` -- informational only, not part of the gate

`plantgeo-ml` is SUCCESS on deployment `e74a2053-a4f4-4c41-a6e4-f77d96d2b7ff`, commit **`d6ef9921`**,
created `2026-09-19T16:46:07.339Z`. That is the pre-wave base -- an ancestor of `3b052766`, not older
than it in the sense that matters, and NOT a miss: this service belongs to a concurrent session and was
neither acted on, redeployed, nor judged here. Recorded for situational awareness only.

### 1.2 Build gates

`plantgeo-main` build log:

```
[INFO]  Test Files  221 passed (221)
[INFO]       Tests  2937 passed (2937)
Starting Healthcheck
[1/1] Healthcheck succeeded!
```

2,937 tests, up from the 2,933 recorded at the wave-11 `c377d349` checkpoint. The delta of +4 is
consistent with the wave-12 additions to `src/__tests__/services/parquet-plane-client.test.ts`
(+127 lines). The image's own healthcheck against `/api/ready` succeeded, which is an independent
confirmation of section 3.1.

`plantgeo-parquet-api` build log, the agri quality-receipt stage:

```
[INFO] quality receipt verified: sha256:0ea35d3d173e99ec8f7843a2a5c00971f455484d1e114f614e791b73871d2a2f
       over 867 files, generated 2026-09-19T18:04:01.145692Z
```

The receipt's generation timestamp (18:04:01Z) sits just before the push, matching `3b052766`'s own
commit message, `build(agri): refresh verified Python receipt for wave 12`. **Read this for what it
is:** a green receipt proves the digested tree matches the recorded digest -- it is a staleness check,
not a test result, and it does not assert that the Python suite passed in this image.

## 2. What wave 12 changed

`d6ef9921..3b052766` is 12 commits / 20 files / +1,901 -383, in four lanes:

| # | Lane | Service | Key files | Observable in prod? |
| --- | --- | --- | --- | --- |
| 1 | Weather recovery witness | `plantgeo-job-executor` | `pipeline/direct/weather_observations/{forward,recovery}.py` | **yes** -- section 4 |
| 2 | Promotion staleness bound | `plantgeo-job-executor` | `execution/lane_specs.py`, `execution/vegetation_partition_promotion.py` | **partially** -- section 5 |
| 3 | Region guard logging | `plantgeo-main` | `src/lib/server/services/parquet-plane-client.ts` | **negative only** -- section 6 |
| 4 | ML schema golden fixtures | (tests) | `tests/parquet/test_ml_schema_parity.py`, 3 JSON fixtures | **no** -- section 7 |

## 3. Sanity sweep

### 3.1 `/api/ready`

`GET https://plantgeo.aevani.com/api/ready -> 200`, on both the pre-wave `d6ef9921` image and the
settled `3b052766` image:

```
{"status":"ready","checks":{"configuration":true,"database":true,"redis":true},"timestamp":"2026-09-19T18:11:41.283Z"}
```

PASS. Shape identical to the `c377d349` baseline.

`GET https://plantgeo-martin-production.up.railway.app/health -> 200`.

### 3.2 Known-good reads -- all UNCHANGED

Every read below was run TWICE: once at 18:07Z against the pre-wave `d6ef9921` main image, and again
at 18:11Z against the settled `3b052766` image. **Both passes returned identical values**, so the
comparison is against this wave's own before/after as well as against the `c377d349` checkpoint.

Direct parquet-api (`/api/v1/parquet/day?layer=<L>&kind=observed&zoom=5&day=<D>&bbox=-124,47,-122,49`):

| Layer | Day | HTTP | state | requested | served | Rows | truncated | vs pre-wave |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| drought | 2026-09-08 | 200 | published | 2026-09-08 | 2026-09-08 | **2** | false | **match** |
| burn-severity | 2026-09-11 | 200 | published | 2026-09-11 | 2026-09-11 | **2** | false | **match** |

Top-level keys unchanged: drought is exactly `requested_day, rows, served_day, state, truncated`;
burn-severity carries its extra `mtbs_snapshot`. No shape drift.

Through `plantgeo-main`'s tRPC readers, on the settled `3b052766` image:

| Procedure | HTTP | state | requestedDay | servedDay | Rows | vs pre-wave |
| --- | --- | --- | --- | --- | --- | --- |
| `environmental.getDroughtClassification` | 200 | ready | 2026-09-08 | 2026-09-08 | **2** | identical |
| `environmental.getBurnSeverity` | 200 | ready | 2026-09-11 | 2026-09-11 | **2** | identical |
| `environmental.getStreamflow` | 200 | ready | 2026-09-19 | 2026-09-19 | **38** | identical |
| `environmental.getSensorStations` | 200 | ready | 2026-09-19 | 2026-09-19 | **18** | identical |
| `environmental.getEvacuationZones` | 200 | ready | 2026-09-19 | **2026-09-16** | **0** | identical |
| `wildfire.getFireDetections` | 200 | `not_generated` (`day_not_written`) | 2026-09-19 | n/a | n/a | identical |

All six expected values from the brief are matched exactly, including evacuation's `0` rows at
`servedDay 2026-09-16` (a served day three behind the requested day, which is the correct governed
answer, not a gap) and fire-detections' `not_generated`/`day_not_written`.

`region_identity_mismatch` appears in no response body.

### 3.3 Coverage census -- unchanged

`GET https://plantgeo-parquet-api-production.up.railway.app/api/v1/parquet/coverage -> 200`

```
coverage_schema_version = 3
layer_bindings length   = 16      <- same 16 as the c377d349 checkpoint
region_slug             = "pnw"
region_display_name     = "Pacific Northwest"
evaluated_through_day   = 2026-09-19
```

`environmental.getSliderCapabilities` through `plantgeo-main` -> 200, **21 layers**, unchanged.

## 4. Change 1 -- the weather recovery witness

Wave 12 stopped treating the support witness as a GATE and made it EVIDENCE. From
`git show 3b052766:.../weather_observations/recovery.py`, `recover_weather_day`:

> `witness is read first, in one object read, but nothing is refused on it: EVERY day is walked,`
> `and the verdict only names which empty answer an exhausted walk earned. foreign_support_grid`
> `needs both halves -- a walk of this grid that recovered nothing, and a witness naming a`
> `different one -- because the witness set is a lower bound on the grids a day was polled under`

The pre-wave code returned `foreign_support_grid` *before* walking a single point when the witness
named another grid, which meant one swallowed witness write could refuse a recovery whose bodies were
sitting under the searched key all along. Three further consequences of the change, all confirmed in
the `3b052766` source:

- The enum member `grid_changed` was renamed `other_grids_witnessed` -- the name no longer asserts a
  move, only that another grid is on the record.
- The LOSS claim's selector was inverted from `witness.verdict == "no_witness"` to
  `witness.verdict == "grid_matches"`: a loss is now claimed only on positive evidence that the
  searched grid really is the one the day was polled under. Everything else falls to UNKNOWN.
- A new `truncated` property reports when the witness set has hit `WEATHER_SUPPORT_WITNESS_LIMIT = 8`
  and may have evicted an older digest, so a `foreign_support_grid` refusal says its grid list may be
  incomplete.

`WeatherRecoveryState` is now the five-member literal
`complete_capture | partial_capture | no_retained_capture | probe_budget_exhausted | foreign_support_grid`.

### 4.1 The observed turn -- 18:30:02Z, on `3b052766`

The `18:30:00Z` bucket, `run_id="weather-observations-direct-forward-20260919T183002Z"`, dispatched
on the wave-12 job-executor deployment `3c0e9a33-fdd3-45df-82d3-60730743d1df`. This is the FIRST
weather turn on wave 12 -- the 17:30Z turn ran on the pre-wave `c377d349` image. Complete event
chain, verbatim:

```
2026-09-19T18:30:10.108257189Z  days_seen=["2026-09-19"] days_selected=["2026-09-19"]
  event="weather_observations_forward_fetch" fetched_at="2026-09-19T18:30:02.167088+00:00"
  observations_written=93 points_sampled=98 points_unavailable=5
  run_id="weather-observations-direct-forward-20260919T183002Z"

2026-09-19T18:30:10.412279305Z  days_deferred=[] days_owed=[] days_recovered=[]
  event="weather_observations_source_recovery_phase"
  run_id="weather-observations-direct-forward-20260919T183002Z" state="probed"

2026-09-19T18:30:30.580733270Z  event="weather_observations_source_retention"
  run_id="weather-observations-direct-forward-20260919T183002Z"
  source_checkpoints_attempted=93 source_checkpoints_failed=0 source_checkpoints_retained=93

2026-09-19T18:30:30.580736840Z  absence_overturned=null actual_z13_rows=1707 attempt=1 bytes=23726
  day="2026-09-19" detail="2026-09-19: derived z9 98 rows in 1 part(s), z5 98 rows in 1 part(s),
  z0 6 rows in 1 part(s); 2026-09-19: availability extended: the generation now covers 2026-09-19
  at every required rung" event="weather_observations_forward_attempt" incoming_rows_verified=93
  outcome="written" parts=1 rows=1707
  run_id="weather-observations-direct-forward-20260919T183002Z"
  tier_statuses={"0":"data","5":"data","9":"data","13":"data"}

2026-09-19T18:30:30.580739730Z  absence_overturned=null actual_z13_rows=1707 added_rows=93
  attempts=1 bytes=23726 day="2026-09-19" detail="..." event="weather_observations_forward_checkpoint"
  existing_rows=1614 incoming_rows=93 incoming_rows_verified=93 merged_rows=1707
  namespace="layer=weather-observations/kind=observed" outcome="written" parts=1 rows=1707
  run_id="weather-observations-direct-forward-20260919T183002Z" updated_rows=0

2026-09-19T18:30:30.580743460Z  absences_overturned=[] actual_z13_rows=1707
  availability_extended=1 availability_ladder_incomplete=0 availability_not_bootstrapped=0
  availability_quarantined_standing=0 availability_reindex_owed=0 availability_retry_claim_failed=0
  availability_retry_owed=0 availability_skipped_unchanged=0 bytes=23726 days=1 days_unwritten=0
  days_written=1 event="weather_observations_forward_complete" exit_code=0 incoming_rows=93
  incoming_rows_verified=93 merged_rows=1707 outcome="complete" outcomes={"written":1} parts=1
  recovery_phase={"days_deferred":[],"days_owed":[],"days_recovered":[],"state":"probed"}
  rows=1707 rows_added=93 rows_updated=0
  run_id="weather-observations-direct-forward-20260919T183002Z" source_checkpoints_attempted=93
  source_checkpoints_failed=0 source_checkpoints_retained=93 unwritten=[]
```

### 4.2 Each requirement, answered against that text

**(a) No `foreign_support_grid` on a healthy day.** It does not appear. Neither does
`probe_budget_exhausted`, `no_retained_capture`, `partial_capture`, `recovery_degraded` nor the new
`witness_truncated` -- all **0** across every production log on all three services (section 9). This
is the correct observation for a day whose bodies are exactly where the configured grid says they
are: the walk is not being *skipped*, it is completing and finding what it expects.

**(b) `recovery_phase` present.** Twice -- as its own event at 18:30:10.412Z
(`state="probed"`, all three day-lists empty) and again inside the terminal report as
`recovery_phase={"days_deferred":[],"days_owed":[],"days_recovered":[],"state":"probed"}`. Shape
identical to the pre-wave `c377d349` turn. **No report-shape drift.**

**(c) Row arithmetic consistent.** Every identity holds:

| Identity | Values | Holds |
| --- | --- | --- |
| `existing + incoming = merged` | 1614 + 93 = **1707** | yes |
| `updated_rows = 0` | `updated_rows=0`, `rows_updated=0` | yes |
| `added_rows = incoming_rows` | 93 = 93 | yes |
| `merged_rows = rows = actual_z13_rows` | 1707 = 1707 = 1707 | yes |
| `incoming_rows_verified = incoming_rows` | 93 = 93 | yes |
| `sampled = written + unavailable` | 98 = 93 + 5 | yes |

Note the continuity across the wave boundary: this turn's `existing_rows=1614` is **exactly** the
pre-wave 17:30Z turn's `merged_rows=1614`. The wave-12 image picked up the day's partition precisely
where the wave-11 image left it -- no double-write, no lost rows, no re-statement of existing rows
(which is what `updated_rows=0` asserts).

**(d) Retention counts clean.** `source_checkpoints_attempted=93`, `failed=0`, `retained=93` --
every body retained, nothing swallowed. This matters more than usual for this change, because a
swallowed witness write is precisely the fault the wave-12 rewrite was hardening against.

**(e) Wall time -- NO regression; this turn was the FASTEST of the four.** Measured `fetched_at` to
`weather_observations_forward_complete` across the last four turns:

| Turn | Image | Wall time |
| --- | --- | --- |
| 13:30:08Z | pre-wave `3a548034`-era | 29.19s |
| 15:30:04Z | pre-wave `5672af3f`-era | 28.55s |
| 17:30:23Z | pre-wave `c377d349` | 29.79s |
| **18:30:02Z** | **wave 12 `3b052766`** | **28.41s** |

The brief flagged this as the number to watch, because the walk now runs on days the pre-wave code
would have refused for free. At **28.41s** the wave-12 turn is 1.38s FASTER than the immediately
preceding turn and below the entire 28.55-29.79s pre-wave range. **No regression.**

The honest reading of *why*: on a healthy day the witness returns `grid_matches`, and the pre-wave
code did not short-circuit that case either -- only the `other_grids_witnessed` case was skipped, and
it does not arise here. So this turn does not actually exercise the extra walking; it demonstrates
that the change costs a healthy turn nothing. The walk's cost on a day that WOULD have been skipped
is not measured by this turn. See section 8.

**(f) Turn outcome.** `exit_code=0`, `outcome="complete"`, `days_written=1`, `days_unwritten=0`,
`unwritten=[]`, `absences_overturned=[]`, and every `availability_*` counter zero except
`availability_extended=1`. All four rungs carry data (`tier_statuses={"0":"data","5":"data",
"9":"data","13":"data"}`). A textbook turn.

### 4.3 Job-executor liveness

12 `plantgeo_job_executor_tick` events between 18:06:28Z and 18:12:18Z, spaced ~30s -- the expected
cadence, no gaps. A `fire-perimeters-direct-forward` turn also completed on the wave-12 image
(`event="fire_perimeters_forward_resolved" state="current"`, plus `_fetched` and
`_geometry_repaired`), so weather is not the only lane proven to run here.

## 5. Change 2 -- the promotion staleness bound

The change replaces a derived bound with a literal one. Before, `lane_specs.py` carried
`VEGETATION_PROMOTION_STALE_CEILING_LAG_ALLOWANCES = 2` and multiplied it back by the vegetation lane's
registered `publication_lag_days`; after, the bound is a stated day count that the lane owns outright.
From the diff's own rationale:

> `1. The number a reader met was 2 and the bound the code enforced was 3 x lag = 21 days, because`
> `   counting past the declared-lag day spends one whole lag before the counter starts.`
> `2. publication_lag_days ... re-measurable at will ... to 10 silently moved this safety bound from`
> `   21 days to 30, with a GREEN suite, because the boundary tests were themselves written as`
> `   lag * (allowances + 1) and tracked the change`

So **21 is not a new ceiling -- it is the value the bound already enforced**, restated so that
re-measuring the lane's publication lag can no longer move a safety bound. This is behaviour-preserving
at today's `publication_lag_days = 7` and defensive against a future re-measurement.

### 5.1 Lane inventory -- `lane_count 14 | active 12`, NDVI inactive

From `plantgeo-job-executor`'s startup inventory (`event="plantgeo_job_executor_inventory"`, emitted
`2026-09-19T18:06:27.034653112Z` on the `3b052766` image), parsed in full:

```
lane_count 14   active 12
  fire-detections-direct-forward             active=True   lag=2   sched=15 * * * *
  water-gauges-direct-forward                active=True   lag=2   sched=15 * * * *
  mtbs-forward                               active=False  lag=7   sched=55 7 * * 2
  climate-nasa-power-direct-forward          active=True   lag=6   sched=40 * * * *
  soil-era5-land-direct-forward              active=True   lag=9   sched=50 * * * *
  vegetation-sentinel2-ndvi-direct-forward   active=True   lag=7   sched=5 * * * *
  vegetation-ndvi-governed-plane-promotion   active=False  lag=7   sched=25 * * * *   <- UNARMED
  weather-observations-direct-forward        active=True   lag=2   sched=30 * * * *
  drought-direct-forward                     active=True   lag=4   sched=45 * * * *
  fire-perimeters-direct-forward             active=True   lag=0   sched=10 * * * *
  sensors-direct-forward                     active=True   lag=1   sched=20 * * * *
  watersheds-direct-forward                  active=True   lag=0   sched=0 3 * * *
  evacuation-zones-direct-forward            active=True   lag=0   sched=35 * * * *
  burn-severity-direct-forward               active=True   lag=None sched=55 8 * * *
```

**`lane_count 14 | active 12` confirmed, matching the expected inventory exactly.** The two inactive
lanes are `mtbs-forward` and `vegetation-ndvi-governed-plane-promotion` -- the latter is the NDVI
promotion lane the staleness bound governs, and it is `active=False` as expected.

**Report shape did not drift.** Every lane record still carries the full key set --
`lane_id, active, cadence_seconds, catch_up_policy, checkpoint, command, command_timeout_seconds,
conflicts_with, dead_letter_visibility, executable, lease_seconds, max_attempts,
migration_disposition, phase_offset_seconds, publication_cadence_days, publication_lag_days,
publication_lag_source, retry_policy, rollback, schedule, selection_policy,
source_watermark_parity, work_class, writer_ceiling, writer_floor` -- and
`activation_variables=["PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES"]` is unchanged. Note in particular that
`publication_lag_days` is still REPORTED on the vegetation lane (`lag=7`); the wave-12 change
decoupled the staleness bound FROM that field, it did not remove the field.

**What this does not prove:** because the promotion lane is unarmed, the 21-day ceiling was never
evaluated against a real ceiling in production. See section 8.

## 6. Change 3 -- the region guard's back-off memo

> **Provenance note.** The local checkout at `C:\Users\atooz\Programming\plantgeo` is on `d6ef9921`,
> the PRE-WAVE base -- it had not been pulled to `3b052766`. Every source excerpt in this document is
> therefore read with `git show 3b052766:<path>` against the fetched object, never from the working
> tree. This was caught mid-watch; an earlier draft of this section had quoted the pre-wave form of
> the function below, which is the wrong code and has been replaced.

The guard lives in `src/lib/server/services/parquet-plane-client.ts`. Wave 12 moved the back-off memo
so it is taken BEFORE the census await, which bounds a failing region learn to one log line per
`REGION_LEARNING_RETRY_MS` window instead of one per row read, and adds a second counter for the
reads that passed unguarded behind the in-flight attempt.

`git show 3b052766:src/lib/server/services/parquet-plane-client.ts`, `learnServedRegion` at `:977`.
The memo is stamped on ENTRY, at `:987-992`, *before* the `await` at `:994`:

```js
async function learnServedRegion(): Promise<void> {
  const previous = regionLearningFailure;
  const startedAt = Date.now();
  if (previous !== null && startedAt - previous.at < REGION_LEARNING_RETRY_MS) {
    // Inside the window: the attempt this read would have made is already spent or in flight, so
    // it costs no census and writes no line. It is still counted, and the line that attempt does
    // write states how many arrived behind it.
    regionLearningFailure = { ...previous, unguardedRowReads: previous.unguardedRowReads + 1 };
    return;
  }
  const stamped = {
    at: startedAt,
    attempts: (previous?.attempts ?? 0) + 1,
    unguardedRowReads: (previous?.unguardedRowReads ?? 0) + 1,
  };
  regionLearningFailure = stamped;
  try {
    await getParquetWarehouseCoverage();
  } catch { /* ... the attempt was recorded on entry, so the retry stays bounded either way. */ }
```

and the single emitting site, which states both counters:

```js
  const settled = regionLearningFailure ?? stamped;
  regionLearningFailure = { ...settled, at: Date.now() };
  console.error(
    "Parquet region guard inert: no census has stated a region; row reads proceed unguarded",
    {
      failedLearningAttempts: regionLearningFailure.attempts,
      unguardedRowReads: regionLearningFailure.unguardedRowReads,
      compiledRegionSlug: getRegion().slug,
      retryAfterMs: REGION_LEARNING_RETRY_MS,
    }
  );
```

That is exactly the described shape: **once per window, two counters**. The early return at `:984`
is what collapses the old once-per-row-read storm into a single line, and `unguardedRowReads` is the
number that makes the collapsed reads countable rather than merely silent.

`console.error` goes to the container's stderr, which Railway captures into the deploy log -- so an
absence in that log is a real signal about this code path, not merely an absence of instrumentation.

### 6.1 Observed zero AFTER forcing the code path

A passively observed zero would prove nothing, so the row-read path was driven deliberately first, on
the settled `3b052766` image:

- the six sanity procedures of section 3.2, re-run post-deploy at 18:11:41Z; plus
- **21 further row reads** -- seven procedures (`getDroughtClassification`, `getBurnSeverity`,
  `getStreamflow`, `getSensorStations`, `getEvacuationZones`, `getVegetationIndex`, `getSoilField`)
  at each of zoom 4, 5 and 6; plus
- **216 further row reads** at 18:18-18:20Z -- nine procedures (the seven above plus
  `getFirePerimeters` and `wildfire.getFireDetections`) at each of zoom 3, 5, 7 and 9, repeated six
  times.

Every one of those 243 requests returned HTTP 200. That is **243 row reads through `plantgeo-main`
on the wave-12 image**, each of which calls `assertServedRegionMatchesBundle()`. Logs were re-fetched
(`railway logs -s plantgeo-main -d --lines 2000`) twice, AFTER each batch completed.

| Pattern | Count in `plantgeo-main` deploy log, post-drive |
| --- | --- |
| `Parquet region guard inert` | **0** |
| `failedLearningAttempts` | **0** |
| `unguardedRowReads` | **0** |
| `region_identity_mismatch` | **0** |
| `Parquet row read refused` | **0** |
| `Traceback` / `ERROR` / `CRITICAL` | **0 / 0 / 0** |

**This is the expected result on a correctly configured deployment**, and it is an *observed-after-forcing*
zero, not a passive one. The census decodes in production (section 3.3 shows it answering with
`region_slug: "pnw"`), so the guard arms on the first read and `learnServedRegion`'s failure branch --
the branch wave 12 edited -- is never entered.

**Honest limit on this evidence.** The zero proves the change did not introduce spurious logging on the
healthy path. It does NOT prove the once-per-window behaviour itself, which only manifests when the
census fails to decode. See section 8.

`plantgeo-main`'s deploy log is 8 lines total (container start, drizzle migration check, Next.js
ready) -- Next.js does not log successful requests -- so the zero above is an absence of `console.error`
output specifically, which is exactly the channel the guard writes to.

## 7. Change 4 -- ML schema golden fixtures

Wave 12 added `tests/parquet/test_ml_schema_parity.py` (+400/-…), three JSON fixtures under
`tests/fixtures/ml_schema_parity/` (`expert-labels.json`, `fire-risk.json`, `weather-forecast.json`)
and `scripts/regenerate_ml_schema_fixtures.py` (+314), so that a cross-service schema change stales
the quality receipt rather than passing silently.

**This is test-only and is NOT observable in production.** Nothing in the running services exercises
these fixtures. The only production-adjacent signal is that the agri quality receipt verified over 867
files (section 1.2), which confirms the fixtures are present in the image and digested -- it does not
confirm the parity test passed. Recorded in section 8 as unproven.

## 8. Not observable

Everything in this section is **UNPROVEN**, not passing. It is listed so no reader mistakes a clean
watch for full coverage of wave 12.

1. **The region guard's inert path (change 3).** The whole point of the edit is what happens when the
   census never decodes: the memo is taken before the await so the log fires once per back-off window
   with two counters, instead of once per row read. Production's census decodes, so this branch was
   never entered under any of the 27 forced row reads. What was proved is the *negative*: the change
   introduced no logging on the healthy path. The once-per-window bound, the
   `failedLearningAttempts`/`unguardedRowReads` counter values, and the back-off timing are
   **unverified in production** and rest on the unit tests added in
   `src/__tests__/services/parquet-plane-client.test.ts` (+127 lines, part of the 2,937 that passed
   in the build).

2. **The NDVI promotion staleness ceiling (change 2).** `vegetation-ndvi-governed-plane-promotion` is
   `active=False`, so no promotion turn ran and the 21-day ceiling was never evaluated against a real
   lane ceiling. What was proved is that the lane inventory still reports `lane_count 14 | active 12`
   with NDVI inactive, and that report shapes did not drift. The bound's actual arithmetic is
   **unverified in production**. The change is argued to be behaviour-preserving at the current
   `publication_lag_days = 7` (both old and new forms yield 21), but that argument comes from the
   diff, not from an observed turn.

3. **The ML schema golden fixtures (change 4).** Test-only. Not exercised by any running service and
   **not covered by this watch at all**. Its presence in the image is confirmed only transitively via
   the receipt digest.

4. **The weather witness's refusal states, and the extra walking itself (change 1).** Section 4
   observes a HEALTHY turn: the witness returns `grid_matches`, which the PRE-wave code did not
   short-circuit either. So the 18:30Z turn proves the change costs a healthy turn nothing (indeed
   28.41s, the fastest of the last four), but it does **not** exercise the behaviour that changed.
   Specifically unverified in production:
   - the `other_grids_witnessed` -> exhausted-walk -> `foreign_support_grid` path, which is the
     only path where wave 12 now walks where the pre-wave code refused for free. **The cost of that
     walk is therefore unmeasured** -- one GET per support point, ~98 points, on a day that used to
     cost nothing. The 28.41s figure says nothing about it.
   - the OWED verdict and its new operator message (including the `witness holds at most 8 grids`
     truncation clause), the LOST verdict's inverted `grid_matches` selector, and the UNKNOWN
     fall-through.
   - `witness_truncated`, `probe_budget_exhausted`, `no_retained_capture` and `partial_capture` --
     all **0** in production, which is correct for a healthy day and is not evidence about them.

   These rest on the unit tests added in `tests/pipeline/direct/test_weather_observations_recovery.py`
   (+89) and `test_weather_observations_forward.py` (+43), which this watch did not run.

## 9. Log sweep

`railway logs -s <service> -d` across the three platform services, on the settled `3b052766`
deployments. `plantgeo-main` was re-fetched after the forced row reads of section 6.1.

| Pattern | `plantgeo-main` | `plantgeo-parquet-api` | `plantgeo-job-executor` |
| --- | --- | --- | --- |
| `Traceback` | 0 | 0 | 0 |
| `ERROR` | 0 | 0 | 0 |
| `CRITICAL` | 0 | 0 | 0 |
| `region_identity_mismatch` | 0 | 0 | 0 |
| `Parquet region guard inert` | 0 | 0 | 0 |
| `foreign_support_grid` | 0 | 0 | 0 |
| `UnregisteredPartitionCells` | 0 | 0 | 0 |
| `DeprecationWarning` | 0 | **2** | 0 |
| `availability_stale` | 0 | **2** | 0 |

### 9.1 The two non-zero counts are both pre-existing, and neither is a wave-12 regression

**`DeprecationWarning` x2 on `plantgeo-parquet-api`** -- the known Sanic listener-loop deprecation:

```
sanic/logging/deprecation.py:33: DeprecationWarning: [DEPRECATION v26.6] Passing the loop argument
to listeners is deprecated. Your listener 'create_app.<locals>.setup_resources' should only accept
the app argument.
```

Called out as known and pre-existing in the brief; it predates `3b052766`.

**`availability_stale` x2 on `plantgeo-parquet-api`** -- the NASA POWER upstream outage, verbatim:

```
availability_stale" reason="availability source ceiling 2026-06-24 precedes required 2026-09-03"
  event="availability_coverage_withheld" request_id="75c15761-..." timestamp="2026-09-19T18:07:29.697179Z"
availability_stale" reason="availability source ceiling 2026-06-24 precedes required 2026-09-03"
  event="availability_coverage_withheld" request_id="b0236220-..." timestamp="2026-09-19T18:11:45.934566Z"
```

The affected layer is `climate-field-shortwave-radiation`, ceiling `2026-06-24` -- exactly the known
upstream POWER outage (`ALLSKY_SFC_SW_DWN` returning -999 from 2026-07-01). Pre-existing, upstream,
**not a wave-12 regression**.

### 9.2 One further line, checked before being reported

`plantgeo-parquet-api` emits at startup:

```
[info] region_bindings_unverified  region=pnw source_slugs=['era5_land_and_nasa_power', 'firms',
'gbif', 'hydrosheds', 'noaa_nws', 'open_meteo', 'oregon_oem_arcgis', 'sentinel2_ndvi', 'usgs_nwis',
'wfigs']
```

Checked against history before reporting: this line is documented in the
`release-checkpoint-20260919-0503ccd8.md` and `release-checkpoint-20260919-3a548034.md` evidence files
from earlier waves today. It is **pre-existing and predates `3b052766`** -- an informational startup
note, not a wave-12 finding.

## 10. Verdict

**PASS.**

All four gate services -- `plantgeo-main`, `plantgeo-parquet-api`, `plantgeo-job-executor` and
`plantgeo-martin` -- carry `3b052766` exactly; containment holds for all four, no deployment was
SKIPPED, no build failed, and no service is on a revision older than the pre-wave `d6ef9921`.
`/api/ready` is 200, Martin's `/health` is 200, all six known-good reads are byte-identical to their
pre-wave values, the coverage census still answers 16 bindings at `region_slug: "pnw"`, and the
slider still offers 21 layers.

Of the four wave-12 changes: the weather recovery witness ran a clean, in-budget turn (28.41s,
faster than all three pre-wave turns, with `existing 1614 + incoming 93 = merged 1707`,
`updated_rows=0`, retention `93/93/0` and no `foreign_support_grid`); the lane inventory confirms
`lane_count 14 | active 12` with NDVI promotion `active=False` and no report-shape drift; the region
guard logged nothing across 243 deliberately forced row reads. The ML golden fixtures are test-only
and were not exercised at all.

**Three of the four changes are only partially proven, and one not at all** -- the guard's inert
path, the promotion staleness arithmetic, the weather witness's refusal states (including the extra
walk's unmeasured cost), and the ML fixtures are all recorded in section 8 as UNPROVEN rather than
passing. Nothing in this watch contradicts any of them; nothing in it confirms them either.

The only two non-zero sweep counts -- 2 Sanic `DeprecationWarning` and 2 `availability_stale` for
`climate-field-shortwave-radiation` (NASA POWER ceiling `2026-06-24`) -- were both checked against
history and **predate `3b052766`**. Neither is a wave-12 regression.

`plantgeo-ml` sits on `d6ef9921` and belongs to a concurrent session; it was observed and reported,
never acted on.
