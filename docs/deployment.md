# Deployment

PlantGeo deploys from `main` to Railway. The application image runs the repository quality gates,
the pre-deploy step applies the current Drizzle baseline ledger, and `/api/ready` verifies the
database migration receipt before traffic moves.

## Services

- The Next.js application serves the UI and transactional APIs.
- The Python data service reads governed Parquet and runs source-direct publication jobs.
- PostgreSQL stores application, community, control, and lookup data.
- Redis provides cache, pub/sub, and job coordination.
- Object storage holds Parquet datasets, availability indexes, PMTiles, and raster products.

## Database order

For an empty database:

1. Install `postgis`, `pgcrypto`, `vector`, and `btree_gist`.
2. Run `alembic upgrade head` from `services/agri-data-service`.
3. Run `node scripts/bootstrap-database.mjs` from the repository root.

The production service uses `scripts/migrate.mjs` as its pre-deploy command. Any new relational
migration must update its matching readiness pin in the same change.

## Environmental data

Environmental ingestion writes source captures and normalized products directly to Parquet.
Serving reads Parquet through the Python data service. PostgreSQL has no environmental observation,
forecast, raster-payload, or fallback role. Backfills and repairs replay the original source and
publish a new validated Parquet object.

## Release checks

Run the integrated type, lint, boundary, JavaScript, and Python test sweep before pushing. After
deployment, verify `/api/ready`, one current and one historical Parquet day for each affected
layer, availability-index agreement, and the relevant agent tool at the UI-selected day.
