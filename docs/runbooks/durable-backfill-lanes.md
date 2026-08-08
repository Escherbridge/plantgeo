# Runbook: Durable archive backfill lanes (`agri.job_*` ledger)

> New operator? Start at
> [`services/agri-data-service/README.md`](../../services/agri-data-service/README.md).

## Why this exists

A bash driver walked the NASA FIRMS fire archive. It hit `ConnectError` on **169 of 298
windows** — 57% — advanced its cursor past every one of them, wrote a `.done` sentinel, and
reported success. 2.5 years of fire history were lost. On the map that did not look like a
broken pipeline; it looked like three quiet fire seasons.

Nothing in that story was an unlucky accident. The driver was **laptop-bound** (it ran as a
Windows scheduled task, so it stopped when the laptop slept), its progress lived in a **file**
(so nothing outside the container could query it), and its failure ledger was **deleted by the
next successful run** (so the evidence did not survive). Most of all, its cursor recorded where
the walk had *reached*, never what had *landed* — and those two numbers can differ by 169
windows without anything noticing.

This runbook covers the replacement. Every one of those properties is inverted:

| The bash driver | This lane |
| --- | --- |
| Cursor file on a laptop's disk | `agri.job_work_item` rows, one per window |
| Failure ledger deleted on success | `dead_letter` status, permanent and queryable |
| `.done` sentinel with no floor recorded | `logical_run_key` carries the floor |
| Advanced past a failed window | A failed window stays visible and non-terminal |
| Ran while a laptop was awake | A Railway cron container, `restartPolicyType: NEVER` |

**The one rule everything else serves:** a window may only reach a terminal `succeeded` state
when its data has actually landed. Any change that lets a window go terminal without that is the
bug coming back.

## The model

```
one lane  ->  one JobRun  ->  one work item per window  ->  one handler call per chunk
```

- A **lane** (`ingest/lanes.py`) declares what to walk and in what steps: a source, a floor, a
  window size, a chunk size. `firms-archive` walks NASA FIRMS from 2000-11-01 in 5-day windows
  of 1-day chunks. `streamflow-archive` walks USGS NWIS daily values from 2022-08-05 in 30-day
  windows of 10-day chunks.
- A **JobRun** is one lane at one floor. The floor is part of `logical_run_key`
  (`archive-walk:firms-archive:2000-11-01`), so changing the floor opens a *second* run rather
  than reopening a finished one.
- A **work item** is one window, keyed `firms-archive:2000-11-01..2000-11-06`. This is the unit
  that succeeds, fails, retries and dead-letters. It is what a completeness query groups by.
- A **cron tick** runs one bounded slice: claim a window, walk one chunk, checkpoint, repeat
  until the time budget runs out, park what is unfinished, exit 0.

**Durability lives in the ledger, never on the filesystem.** A cron container gets a fresh disk
on every tick and nothing brings it back if it dies, so anything written to disk is already lost.
A killed container loses only the work since its last checkpoint; the next tick claims the same
shards straight out of the database and resumes each from its own cursor.

Design rationale beyond the operator's view lives in
`services/agri-data-service/src/agri_data_service/jobs/AGENTS.md`.

## The verbs

All five live in `agri_data_service/ingest/commands.py` and ship in the same image as every
other cron verb.

| Verb | What it does | Exit code |
| --- | --- | --- |
| `jobs-plan-lane --lane <token> [--floor DATE] [--until DATE]` | Declare the lane and fan its windows out as work items. Idempotent. | `0` unless the plan could not be written |
| `jobs-run --lane <token> [--budget-seconds N] [--worker-id S]` | Run one bounded slice. **This is what a cron tick invokes.** | `1` only if a window dead-lettered or the slice raised; otherwise `0` |
| `jobs-status [--lane <token>] [--definition <name>]` | Counts by state, the oldest outstanding window, the dead-lettered shard keys. | always `0` |
| `jobs-reconcile-lane --lane <token> [--apply]` | Settle windows whose days the layer already serves. Dry run by default. | always `0` |
| `validate-streams [--format json\|markdown] [--output PATH]` | The cross-stream completeness and validity report. | `1` only if a stream is `invalid` |

