# Database

PlantGeo uses PostgreSQL for application state, community data, operational control, and small
reference dimensions. Environmental observations, forecasts, rasters, derived products, and
their availability indexes live in governed Parquet on object storage.

The current relational ownership is:

- `public`: authentication, teams, community requests, alerts, AI conversations, and application
  metadata.
- `geo`: layer catalogue, user-authored interventions, geometry versions, and raster catalogue
  metadata.
- `tracking`: assets, positions, geofences, and tracking alerts.
- `agri`: job control, source catalogue, expert labels, spatial cells, and lightweight
  species/location/profile lookup tables.

PostgreSQL is never an environmental serving fallback. A missing Parquet object or incomplete
availability record remains an explicit unavailable response until a source-direct backfill or
validation repair publishes the governed object.

## Fresh database

1. Install `postgis`, `pgcrypto`, `vector`, and `btree_gist`.
2. From `services/agri-data-service`, run `alembic upgrade head`.
3. From the repository root, run `node scripts/bootstrap-database.mjs`.

Alembic owns `agri` through
`services/agri-data-service/db/agri_baseline.sql`. Drizzle owns `public`, `geo`, and
`tracking` through `drizzle/0000_baseline.sql`. Both migration ledgers contain one current
baseline.

## Adding relational data

Add a relation only when it is application state, a control-plane ledger, or a bounded lookup
dimension. Environmental payloads and computed layer products must use the Parquet publication
contracts described in `docs/layer-lane-standard.md`.
