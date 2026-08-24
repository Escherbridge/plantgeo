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