Every verb prints **one JSON line per result on stdout**; operational logging goes to stderr.
That is the same contract every `ingest-*` verb honours, so a cron log stays parseable.

### Why `jobs-run` exits 0 with work remaining

**A healthy multi-tick backfill is incomplete by definition.** FIRMS' archive is ~1,900 windows;
at one slice every 30 minutes it is weeks of correct operation. If `jobs-run` exited non-zero
because work remained, every one of those ticks would be a red cron run, and by the time a
window genuinely dead-lettered nobody would be reading the alerts.

So the failure signal is narrow and specific: **exit 1 when a window dead-lettered during this
slice.** That means the window spent all eight attempts and is now missing from the archive until
a human requeues it. Everything else the runtime does with a shard is a park it will pick up
again:

- `retried` — the attempt failed, the backoff is ticking, the shard comes back.
- `deferred` — upstream said "not yet". Costs no retry budget.
- `yielded` — the handler ran out of the tick's clock. Costs no retry budget.
- `abandoned` — the fence moved; another worker owns that window and its work is theirs.
- `stop_reason: no_claimable_work` / `no_open_run` — nothing to do. Not an error.

### Always use `--lane`, not `--definition`

`archive_lane_definition_name()` is the **single producer** of a `job_definition.name`. A
`railway.json` that spelled `agri.ingest.archive_walk.firms-archive` itself would carry a second
copy that joins to nothing the day the naming changes — and a slice that joins to nothing claims
no work while still exiting 0. `--lane firms-archive` resolves through the producer.
`--definition` exists for ad-hoc queries against a definition the lane registry no longer knows.

## Day-to-day

### Plan a lane

```bash
agri-cli jobs-plan-lane --lane firms-archive
```

**Safe to re-run on every tick of every day.** The run inserts `ON CONFLICT (logical_run_key) DO
NOTHING` and each window `ON CONFLICT (job_run_id, shard_key) DO NOTHING`, and the window grid is
anchored at the lane's **floor** rather than at today — so a replan produces byte-identical shard
keys and adds, at most once per `window_days`, one genuinely new window. Read
`added_work_items: 0` as "nothing changed", which is the normal outcome.

Trailing days above the newest whole window are deliberately not planned. The forward hourly
cron owns the present.

```json
{"lane":"firms-archive","run_key":"archive-walk:firms-archive:2000-11-01","created":true,
 "added_work_items":1893,"total_work_items":1893,"run_status":"queued","floor_day":"2000-11-01"}
```

**`--floor` opens a new run, it does not edit the old one.** The floor is in the run key, so
`--floor 2012-01-20` mints a second run with its own grid and its own counters, and the first
stays exactly as complete as it was. That is the durable form of what `firms-archive-full.sh` had
to delete a sentinel file to achieve.

**A floor below what the source serves is refused.** Both lanes are already floored at their
source's `HistoryCapability.earliest`, so walking *deeper* than a lane declares means lowering
that value in `ingest/firms.py` or `ingest/usgs_nwis.py` first — it is the thing doing the
refusing, and planning past it would create thousands of windows every one of which
dead-letters after eight attempts.

### Reconcile it against what already landed — before the first tick

```bash
agri-cli jobs-reconcile-lane --lane firms-archive            # dry run; writes nothing
agri-cli jobs-reconcile-lane --lane firms-archive --apply    # settle the covered windows
```

Planning a lane creates a window for *every* window from the floor to today. Several hundred of
them already landed via the bash walk. Re-walking one is harmless — the feature writer's diff
rejects an unchanged payload, so it writes zero rows by construction — but at a measured
**11.5 minutes for one peak-season FIRMS day** it is many hours of fetch spent proving something
already true.

The reconciler derives what landed **from the data**: the distinct observed days its target layer
already serves, read through `geo.feature_observation_day` with the same `status = 'published'`
and `geometry_id IS NOT NULL` filters the completeness report uses. Three outcomes:

