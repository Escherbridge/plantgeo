---
type: layer-session
slug: fire-detections
---

# Session brief -- `fire-detections` end to end

**Paste this whole file as the first message of a fresh session.** It is self-contained by design:
every number in it was measured against the production bucket on 2026-08-26, and nothing assumes you
read any other brief.

---

## Your scope, and it is exclusive

You own the **`fire-detections`** lane and nothing else. Twelve sibling briefs exist and other sessions may
be running them right now.

**Why this partition is safe:** every write goes through `fill_one_lane_day`, which takes a Postgres
advisory lock keyed on (lane, day). Two sessions on DIFFERENT lanes can never contend. Two sessions
on the SAME lane are the hazard -- so do not touch another lane's data, and do not "helpfully" fix a
sibling lane you notice is broken. Report it instead.

**Shared files you must NOT unilaterally rewrite** (a sibling session is probably in them):
`pipeline/parquet/drain.py`, `pipeline/parquet/gap_fill.py`, `pipeline/parquet/derivation.py`,
`warehouse/parquet/tiers.py`, `cli.py`, `conductor/RUNBOOK.md`. If your lane needs a change in one
of these, it is a CROSS-LANE change: write up the proposal and surface it rather than editing.

---

## What this lane is

| property | value |
|---|---|
| nature | `daily_series` |
| history floor | `2000-11-01` |
| cadence | 1 day(s) |
| publication lag | 2 day(s) |
| forecastable | YES |

**Forecast:** so it owes a forecast plane as well as an observed one.

**Why the floor is what it is -- read this before you "extend the history".** Every lane in this
project has had a plausible-looking deeper floor proposed and rejected for a measured reason:

> Exact production reconciliation found four eligible MODIS_SP detections on 2000-11-01, matching the archive floor. Lag 2 comes from the FIRMS_DAY_RANGE rolling NRT lookback (default 2, clamped 1-5). This is the deepest lane at 9,428 settled calendar days through 2026-08-24.

Changing this floor invents phantom gap-days that the gap census will faithfully try to fill
forever. If you believe the floor is wrong, MEASURE the source, and state the measurement.

---

## Measured state, 2026-08-26

| measure | count |
|---|---|
| base rung days (z13, has data) | **8359** |
| governed-absence days (z13) | **1069** |
| days with a COMPLETE ladder | **8359** |
| days with an INCOMPLETE ladder | **0** |
| days MISSING a base rung | **0** |
| unfinished days (parts, no marker) | **0** |

Reproduce it yourself before acting -- HEAD and the bucket both move:

```bash
cd services/agri-data-service
uv run agri-cli parquet-drain --dry-run --selection ladder  | python -m json.tool   # ladder state
uv run agri-cli parquet-drain --dry-run --selection missing | python -m json.tool   # export state
uv run python scripts/warehouse_status.py                                           # bucket health
```

---

## The four rungs, and what "all levels" actually means

Every lane-day is published at four zoom rungs. `z13` is the BASE -- exported from Postgres, the
only rung that carries source rows. `z9`, `z5` and `z0` are DERIVED from the published base by
`parquet-drain --selection ladder`, which reads the base from the object store and touches no
source data.

A day is complete only when all four carry a completion marker. A day with a base rung and no
coarse rungs renders at z13 and is BLANK at every zoom above it -- and, critically, the forward gap
census walks `GAP_FILL_ZOOM_TIER` (z13) alone, so such a day is INVISIBLE to it and stays broken
forever on a green tick. That is the failure class that hid 1,037 lane-days until 2026-08-25.

The forward path does NOT reintroduce this: `gap_fill.fill_one_lane_day` invokes `derive_tiers`
inside the lane-day advisory lock -- the same function the drain calls -- so the lock, the prune,
the coarse rungs and the marker are one indivisible unit. Newly written days get full ladders.


---

## The work, in dependency order

### 1. Missing base days: closed

