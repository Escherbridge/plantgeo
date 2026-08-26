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