- **covered** — every UTC day of the window is present. Marked `succeeded`.
- **partial** — some days present. **Stays queued.** Partial is not landed: the missing days are
  exactly the days the walk still owes, and nothing visible from here separates "upstream
  published nothing that day" from "the fetch never reached upstream".
- **absent** — no day present. Stays queued.

It also **never touches**: a window another worker holds under a live lease (reported as
`held_shard_keys`), and a **dead-lettered window** (reported as `dead_lettered_shard_keys`, and
as `dead_lettered_covered` when its days are in fact all present). A dead letter is the durable
evidence that eight attempts failed; converting one to `succeeded` because the days happen to be
there now is the same erasure the whole ledger exists to prevent, run backwards. If you want one
of those windows settled, requeue it and let a real walk close it.

**Read the dry run before spending `--apply`.** The single most useful field is the span:

```json
{"lane":"firms-archive","state":"dry_run","observed_day_count":1284,
 "first_observed_day":"2022-08-05","last_observed_day":"2026-08-06",
 "planned_window_count":1893,
 "covered":{"window_count":293,"first_day":"2022-08-05","last_day":"2026-08-01","windows":[...]},
 "partial":{"window_count":2,"...":"..."},
 "absent":{"window_count":1598,"...":"..."},
 "would_mark_succeeded":293,"marked_succeeded":0}
```

If that span is not the era you expect the bash walk to have covered, stop and find out why
before applying. A settled window records a marker on its own payload
(`payload -> 'reconciled_from_observed_coverage'`) naming the layer, the day counts and the
timestamp, so the settlement is auditable afterwards:

```sql
SELECT shard_key, payload -> 'reconciled_from_observed_coverage'
FROM agri.job_work_item
WHERE payload ? 'reconciled_from_observed_coverage';
```

It records **no attempt row**. No attempt happened, and fabricating a worker id and a fencing
token for work this process never did would make every later query that counts attempts read a
lie.

#### Why this beats importing the bash cursor files

The cursor recorded where the walk had **reached**. It did not record what had **landed**. The
whole 169-window bug is the gap between those two, and importing `.agri-local-runs/locks/
firms-archive.cursor` would import exactly that error — marking 169 empty windows succeeded and
recreating the silent hole inside the system built to remove it.

Consequence for the cutover: **the cursor and failure files need no migration.** Abandon them
where they lie.

### Watch it

```bash
agri-cli jobs-status --lane firms-archive
```

```json
{"definition":"agri.ingest.archive_walk.firms-archive","run_count":1,
 "states":{"queued":1400,"retry_wait":6,"succeeded":486,"dead_letter":1},
 "total_windows":1893,"outstanding_windows":1407,
 "oldest_outstanding_window":"firms-archive:2000-11-01..2000-11-06",
 "dead_lettered":1,
 "dead_letter_windows":[{"shard_key":"firms-archive:2013-07-11..2013-07-16",
                         "attempt_count":8,"last_error_class":"upstream_unavailable"}],
 "omitted_dead_letter_windows":0,
 "runs":[{"run_key":"archive-walk:firms-archive:2000-11-01","...":"..."}]}
```

This replaces grepping a log file. When a lane has more than one run (because its floor was
lowered), read the per-run breakdown: the top-level `states` counts **ledger rows**, and two runs
of one lane hold overlapping calendar days.

`jobs-status` carries no timestamp of any kind, so it cannot tell you a rate, an ETA, or whether
anything has moved since yesterday. For that, use the live dashboard.

### Live dashboard

The `agri-data-service` Sanic app serves an operator console at **`/ops/backfill`**. Locally:

```bash
cd services/agri-data-service && make dev
# then open http://localhost:8000/ops/backfill
```

It exists on the deployed service too, at `https://<agri-data-service-host>/ops/backfill`.

