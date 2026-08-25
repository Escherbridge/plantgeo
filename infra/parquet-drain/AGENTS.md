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
because the image is just `agri-cli`; they share nothing else.

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

**"Backlog zero" was measured at z13 only.** RUNBOOK 0.40.2: no coarse rung (z9/z5/z0) exists for
any lane, and the written `signal` base carries 10 columns with **no `cell_longitude`/`cell_latitude`**,
so all ~1,560 signal days must be **re-exported** before a coarse rung can be derived at all. That
re-export is pivot slice `d1`, and it is the longest pole in the whole programme. This service is
exactly what makes it tractable -- the measurements above show a drained day is dominated by
round-trip latency, not data, which is why it runs next to the bucket instead of from a laptop.
**Deleting it would destroy the in-region runner `d1` needs.**

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
doing no work while competing with Postgres. `d1` owns `drain.py` and rewrites it as a fused
drain + tier derivation; give it a bounded run, and honour "one writer at a time" against the
ingest cron.
