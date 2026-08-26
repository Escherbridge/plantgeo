"""Render one self-contained session brief per lane into conductor/layer-sessions/."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from lane_facts import LANES  # noqa: E402

OUT = pathlib.Path(r"C:\Users\atooz\Programming\plantgeo\conductor\layer-sessions")
OUT.mkdir(parents=True, exist_ok=True)

MEASURED = "2026-08-25"

SHARED_RULES = """\
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
"""

FOUR_RUNGS = """\
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
"""


def state_table(f: dict) -> str:
    rows = [
        ("base rung days (z13, has data)", f["base"]),
        ("governed-absence days (z13)", f["absent"]),
        ("days with a COMPLETE ladder", f["ladder_ok"]),
        ("days with an INCOMPLETE ladder", f["ladder_bad"]),
        ("days MISSING a base rung", f["missing"]),
        ("unfinished days (parts, no marker)", f["unfinished"]),
    ]
    out = ["| measure | count |", "|---|---|"]
    out += [f"| {k} | **{v}** |" for k, v in rows]
    return "\n".join(out)


SPECIAL = {
    "soil-survey": """\
### 0. READ THIS FIRST -- this lane is EMPTY, the recorded reason is WRONG, and it has its own track

`soil-survey` holds **zero base days**. Not a ladder gap, not a partial load -- no data at all, and
it is the only lane in that state.

**`conductor/RUNBOOK.md` section 0.29.1 records this lane's state AND its reason, and both are
wrong.** It is NOT `source_empty`. The export hits a key cap of 200,000 against a dataset of
200,001 keys, so it RAISES rather than returning a truncated result -- which means the lane never
drains on its own and never will, no matter how many times the drain runs. A lane that raises looks
identical to a lane with no work, which is why this went unnoticed. (Contrast `burn-severity`,
whose similar-looking zero IS correct.)

**Do NOT just raise the cap.** Owner call, 2026-08-25: *"soil survey is static but large so it may
need a different shape."* A vintage-keyed dataset of ~1.5M delineations may not want day
partitioning at all. That design question is chartered as
`conductor/tracks/soil_survey_lane_shape_20260825/`, which records four open questions and
deliberately answers none of them.

**So this session's scope is the DESIGN, not a cap bump.** Answer the track's questions with
measurements, and correct section 0.29.1. If you conclude the cap is genuinely all that is wrong,
say so with the evidence -- but that is a finding, not the starting assumption.

""",
    "sensors": """\
### 0. READ THIS FIRST -- there is a hard, unrecoverable deadline

NWS keeps a rolling **~6-day** window and Postgres is this lane's ONLY archive. **Days after
2026-08-31 become permanently unrecoverable** once that window rolls past them. Nothing downstream
can reconstruct them.

The forward writer is `plantgeo-ingest-cron`, whose `cronSchedule` was restored in config but
**takes effect only on a deploy**. If no deploy has happened, this lane is actively losing history
right now. Establish that first, before any backfill work -- a lane bleeding new days while you
repair old ones is the wrong order.

""",
    "weather-observations": """\
### 0. READ THIS FIRST -- this lane has NO contract, and half your job is writing it

The history floor below is a FALLBACK that is **not declared anywhere**. The census says so in its
own words: *"WRITE THAT HALF OF THE CONTRACT, then measure min(geo.feature_observation_day) for
this layer and replace both numbers."*

The trap is a name collision. `docs/lanes/weather-observations.md` describes the NASA POWER /
ERA5-Land archive -- **that is the `signal` stream, not this lane.** The producer THIS lane exports
is `ingest/open_meteo.py`'s `WEATHER_LAYER` current-conditions poll into `geo.features`, and it has
no contract content at all: no declared cadence, no horizon, no historical depth, no known-gaps
list. Anyone reading the docs file will confidently configure the wrong lane.

So `2026-08-01` and lag 2 are both deliberately conservative guesses, chosen so being wrong costs a
few dozen phantom gap-days instead of thousands. Measure the real values and replace them.