Per lane run it shows the eight work-item state counts, completion percentage, windows-per-hour,
a naive ETA, the number of leases that have already expired while their window is still `leased`
or `running` (stall candidates), the newest dead-lettered and retry-waiting windows with their
error text, and a daily dead-letter trend with a running total. The page renders server-side and
then refreshes itself over server-sent events every five seconds; `?interval=` accepts 2–30
seconds and `?window=` changes the trailing window the rate is measured over (default 24 hours).
`GET /ops/backfill.json` returns the same snapshot for scripting.

Two things to read correctly:

- **`eta` shows an em dash when the rate is zero.** No windows succeeded in the trailing window,
  so the remaining time is not derivable — that is a stalled lane, not an instant one.
- **The activity column is "last recorded activity", not "last cron run".** It is the newest
  durable timestamp across the run's work items, attempts and checkpoints. A tick that claims
  nothing writes *nothing* to the ledger, so a healthy finished lane and a cron service that has
  not fired in three days look identical here. To tell those apart you still need the Railway
  deployment log for the cron service.

> **`/ops` is not authenticated yet.** It leaks lane names, shard keys and redacted error
> summaries to anyone who can reach the service. Gate it (bearer token or Cloudflare Access)
> before the service is publicly reachable — tracked as a follow-up.

### What a dead-lettered window means, and how to requeue one

A `dead_letter` window failed on all eight attempts, with the backoff doubling from 30 seconds to
an hour between them. It is **missing from the archive** and nothing will pick it up again on its
own. That is deliberate: a shard that quietly reported success after exhausting its retries would
be indistinguishable from one that worked.

Read `last_error_class` first — it names what to do:

| `last_error_class` | Meaning | Action |
| --- | --- | --- |
| `upstream_unavailable` | The fetch failed eight times. Usually socket/DNS exhaustion, occasionally a real outage. | Requeue; it normally succeeds. |
| `all_records_rejected` | Records arrived and **every one** was rejected before the write path. | Investigate first — a renamed FIRMS CSV column, or FIRMS answering `Invalid MAP_KEY` as a 200 with a plain-text body. Requeuing without fixing that just burns eight more attempts. |
| `record_cap_truncation` | The chunk bit `INGEST_MAX_SOURCE_RECORDS`. The reason text names the narrower `--chunk-days` that would have fitted. | Narrow the lane's `chunk_days` in `ingest/lanes.py`, then requeue. |
| `walk_skipped` | `INGEST_BBOX` unset, or a typed history refusal. | Fix the deployment variable, then requeue. |
| `missing_credential` | The lane needs `NASA_FIRMS_KEY` and the service does not set it. | Set the variable on the cron service, then requeue. |
| `fence_lost` | Recorded but never persisted as a failure — the runtime abandons the shard instead. | Nothing; you should not see this on a dead-lettered row. |

Requeue one window (the ledger has no verb for this yet — it is a deliberate manual step, because
requeuing without reading the failure class is how a bad window burns another eight attempts):

```sql
UPDATE agri.job_work_item
SET status           = 'queued',
    completed_at     = NULL,
    next_attempt_at  = now(),
    attempt_count    = 0,
    last_error_class = NULL,
    last_error_summary = NULL
WHERE job_run_id = (SELECT id FROM agri.job_run
                    WHERE logical_run_key = 'archive-walk:firms-archive:2000-11-01')
  AND shard_key  = 'firms-archive:2013-07-11..2013-07-16';
```

Then let the next tick pick it up. The run's counters are recomputed from the work items at the
end of every slice, so nothing else needs correcting.

To requeue **every** dead letter on a lane, swap the `shard_key` predicate for
`AND status = 'dead_letter'`. Do that only after you know why they failed.

### Run the validation report

```bash
agri-cli validate-streams                                    # one JSON line
agri-cli validate-streams --format markdown --output docs/reports/streams.md
```

Three verdicts, and the distinction between the last two is the operational point:

- **`complete`** — no dead-lettered window, no gap beyond the stream's publication cadence, every
  validity check at zero.
