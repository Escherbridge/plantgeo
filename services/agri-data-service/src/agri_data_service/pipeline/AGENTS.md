# Layer L2: Pipeline

## Responsibility
Upstream acquisition, external API fetching, raw tile/data backfill routines (`ingest/` and `historical_*`).

## Dependency Rules
- **May import**: `foundation` (L0), `warehouse` (L1).
- **May NOT import**: `method` (L1), `planes` (L3), `interface` (L4).
