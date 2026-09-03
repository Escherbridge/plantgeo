# Layer L2: Pipeline

## Responsibility
Upstream acquisition, external API fetching, raw tile/data backfill routines (`ingest/` and `historical_*`).

## Dependency Rules
- **May import**: `foundation` (L0), `warehouse` (L1).
- **May NOT import**: `method` (L1), `planes` (L3), `interface` (L4).

## Shared governed source census

`vegetation_source.py` owns the bounded PostgreSQL cell-day census shared by vegetation writers,
operators, and validators. Validation modules may re-export that contract for compatibility, but
sibling validation modules import the lower pipeline module so pytest's layer contract remains
acyclic.

## Availability follows publication, never leads it

`parquet/availability_extension.py` is the only path from a terminal lane-day into that lane's
availability generation, and it runs strictly AFTER the day's completion or governed-absence marker.
See `parquet/AGENTS.md`, "`availability_extension.py` — the terminal day joins the index", for the
write ledger it reads its receipts from, the five typed outcomes, the retry claim, and why a lane
without a bootstrap still completes its days.

`db/vegetation_publication.py` owns the vegetation-wide advisory barrier and durable per-day queue
operations. The 45-day ingestion lookback spans up to 46 inclusive UTC dates; publication rechecks
that boundary every tick and drains durable pending work independently of whether ingestion emitted
a callback in that tick.