- **`incomplete`** — the stream is short of what it owes. A lane in flight is incomplete for
  weeks by design. **This does not fail the run**, and that is not leniency: a daily cron that
  went red for the whole correct duration of a backfill would be ignored by the time it mattered.
- **`invalid`** — rows that *are* there are wrong: null geometry, geometry not linked (so the row
  draws on the map but is invisible to the time slider), a duplicate producer identity, USGS's
  `-999999` sentinel served as a real measurement. **This fails the run.** No amount of further
  walking repairs a wrong row.

A dead-lettered window can never produce a `complete` verdict, so a lost window still surfaces
here even when every validity check reads zero.

The report applies the **same two axis rules the time slider applies** — the 21-day cluster gap
and the 1% density floor, mirrored from
`src/lib/server/services/environmental-read-model.ts` and pinned by a cross-language test. A
report using a bare `MIN(observed_day)` would call water-gauges complete back to 1990 while the
slider starts it at 2022-08.

## Deployment

### The three new Railway services

All three build from the shared `infra/cron-ingest/Dockerfile`; the verb in `startCommand` is
what distinguishes them. `COPY services/agri-data-service/src/ src/` already carries the `jobs`
package, so no build change was needed.

| Service | `cronSchedule` | `startCommand` |
| --- | --- | --- |
| `cron-archive-firms` | `5,35 * * * *` | `agri-cli jobs-run --lane firms-archive` |
| `cron-archive-streamflow` | `20,50 * * * *` | `agri-cli jobs-run --lane streamflow-archive` |
| `cron-validate` | `0 6 * * *` | `agri-cli validate-streams` |

#### Why 30 minutes, and not `*/15`

**The real slice bound is 1,470 seconds — 24.5 minutes — not the 780-second budget.** The runtime
tests its deadline *before* each handler call and a handler call is uninterruptible, so a tick
that starts a chunk at second 779 runs until that chunk finishes:

```
ARCHIVE_WALK_TIME_BUDGET_SECONDS (780)  +  ARCHIVE_WALK_WORST_CHUNK_SECONDS (690)  =  1470s
```

690 seconds is the worst chunk ever measured on either lane: the 2026-07-23 FIRMS day at peak PNW
fire season, 11.5 minutes for one day.

A `*/15` cadence would therefore run **two ticks of the same lane concurrently** as a matter of
routine. That is *safe* — the fencing token and `FOR UPDATE SKIP LOCKED` hand them different
windows — but it is not free: it doubles the concurrent request rate against FIRMS, and sustained
concurrent load is exactly what the measured 169-of-298 first-attempt failures were
(`ConnectError`, with `getaddrinfo failed` alongside — local socket and DNS exhaustion, not an
upstream that was down). Deploying the fix at a cadence that reproduces its cause would be
self-defeating.

30 minutes (1,800s) is the tightest standard cadence that clears the worst case, leaving 330
seconds for container start, image pull, the definition load, the reaper pass and the closing
rollup. The handler shrinks the overrun further on its own: it estimates each chunk from the
slowest chunk its own window has already walked and **declines to start one it cannot finish**,
so a tick only runs long when a chunk turns out slower than twice everything its window has seen.

#### Why the two lanes are 15 minutes out of phase

`5,35` and `20,50` are the same cadence, half a period apart. Both lanes hold an ingest session
against the same Postgres for the whole of their tick and both fetch heavily, and correlated load
is the failure mode above. The `:05`/`:20` offsets also keep them clear of the quarter-hour where
every existing forward cron clusters (`cron-evacuation-zones` at `*/15`, `cron-streamflow` at
`*/30`). Neither offset is load-bearing on its own; the cadence is.

#### Why `cron-validate` runs at 06:00 UTC

`cron-ndvi` runs `0 5 * * *` and is the heaviest daily forward load, so 06:00 reads a day whose
loads have settled. It also falls in none of `{:05, :20, :35, :50}`, so the report never starts
while an archive slice is starting.

### Railway dashboard settings — per new service

These are **dashboard changes**, not repo changes. `railway.json` cannot set them.