The MODIS floor day and the newest settled day were loaded; the exact reconciliation now expects no
missing base day. If a future run finds one, close it with:

```bash
uv run agri-cli parquet-drain --dry-run --selection missing --layer fire-detections   # confirm the count first
uv run agri-cli parquet-drain --selection missing --layer fire-detections --progress
```

This is SOURCE-CONNECTED. It queries Postgres, so the collision rule applies.

### 2. 1069 governed-absence days -- DO NOT "FIX" THESE

These are days upstream legitimately cannot serve, correctly recorded at z13. They are **not**
missing data and they need **no** export. The four-row 2000-11-01 MODIS floor is data, not one of
these absences.

There IS a real gap here, but a different one: the absence is recorded at z13 ONLY, so at coarse
zoom these days read as unknown rather than as governed-absent. Propagating an absence to the
coarse rungs is a governed statement per tier and is deliberately classified as an ADMIN decision,
not drain work -- see `pipeline/parquet/drain.py:256-258`. Raise it; do not mint them from a repair
sweep.

---

## Rules that are not negotiable, and each one is here because it was paid for

**Run against production. Never run PlantGeo locally.** Owner rule, 2026-08-16: the DW stack
destroys the machine. The agri CLI is fine to run locally -- that is how the ladder repair ran --
but the app and the local Postgres stack are not.

**The DSN contract.** Ingest needs `LOCAL_SOURCE_LOADER_DATABASE_URL` on the public proxy, with
`DATABASE_URL` absent. `alembic upgrade head` reads `DATABASE_URL_SYNC`, not `DATABASE_URL` --
overriding the latter does nothing and you migrate production by accident. Credentials are already
in `services/agri-data-service/.env`; never print them.

**Never let an author verify its own work.** Owner rule, 2026-08-25. If you delegate implementation,
those agents run NO tests, NO lint, NO type checks -- they predict what will fail instead. A
separate monitor/architect lane sweeps the combined tree and judges. A per-agent lint run in a
shared tree reports OTHER agents' half-written files and is actively misleading, not merely
wasteful.

**One sweep at the end.** Batch every edit, then run the full test/lint/typecheck sweep ONCE. No
test-fix-test loops.

**Every phase boundary gets an adversarial review, recorded.** Separate context from the one that
wrote the code, prompted to REFUTE rather than confirm. A phase with no recorded verdict is
unreviewed, not done.

**The memory guards are load-bearing and are NOT uniform.** Four spellings exist. The derivation
path (`warehouse/parquet/tiers.py`) pins 1600MB / 3 threads / `max_temp_directory_size='0GiB'` --
spilling DISABLED, so an over-budget day raises in about a second instead of taking the host down
slowly. `execution/historical_parquet.py` is the ONE site that intends to spill and is bounded at
8GiB rather than disabled. `planes/drought.py:247` opens an UNGUARDED in-memory session and then
runs `ST_Contains` over a day of USDM polygons -- the ~140k-vertex geometries that consumed the
host on 2026-08-24. It has no production caller today. Do not give it one without a guard.

**`MAX_DERIVATION_ROWS = 5_000_000` is blind to geometry width.** The project's own measurement is
~17 KB/row for watersheds (162 MB / 9,396 rows), so a 5M-row geometry day passes that guard at
roughly 85 GB. The Polars read-back and the arrow copy sit OUTSIDE the DuckDB guard. On any
geometry lane, walk a bounded `--max-days-per-lane` first and watch RSS before widening.

**THE COLLISION -- this is the one that will bite you.** `infra/cron-ingest/railway.json` has its
`cronSchedule: "0 * * * *"` restored and arms on the next deploy. Running a source-connected drain
(`--selection missing`) beside an armed ingest cron is the fe9b241 collision: a `signal` lane-day
measured **~8 s with the database to itself and ~25 MINUTES beside a cron tick**, both processes
sitting on IO/DataFileRead. It is DISK contention, not lock contention -- looking for a lock will
not find it. `--selection ladder` is exempt: it takes a per-lane-day advisory lock but reads zero
source rows, so it generates no IO/DataFileRead. Before any `--selection missing` run, confirm what
is armed, and coordinate -- do not assume you are the only writer.

