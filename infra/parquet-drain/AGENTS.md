# `plantgeo-parquet-drain`

The historical Parquet backlog, drained from inside Railway instead of from a laptop.

## Why this service exists

`parquet-drain` is dominated by round-trip latency, not by data. Measured on 2026-08-24 against
production from a local Windows machine: a `burn-severity` absence costs ~1.1 s, a
`fire-detections` day carrying ~33 rows costs ~2.95 s, and a `signal` day carrying 16,127 rows
costs ~7.2 s. Four hundred and eighty-nine times the data buys 4.25 extra seconds, so a written
day is roughly **2.85 s of fixed cost plus 0.27 ms per row**. That fixed cost is a serialized
chain of object-store calls -- every zoom rung does a HEAD (`absence_exists`), the part PUT and
the marker PUT, and part 0 also DELETEs the previous completion marker, with
`BotoObjectStoreBackend.put` issuing them one at a time. Sixteen sequential round-trips at a
home connection's latency is where three quarters of the drain's life went.

Running the same verb next to the database and the bucket is what removes that, and it needs no
code change -- only a shorter wire.

## Why it is not the ingest cron

`infra/cron-ingest/` runs the forward path: `ingest-all`, `jobs-pulse`, `parquet-gap-fill`,
newest-first, on a schedule, with `restartPolicyType NEVER` because a cron tick must not overlap
itself. This service is the opposite job -- oldest-first, continuous, and restarted forever --
so it gets its own config rather than another branch inside that one. They share the image
because the image is just `agri-service`; they share nothing else.

`restartPolicyType ALWAYS` is deliberate and replaces the local supervisor script. The drain
survives a dropped query (the day is reported `raised` and the walk continues) but nothing
catches a failed RECONNECT, so on 2026-08-24 it died outright on `ConnectionResetError
[WinError 64]` raised inside asyncpg's CONNECT path. Railway restarting the container is the
supervision that makes that recoverable.

## The budget is an hour, not the cron's half hour

Every pass re-resolves lane watermarks and rebuilds the gap census by listing the bucket, which
is pure overhead paid per pass. With no forward path to yield to here, a longer budget amortises
it. The verb never kills a day mid-write: the budget stops it STARTING a new lane-day, and a day
already in hand always finishes.

## Retiring it

When the backlog reaches zero this service has no work left -- `parquet-gap-fill` on the ingest
cron keeps the warehouse current on its own. Delete the service; there is nothing to undo.

### That condition is NOT met, and the service is DISABLED rather than deleted (2026-08-25)

**"Backlog zero" was measured at z13 only.** Both of RUNBOOK 0.40.2's key claims have since been refuted by measurement:

1. **"No coarse rung (z9/z5/z0) exists for any lane"** — Contradicted by a live census run on 2026-08-25 (`agri-service data parquet-drain --dry-run --selection ladder`), which reported bucket-wide completion marks: `{'00': 10473, '05': 10473, '09': 10473, '13': 11510}` — three coarse rungs, each with 10,473 marks.

2. **"The written `signal` base carries no `cell_longitude`/`cell_latitude`, so all ~1,560 signal days must be re-exported"** — Also refuted. The RUNBOOK's own 2026-08-25 retro entry states: "Assumption falsified: §0.40.2's 'no coarse rung exists for any lane' and 'the signal base lacks positions'. Both dead. The ~1,560-day re-export I briefed as the longest pole **was already done** — a background loop kept working after the listing behind §0.40.2 was taken."

**Signal does NOT require WHOLESALE re-export -- but the 222 days that remain DO, and this correction
supersedes the paragraph above.** A 2026-08-25 census reported `base_days: 1560`,
`ladder_complete_days: 1338`, `incomplete_ladder_days: 222`. The 1,338 already carry a complete
ladder, so 0.40.2's "all ~1,560 must be re-exported" is genuinely wrong.

**The rest of 0.40.2 is NOT wrong, and calling it "dead" went too far.** Running
`parquet-drain --selection ladder` against production the same day, the derivation FAILED on every
signal day it tried, with the same fault on `vegetation` and `sensors`:

```
TierDerivationError: signal: the tier derivation names coordinate column(s)
['cell_longitude', 'cell_latitude'] that the base table does not carry; it has
['allowed_client_exposure', 'cell_id', 'coverage_fraction', 'newest_observed_at',
 'normalized_unit', 'normalized_value', 'observation_count', 'observed_day',
 'signal_name', 'support_key']
```

So "the signal base lacks positions" is TRUE -- of precisely the days still outstanding. The
re-export that carried positions covered 1,338 days and stopped; the 222 it never reached are the
ones the census still selects. Both statements were half right, which is why the bucket listing
alone could not settle it: **a lane-day can be base-complete and still be underivable, and only
running the derivation tells you which.**

Measured split of the 1,037 incomplete-ladder days, 2026-08-25:

| repairable by derivation | 585 | fire-detections 222, drought 208, water-gauges 91, fire-perimeters 44, weather-observations 18, calendar 1, evacuation-zones 1 |
|---|---|---|
| **blocked, needs retract + re-export** | **452** | **signal 222, vegetation 205, sensors 25** |

The 452 are NOT ladder work. `--selection ladder` cannot fix them and stops each lane after three
consecutive failures rather than burning the backlog discovering that. They need the base retracted
and re-exported from Postgres (`--selection missing`), which is a source-connected job and collides
with the ingest cron -- see the reconnection warning below.

**The lesson this retro drew, stated plainly so it is not re-learned a third time: list the bucket before planning Parquet work.** The §0.40.2 listing was already stale when it was written, because a background loop kept working after it was taken.

This service is disabled because it is the in-region runner, and the measurements above show a drained day is dominated by round-trip latency, not data, which is why it runs next to the bucket instead of from a laptop. **Deleting it would remove the only candidate for doing bulk work where it matters — next to the bucket.**

**What was done instead:** `railway service source disconnect --service plantgeo-parquet-drain`.
The repo source is gone, so the service no longer redeploys on every push (RUNBOOK 0.40.1) -- the
collision in RUNBOOK 0.42.9 step 5 is now prevented by construction rather than by a rule someone
has to remember. Variables and service config survive. `plantgeo-ingest-cron` was deliberately left
connected: restoring its `cronSchedule` is lane A's `s0`, and its upstream keeps only ~6 days.

**To bring it back for `d1`:**

```
railway service source connect --repo Escherbridge/plantgeo --branch main --service plantgeo-parquet-drain
```

**Change the start command when you do.** The `while true ... sleep 15` loop in `railway.json` was
right for a bulk backlog and is wrong now: with the backlog at z13-zero it spins forever on one
line -- `burn-severity 2024-08-22 raised: the base rung is written but its coarse rungs are not` --
doing no work while competing with Postgres. The `infra/cron-ingest/railway.json` has its `cronSchedule`
restored as of 2026-08-25 and will be armed on the next deploy. Without a bounded, non-overlapping run,
reconnecting this drain resurrects the fe9b241 collision: a `signal` lane-day measured ~8 s alone versus
~25 MINUTES beside a cron tick, because both processes sit on IO/DataFileRead, competing for the same disk -- it is disk contention, not lock contention, and looking for a lock will not find it. `d1` owns
`drain.py` and rewrites it as a fused drain + tier derivation with explicit bounded-run semantics; defer
reconnection until that work is done, and honour "one writer at a time" against the ingest cron.