| Setting | Value | Why |
| --- | --- | --- |
| **Root Directory** | `/` | The build context is the repository root; the image needs `services/agri-data-service/{pyproject.toml,uv.lock,src/}`, which a service rooted at `/infra/cron-archive-firms` cannot see. |
| **Config-as-code path** | `infra/cron-archive-firms/railway.json` (etc.) | With Root Directory at `/`, Railway will not find the per-service `railway.json` unless it is named. **Both settings must change together** — setting one without the other cannot work. |
| **Builder** | Dockerfile | Comes from `railway.json`. |
| **Restart policy** | `NEVER` | Comes from `railway.json`. A cron container must not be resurrected mid-walk. |

`RAILWAY_DOCKERFILE_PATH` as a service variable **cannot** substitute for the config path; that
is a known dead end recorded for the existing `cron-ingest` service.

### Environment variables — per new service

| Variable | `cron-archive-firms` | `cron-archive-streamflow` | `cron-validate` | Notes |
| --- | --- | --- | --- | --- |
| `LOCAL_SOURCE_LOADER_DATABASE_URL` | **required** | **required** | **required** | The loader DSN, on the public proxy. `ingest_session()` reads only this. |
| `DATABASE_URL` | **must be ABSENT** | **must be ABSENT** | **must be ABSENT** | `config.py` refuses to fall back to it, and refuses a loader URL that reuses it. Setting it fails the run. |
| `INGEST_BBOX` | **required** | **required** | recommended | An unset box makes the walk a typed skip, which this lane treats as a **failed** window — a window that wrote nothing is not a walked window. For `validate-streams` an unset box only marks the boundary check unevaluated. |
| `NASA_FIRMS_KEY` | **required** | not used | not used | The lane refuses loudly and dead-letters without it, rather than parking forever on a missing variable. |
| `REDIS_URL` | optional | optional | not used | Realtime invalidation. Absent is safe; the publisher opens no socket it cannot open. |
| `INGEST_MAX_SOURCE_RECORDS` | **do not set** | **do not set** | not used | The lane pins its own ceiling (50,000) per walk and restores the previous value. An unset variable defaults to 10,000, and a bitten cap drops the **oldest days of a chunk whole** rather than thinning it — a silent hole in a window the run still reports as walked. |

`cron-validate` reads and writes nothing but the report, and its session is pinned
`transaction_read_only`.

### First deployment, in order

```bash
# 1. Plan both lanes (once, from anywhere with the loader DSN).
agri-cli jobs-plan-lane --lane firms-archive
agri-cli jobs-plan-lane --lane streamflow-archive

# 2. Look at what already landed. READ THIS before step 3.
agri-cli jobs-reconcile-lane --lane firms-archive
agri-cli jobs-reconcile-lane --lane streamflow-archive

# 3. Settle it, so the first weeks of ticks are not spent re-walking landed windows.
agri-cli jobs-reconcile-lane --lane firms-archive --apply
agri-cli jobs-reconcile-lane --lane streamflow-archive --apply

# 4. Confirm the ledger reads the way you expect.
agri-cli jobs-status

# 5. Only now enable the two cron services.
```

Re-run `jobs-plan-lane` whenever a lane's `window_days` boundary passes — or simply let a
scheduled invocation do it; it is a no-op on every other day.

## The cutover from the bash drivers

**At the time of writing the two bash drivers are still on disk and still running.** Stopping them
is a deliberate operator step, not something this change did:

```
services/agri-data-service/durable-archive-backfill.sh
services/agri-data-service/firms-archive-full.sh
```

They run as **Windows scheduled tasks**, which is the laptop-bound property that made the original
failure invisible. All four must be unregistered:

| Scheduled task | Supersedes to |
| --- | --- |
| `PlantGeo-FIRMS-archive-backfill` | `cron-archive-firms` |
| `PlantGeoStreamflowArchiveBackfill` | `cron-archive-streamflow` |
| `PlantGeo-OpenMeteo-SoilTemp-backfill` | **nothing — see "Still to unify"** |
| `PlantGeo-OpenMeteo-VPD-backfill` | **nothing — see "Still to unify"** |

