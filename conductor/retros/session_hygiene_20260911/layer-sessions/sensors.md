# Session brief -- `sensors` end to end

**Paste this whole file as the first message of a fresh session.** It is self-contained by design:
every number in it was measured against the production bucket on 2026-08-25, and nothing assumes you
read any other brief.

---

## Your scope, and it is exclusive

You own the **`sensors`** lane and nothing else. Twelve sibling briefs exist and other sessions may
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
| history floor | `2026-07-29` |
| cadence | 1 day(s) |
| publication lag | 1 day(s) |
| forecastable | YES |

**Forecast:** so it owes a forecast plane as well as an observed one.

**Why the floor is what it is -- read this before you "extend the history".** Every lane in this
project has had a plausible-looking deeper floor proposed and rejected for a measured reason:

> NWS keeps a rolling ~6-day window and NO deeper archive exists. The whole record is what this producer accreted since 2026-08-04 plus its first run's ~6-day reach. geo.features is append-only for this lane, so the floor is static even though the SOURCE's is not.

Changing this floor invents phantom gap-days that the gap census will faithfully try to fill
forever. If you believe the floor is wrong, MEASURE the source, and state the measurement.

---

## Measured state, 2026-08-25

| measure | count |
|---|---|
| base rung days (z13, has data) | **26** |
| governed-absence days (z13) | **1** |
| days with a COMPLETE ladder | **1** |
| days with an INCOMPLETE ladder | **25** |
| days MISSING a base rung | **1** |
| unfinished days (parts, no marker) | **0** |

Reproduce it yourself before acting -- HEAD and the bucket both move:

```bash
cd services/agri-data-service
uv run agri-service data parquet-drain --dry-run --selection ladder  | python -m json.tool   # ladder state
uv run agri-service data parquet-drain --dry-run --selection missing | python -m json.tool   # export state
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

### 0. READ THIS FIRST -- there is a hard, unrecoverable deadline

NWS keeps a rolling **~6-day** window and Postgres is this lane's ONLY archive. **Days after
2026-08-31 become permanently unrecoverable** once that window rolls past them. Nothing downstream
can reconstruct them.

The forward writer is `plantgeo-ingest-cron`, whose `cronSchedule` was restored in config but
**takes effect only on a deploy**. If no deploy has happened, this lane is actively losing history
right now. Establish that first, before any backfill work -- a lane bleeding new days while you
repair old ones is the wrong order.

### 1. The 25 incomplete-ladder days -- BLOCKED, and not by effort

`sensors` is one of exactly three lanes whose coarse rungs CANNOT be derived. Measured
2026-08-25 by running the repair against production:

```
TierDerivationError: sensors: the tier derivation names coordinate column(s)
[station_longitude, station_latitude] that the base table does not carry
```

The base rungs for these 25 days were written before the coordinate columns existed.
`--selection ladder` cannot fix them at any budget, and the drain correctly stops this lane after
three consecutive failures rather than burning the whole backlog rediscovering the same fault.

**The remedy the error itself names: retract and re-export, not re-derive.** That means an admin
retraction of the affected base days, then `parquet-drain --selection missing --layer sensors`,
which IS source-connected -- so read the collision rule above before you start it.

**Confirm the boundary before retracting anything.** 1 of this lane's 26
base days already carry a complete ladder and are CORRECT -- they must not be retracted. Establish
exactly which days lack the columns before touching one; re-exporting a good day costs production
Postgres time to rewrite bytes that are already right, and `signal` alone measured 151 s for one
cold day.

### 2. The 1 missing base day

Days with no base rung at all. Close them with:

```bash
uv run agri-service data parquet-drain --dry-run --selection missing --layer sensors   # confirm the count first
uv run agri-service data parquet-drain --selection missing --layer sensors --progress
```

This is SOURCE-CONNECTED. It queries Postgres, so the collision rule applies.

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
- [ ] `--dry-run --selection ladder` reports `incomplete_ladder_days: 0` for `sensors`. **Expect it
      to equal the run's `emptied_ladders`, not necessarily zero** -- a rung that derives to no rows
      is retracted and re-selected forever by design. Reading it as zero-or-failure is a false
      alarm.
- [ ] `--dry-run --selection missing` reports `missing_days: 0` for `sensors`.
- [ ] A forward refresh exists and is armed, so the lane stays current without a human.
- [ ] Gap detection turns a hole into a work item automatically, at the declared cadence.
- [ ] Days upstream cannot serve are governed absences, not silence.
- [ ] A serving reader exists AND the stream is registered in the slider capability catalogue.
      Registration is separately forgettable from the reader -- check both.
- [ ] Agent tools answer at the UI-selected time, plus temporal and spatial neighbours, each
      carrying its distance.
- [ ] An adversarial review verdict is recorded for the work you did.

---

## Report back with

1. The before and after of both censuses for `sensors`, as the raw numbers.
2. Every file you touched, as `file:line`, with the load-bearing lines quoted.
3. What you did NOT do and why -- especially anything you found that belongs to another lane, or
   any cross-lane change you decided to surface rather than make.
4. Anything measured that contradicts this brief. This brief is a snapshot; the bucket is the
   truth.