""",
}


def work_section(lane: str, f: dict) -> str:
    parts: list[str] = []

    if f["ladder_bad"]:
        parts.append(
            f"""\
### 1. The {f['ladder_bad']} incomplete-ladder days -- BLOCKED, and not by effort

`{lane}` is one of exactly three lanes whose coarse rungs CANNOT be derived. Measured
{MEASURED} by running the repair against production:

```
TierDerivationError: {lane}: the tier derivation names coordinate column(s)
[{f['cols']}] that the base table does not carry
```

The base rungs for these {f['ladder_bad']} days were written before the coordinate columns existed.
`--selection ladder` cannot fix them at any budget, and the drain correctly stops this lane after
three consecutive failures rather than burning the whole backlog rediscovering the same fault.

**The remedy the error itself names: retract and re-export, not re-derive.** That means an admin
retraction of the affected base days, then `parquet-drain --selection missing --layer {lane}`,
which IS source-connected -- so read the collision rule above before you start it.

**Confirm the boundary before retracting anything.** {f['ladder_ok']} of this lane's {f['base']}
base days already carry a complete ladder and are CORRECT -- they must not be retracted. Establish
exactly which days lack the columns before touching one; re-exporting a good day costs production
Postgres time to rewrite bytes that are already right, and `signal` alone measured 151 s for one
cold day."""
        )

    if f["missing"]:
        n = f["missing"]
        emph = " -- the largest export backlog of any lane" if n > 10 else ""
        parts.append(
            f"""\
### {len(parts) + 1}. The {n} missing base day{'s' if n != 1 else ''}{emph}

Days with no base rung at all. Close them with:

```bash
uv run agri-cli parquet-drain --dry-run --selection missing --layer {lane}   # confirm the count first
uv run agri-cli parquet-drain --selection missing --layer {lane} --progress
```

This is SOURCE-CONNECTED. It queries Postgres, so the collision rule applies."""
        )

    if f["unfinished"]:
        static_note = (
            """

**This lane is `static_lookup`, which makes the repair an ADMIN action, not a drain action.** A
static lane's partition day is a VERSION STAMP, so re-exporting one today would date the CURRENT
population as that old version -- silently rewriting history. The driver refuses for exactly this
reason. Retract it deliberately, or leave it and record why."""
            if f["nature"] == "static_lookup"
            else ""
        )
        parts.append(
            f"""\
### {len(parts) + 1}. {f['unfinished']} unfinished day/version -- parts on disk, no completion marker

The census reports this lane holds part files that no completion marker vouches for. Such a day is
neither served nor collected: `try_parse_partition_path` accepts it, but with no marker it reads
`incomplete` to every census.{static_note}"""
        )

    if f["absent"] > 100:
        parts.append(
            f"""\
### {len(parts) + 1}. {f['absent']} governed-absence days -- DO NOT "FIX" THESE

These are days upstream legitimately cannot serve, correctly recorded at z13. They are **not**
missing data and they need **no** export. {f['basis'].split('.')[0]}.

There IS a real gap here, but a different one: the absence is recorded at z13 ONLY, so at coarse
zoom these days read as unknown rather than as governed-absent. Propagating an absence to the
coarse rungs is a governed statement per tier and is deliberately classified as an ADMIN decision,
not drain work -- see `pipeline/parquet/drain.py:256-258`. Raise it; do not mint them from a repair
sweep."""
        )

    if f["nature"] == "static_lookup":
        parts.append(
            f"""\
### {len(parts) + 1}. Coverage is UNKNOWN, which is not the same claim as current

The census reports `static_state: watermark_unread` for this lane -- *"no source watermark was read
this run, so this lane's coverage is UNKNOWN -- that is not the same claim as being current"*.

