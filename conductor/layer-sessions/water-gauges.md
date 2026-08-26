# Session brief -- `water-gauges` end to end

**Paste this whole file as the first message of a fresh session.** It is self-contained by design:
every number in it was measured against the production bucket on 2026-08-25, and nothing assumes you
read any other brief.

---

## Your scope, and it is exclusive

You own the **`water-gauges`** lane and nothing else. Twelve sibling briefs exist and other sessions may
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
| history floor | `2026-05-24` |
| cadence | 1 day(s) |
| publication lag | 2 day(s) |
| forecastable | YES |

**Forecast:** so it owes a forecast plane as well as an observed one.

**Why the floor is what it is -- read this before you "extend the history".** Every lane in this
project has had a plausible-looking deeper floor proposed and rejected for a measured reason:

> The DENSE record starts 2026-05-24. The code floor USGS_DAILY_VALUES_EARLIEST = 2022-08-05 is explicitly BORROWED from the vegetation layer, not source-imposed, and nothing confirms the archive walk has reached it -- using it would invent ~1,400 phantom gap-days. The bare min(observed_day) of 1990-10-01 is documented as a TRAP. Lag 2 is UNVERIFIED for this bbox.

Changing this floor invents phantom gap-days that the gap census will faithfully try to fill
forever. If you believe the floor is wrong, MEASURE the source, and state the measurement.

---

## Measured state, 2026-08-25

| measure | count |
|---|---|
| base rung days (z13, has data) | **91** |
| governed-absence days (z13) | **0** |
| days with a COMPLETE ladder | **91** |
| days with an INCOMPLETE ladder | **0** |
| days MISSING a base rung | **2** |
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

### 1. The 2 missing base days

Days with no base rung at all. Close them with:

```bash
uv run agri-cli parquet-drain --dry-run --selection missing --layer water-gauges   # confirm the count first
uv run agri-cli parquet-drain --selection missing --layer water-gauges --progress
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
- [ ] `--dry-run --selection ladder` reports `incomplete_ladder_days: 0` for `water-gauges`. **Expect it
      to equal the run's `emptied_ladders`, not necessarily zero** -- a rung that derives to no rows
      is retracted and re-selected forever by design. Reading it as zero-or-failure is a false
      alarm.
- [ ] `--dry-run --selection missing` reports `missing_days: 0` for `water-gauges`.
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

1. The before and after of both censuses for `water-gauges`, as the raw numbers.
2. Every file you touched, as `file:line`, with the load-bearing lines quoted.
3. What you did NOT do and why -- especially anything you found that belongs to another lane, or
   any cross-lane change you decided to surface rather than make.
4. Anything measured that contradicts this brief. This brief is a snapshot; the bucket is the
   truth.