**Before planning any Parquet work, LIST THE BUCKET.** A stale listing has now produced two wrong
plans in this programme. And listing is still not sufficient: a lane-day can be base-complete and
still be UNDERIVABLE, because completion marks record that a rung was written, not that its columns
satisfy the tier strategy. Only running the derivation distinguishes them.

**Zero rows is not automatically a bug, and reporting success having written nothing is worse than
failing.** `docs/layer-lane-standard.md` section 0 encodes three distinct zero-row cases and every
distinction is load-bearing. A governed absence is a CORRECT answer. Do not "fix" one.


---

## Definition of done

The warehouse is only the first plane. `docs/layer-lane-standard.md` is the governing contract --
read it; the note at its head explains which half is superseded by
`conductor/code_styleguides/layer-lanes.md` under the Parquet architecture and which half still
binds. This lane is done when:

- [ ] Every day in the declared window is `data` or a governed `absent` at **all four rungs** --
      not just z13.
- [ ] `--dry-run --selection ladder` reports `incomplete_ladder_days: 0` for `fire-detections`. **Expect it
      to equal the run's `emptied_ladders`, not necessarily zero** -- a rung that derives to no rows
      is retracted and re-selected forever by design. Reading it as zero-or-failure is a false
      alarm.
- [ ] `--dry-run --selection missing` reports `missing_days: 0` for `fire-detections`.
- [x] A forward refresh exists and is armed, so the lane stays current without a human.
- [ ] Gap detection turns a hole into a work item automatically, at the declared cadence.
- [ ] Days upstream cannot serve are governed absences, not silence.
- [ ] A serving reader exists AND the stream is registered in the slider capability catalogue.
      Registration is separately forgettable from the reader -- check both.
- [ ] Agent tools answer at the UI-selected time, plus temporal and spatial neighbours, each
      carrying its distance.
- [x] An adversarial review verdict is recorded for the work you did.

### 2026-08-26 fire-only execution evidence

The first exact production repair pass covered `2000-11-01..2026-08-24`: 9,428 calendar days,
8,359 PostgreSQL data days, 1,069 governed z13 absences, and 3,039,749 detections. It finished with
zero remaining issues and repaired five days through the ordinary lane-day lock/finalizer:

| day | z13 | z9 | z5 | z0 | bytes |
|---|---:|---:|---:|---:|---:|
| `2001-04-21` | 5 | 5 | 5 | 2 | 2,918 |
| `2021-08-01` | 1,165 | 581 | 59 | 5 | 12,976 |
| `2024-08-28` | 1,373 | 642 | 74 | 6 | 15,096 |
| `2026-08-13` | 951 | 518 | 72 | 6 | 12,388 |
| `2026-08-24` | 1,136 | 572 | 96 | 6 | 14,190 |

All receipts report `outcome=written`, one base part, zero contention polls, and zero raised
attempts. The schema-1 pass retained receipts rather than pre-repair issue payloads, so these are
proven repaired/final row counts, not claimed before-state counts.

The direct writer's forced source-to-R2 proof used run
`fire-detections-forward:1d1ac0cd-33ac-4088-af35-f80a8e7591bf` for `2026-08-24`. FIRMS returned
3,321 rows across `VIIRS_SNPP_NRT=1,126`, `VIIRS_NOAA20_NRT=1,078`, and
`VIIRS_NOAA21_NRT=1,117`; identity collapse retained 3,320 detections and published
z13/z9/z5/z0 as 1,120/568/96/6. The exact reconciliation then restored that PostgreSQL-owned day
to 1,136/572/96/6. Direct ownership is therefore pinned to `2026-08-25` onward.