Read the watermark so the lane can state its coverage as a fact. The partition day for a static
lane comes from its `sql/pipeline/lane_watermark_{lane.replace('-', '_')}.sql`, never from the
history floor and never from the cron's run date."""
        )

    if not parts:
        parts.append(
            f"""\
### 1. The warehouse side of this lane is COMPLETE -- verify, then move up the stack

All {f['base']} base days carry a complete four-rung ladder, nothing is missing, and nothing is
unfinished. Confirm that independently before believing it, then spend this session on the parts of
the lane contract ABOVE the warehouse: serving, the slider capability catalogue, and agent-tool
exposure. Those are listed in the Definition of done below and are where this lane is most likely
to be silently unfinished."""
        )

    body = "\n\n".join(parts)
    if lane in SPECIAL:
        body = SPECIAL[lane].rstrip() + "\n\n" + body
    return body


def render(lane: str, f: dict) -> str:
    fc = "YES" if f["fc"] else "no"
    fc_note = (
        "so it owes a forecast plane as well as an observed one"
        if f["fc"]
        else "a daily series is ALLOWED to decline a forecast; the nature is the ceiling, the shipped forecaster is the claim"
        if f["nature"] == "daily_series"
        else "not applicable to this nature"
    )
    return f"""# Session brief -- `{lane}` end to end

**Paste this whole file as the first message of a fresh session.** It is self-contained by design:
every number in it was measured against the production bucket on {MEASURED}, and nothing assumes you
read any other brief.

---

## Your scope, and it is exclusive

You own the **`{lane}`** lane and nothing else. Twelve sibling briefs exist and other sessions may
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
| nature | `{f['nature']}` |
| history floor | `{f['floor']}` |
| cadence | {f['cadence']} day(s) |
| publication lag | {f['lag']} day(s) |
| forecastable | {fc} |

**Forecast:** {fc_note}.

**Why the floor is what it is -- read this before you "extend the history".** Every lane in this
project has had a plausible-looking deeper floor proposed and rejected for a measured reason:

> {f['basis']}

Changing this floor invents phantom gap-days that the gap census will faithfully try to fill
forever. If you believe the floor is wrong, MEASURE the source, and state the measurement.

---

## Measured state, {MEASURED}

{state_table(f)}

Reproduce it yourself before acting -- HEAD and the bucket both move:

```bash
cd services/agri-data-service
uv run agri-cli parquet-drain --dry-run --selection ladder  | python -m json.tool   # ladder state
uv run agri-cli parquet-drain --dry-run --selection missing | python -m json.tool   # export state
uv run python scripts/warehouse_status.py                                           # bucket health
```

---

{FOUR_RUNGS}

---

## The work, in dependency order

{work_section(lane, f)}

---

{SHARED_RULES}

---

## Definition of done

The warehouse is only the first plane. `docs/layer-lane-standard.md` is the governing contract --
read it; the note at its head explains which half is superseded by
`conductor/code_styleguides/layer-lanes.md` under the Parquet architecture and which half still
binds. This lane is done when:

- [ ] Every day in the declared window is `data` or a governed `absent` at **all four rungs** --
      not just z13.
- [ ] `--dry-run --selection ladder` reports `incomplete_ladder_days: 0` for `{lane}`. **Expect it
      to equal the run's `emptied_ladders`, not necessarily zero** -- a rung that derives to no rows
      is retracted and re-selected forever by design. Reading it as zero-or-failure is a false
      alarm.
- [ ] `--dry-run --selection missing` reports `missing_days: 0` for `{lane}`.
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

1. The before and after of both censuses for `{lane}`, as the raw numbers.
2. Every file you touched, as `file:line`, with the load-bearing lines quoted.
3. What you did NOT do and why -- especially anything you found that belongs to another lane, or
   any cross-lane change you decided to surface rather than make.
4. Anything measured that contradicts this brief. This brief is a snapshot; the bucket is the
   truth.
"""


def main() -> None:
    written = []
    for lane, facts in sorted(LANES.items()):
        path = OUT / f"{lane}.md"
        path.write_text(render(lane, facts), encoding="utf-8")
        written.append((lane, len(path.read_text(encoding="utf-8").splitlines())))
    for lane, lines in written:
        print(f"  {lane:24} {lines:4} lines")
    print(f"\n{len(written)} briefs -> {OUT}")


if __name__ == "__main__":
    main()