```powershell
Get-ScheduledTask -TaskName 'PlantGeo-FIRMS-archive-backfill','PlantGeoStreamflowArchiveBackfill' |
  Unregister-ScheduledTask -Confirm:$false
```

Order matters only in one direction: **unregister the tasks and let any in-flight walk finish or
be killed before enabling the two cron services**, so the two mechanisms are never walking the
same archive at once. They would not corrupt each other — the feature writer refreshes by
`properties->>'id'` and its diff rejects an unchanged payload — but they would double the request
rate against FIRMS, which is the exact condition that produced the original failures.

**Their state files need no migration.** Leave them; they are inert once the tasks are gone:

```
.agri-local-runs/locks/firms-archive.{cursor,failures,done,lock}
.agri-local-runs/locks/streamflow-archive.{cursor,failures,done,lock}
.agri-local-runs/logs/durable-*.log
```

`jobs-reconcile-lane` derives coverage from the warehouse instead, which is strictly better
information: the cursor said where the walk *reached*, and the 169-window divergence between that
and what *landed* is the entire bug. The logs are worth keeping as evidence; nothing reads them.

The two `.sh` files themselves can be deleted once the tasks are unregistered and the cron
services have completed a full pass. Deleting them earlier can corrupt a running bash process.

## Still to unify

**`durable-backfill.sh` is NOT part of this migration and remains a separate mechanism.** It
drives the PLAN-based historical lanes — ERA5-Land, NASA POWER, Open-Meteo archive, CAMS, GloFAS,
the ensemble lane — through `agri_data_service/execution/`, and those lanes have their own
checkpoint tables and their own release/finalization model. They are not `ingest-backfill` walks
and they do not fit the one-window-per-work-item shape.

That is why `PlantGeo-OpenMeteo-SoilTemp-backfill` and `PlantGeo-OpenMeteo-VPD-backfill` have no
successor service in the table above: **do not unregister those two as part of this cutover.**
They are still the mechanism for their lanes.

Porting them onto this ledger is a genuine follow-up and a real one — the same durability argument
applies — but it is a different piece of work, because their checkpoints already exist in
Postgres and the migration is "replace one durable mechanism with another", not "replace a file
with a row".

## The DBOS Transact evaluation

Recorded so it is not re-litigated. DBOS Transact (Python) was evaluated as an alternative to
writing this runtime and was **declined**, for three reasons that are all properties of *our*
deployment rather than defects in the library.

Its recovery is gated on an `app_version` hash of the registered function source, so any deploy
touching backfill code strands every in-flight walk until someone manually resumes it — and our
backfills change often. Its system database is driven by a **synchronous psycopg3 engine** inside
a service that is `sqlalchemy[asyncio]` + asyncpg end to end and hard-validates the
`postgresql+asyncpg` scheme on every DSN, and it self-migrates at launch (it will `CREATE
DATABASE`) against a schema where Alembic is the only permitted DDL actor. And its checkpoints are
opaque base64'd pickle blobs in `operation_outputs.output`, which cannot answer "which archive
windows have landed" as a SQL predicate — which is precisely the question this ledger exists to
answer.

Revisit only if the deployment model changes to a long-lived worker fleet; its async support and
its GIN-indexed workflow attributes are genuinely good.

## Reference

| Thing | Where |
| --- | --- |
| Runtime design intent | `services/agri-data-service/src/agri_data_service/jobs/AGENTS.md` |
| Lane declarations and the window grid | `.../ingest/lanes.py` |
| The handler (one call = one chunk) | `.../ingest/archive_walk.py` |
| The coverage reconciler | `.../ingest/reconcile.py` |
| The completeness report | `.../ingest/validation.py` |
| The verbs | `.../ingest/commands.py` |
| Ledger tables | `agri.job_definition`, `agri.job_run`, `agri.job_work_item`, `agri.job_attempt`, `agri.job_checkpoint` |