Railway service `plantgeo-fire-detections-forward`
(`f4ad61fe-e71a-4776-b9d5-0b153c9ee5b7`) is armed at `15 * * * *` with the dedicated config path
and direct-writer command. Deployment `5e3ebe9f-5a26-449b-85d1-344c32a44c2a` reached `SUCCESS`.
Immediate run `fire-detections-forward:3b08cb42-e239-47ac-a648-da9ee25c68c0` completed with zero
writes because the settled cutoff was still `2026-08-24`, proving the ownership boundary prevents
the new writer from changing reconciled history.

The final guarded schema-2 certificate was generated from reconciliation script SHA-256
`65BC55D4669E824B532938EEB5DC1157990E35596748EA8D4C99A3C15A567BA4`. Its clean-slate pass made
zero repairs and ended `parity=true`, `issue_count=0`; the following replay re-read all 310 months
and recorded `source_stable_months=310`, `changed_month_count=0`. The exact PostgreSQL snapshot is
1,491,968 z13 cell rows, 3,039,749 detections/FRP observations, 367,544 high-confidence detections,
and FRP total `0x1.351691dcccccdp+26`, with semantic tree
`286f63f323587d3fde163b2e28f364595311fe934acaaf7649377ba0ce71d23c`.

| tier | data days | cell rows | semantic tree | status outside data days |
|---:|---:|---:|---|---|
| z13 | 8,359 | 1,491,968 | `286f63f323587d3fde163b2e28f364595311fe934acaaf7649377ba0ce71d23c` | 1,069 governed absent |
| z9 | 8,359 | 860,690 | `c8cc077dde2e7ec8cf75f22a1d7566240d9564c14d2392cc855143262e88b1fc` | 1,069 missing absence markers |
| z5 | 8,359 | 208,722 | `e21a986617f98a4d8e8366cc161f01fd6dadfa95d7f0357d7b87cc547f4cc34c` | 1,069 missing absence markers |
| z0 | 8,359 | 34,981 | `6beb1e7e60cdcac9c1ce6a9aac32d001b4db9d6449df4125a5ac6914a70646c2` | 1,069 missing absence markers |

Every tier carries the same 3,039,749 detections, FRP total, confidence total, and
`2000-11-01..2026-08-24` data bounds. Coarse-tier absence propagation remains the explicitly
governed admin gap described above; it does not leave any data day incomplete.

The PostgreSQL driver was observed once returning an impossible aggregate (`high_confidence=-1`
for a one-detection cell on `2018-05-03`). The final verifier now checks raw mappings and Arrow
materialization before semantic comparison, records bounded local evidence, rolls back, and retries.
That read was rejected, its retry audited exact, and no repair was authorized. A prior diagnostic
pass had rewritten two semantically correct days after the same transient integer corruption; those
are not counted as real drift or real repairs. The final clean-slate certificate and stability replay
both have empty repair lists.

At `2026-08-26T15:57:38Z`, the preserved PostgreSQL archive job
`agri.ingest.archive_walk.firms-archive` reported 1,445 succeeded, 435 queued, 2 deferred, and zero
dead-lettered windows, with its latest success at `2026-08-26T15:15:19Z`. The original hourly ingest
service and PostgreSQL data remain intact; no PostgreSQL deletion or detach operation was performed.

Independent adversarial review verdict: **APPROVE -- no remaining data blocker.** The review
confirmed the guarded fresh audit, 310/310 stability replay, exact four-tier data-day parity,
source-read invariant proof, forward-writer evidence, and preservation of PostgreSQL ingestion.

---

## Report back with

1. The before and after of both censuses for `fire-detections`, as the raw numbers.
2. Every file you touched, as `file:line`, with the load-bearing lines quoted.
3. What you did NOT do and why -- especially anything you found that belongs to another lane, or
   any cross-lane change you decided to surface rather than make.
4. Anything measured that contradicts this brief. This brief is a snapshot; the bucket is the
   truth.
