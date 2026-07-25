# PlantGeo Documentation

PlantGeo is an open-source geospatial action-network platform for publishing,
exploring, and coordinating evidence-backed environmental work. The repository
contains a Next.js/MapLibre application, PostGIS-backed operational APIs, a
Martin vector-tile boundary, and a Python/Alembic foundation for governed
environmental data and locally executed predictive workflows.

## Operational status

The platform is in a hardening and data-foundation phase, not feature-complete
production. Current environmental read paths either return persisted facts or
an explicit unavailable state. They do not silently substitute demonstrations,
coordinate heuristics, or model-like scores. The action-network worker pipeline
is present, but its serving endpoint is intentionally inactive until reviewed,
access-safe opportunity waypoints are published.

Partner collaboration is account and workspace based: raw requests stay within
the submitting account or an explicitly authorized team. Legacy priority-zone
aggregation, external supplier matching, and regional AI remain inactive. The
AI path will require a real paid-account or partner entitlement with durable
usage reservation before any model call is enabled.

The following remain gated:

- the replacement Railway database must be verified for PostGIS, TimescaleDB,
  pgvector, pgcrypto, roles, and migrations before cutover;
- Martin remains private/stopped until that database gate passes;
- no danger, intervention-effect, amendment, or waypoint model has been trained
  or promoted;
- locally published model artifacts remain non-serving until a typed loader and
  product-specific validation gate promotes them; and
- Valhalla and Photon are deferred rather than assumed to be running.

## Local development

Requirements: Node.js 22, npm, Podman/Docker Compose, Python 3.12, and `uv`.

```powershell
Copy-Item .env.example .env.local
# Set POSTGRES_PASSWORD and other local-only values.

npm install
npm run docker:up
npm run db:migrate
npm run dev
```

The development server listens on `http://localhost:3001`. The production
container defaults to port `3000` and honors Railway's `PORT` variable.

For the Python data service:

```powershell
Set-Location services/agri-data-service
Copy-Item .env.example .env
uv sync --locked --all-extras
uv run agri-cli db-status
uv run sanic agri_data_service.app:create_app --factory --dev --port 8000
```

Do not point local reset/migration experiments at Railway. The Python service
does not provide a destructive database-reset command.

## Documentation index

- [Architecture](./architecture.md) — major runtime boundaries and data flows.
- [Data ingestion and serving contract](./data-ingestion-and-serving-contract.md)
  — warehouse-first custody, exceptions, and browser boundaries.
- [Historical ingestion runbook](./historical-backfill-runbook.md) — local
  four-year backfill, validation, Railway promotion, and recurring-run gates.
- [Predictive environmental intelligence](./predictive-environmental-intelligence-spec.md)
  — three decision products, local execution, durable publication, waypoints,
  safety gates, and cost estimates.
- [SQL-first forecasting framework](./sql-forecasting-framework.md) — typed,
  release-pinned metric forecasts, quality gates, immutable receipts, and the
  serving boundary distinct from generic artifact-only publication.
- [North America intervention pilot report](./reports/north-america-intervention-pilot-2026-07-23.md)
  — Boise open-data capture, PostGIS evidence, forecast evaluation, validation,
  costs, confidence limits, and the selective upstream path.
- [Ecological knowledge source register](./ecological-knowledge-source-register.md)
  — evidence tiers and governed sources for strategies, amendments, plants,
  seeds, and companion claims.
- [Railway operations](./deployment.md) — exact shared-project allowlist,
  replacement-database cutover, Martin gate, and deploy kill switch.
- [Environment variables](./env-vars.md) — server/client separation and
  production configuration.
- [Database](./database.md) and [DBML](./schema.dbml) — schema ownership and
  existing relational model.
- [API reference](./api-reference.md), [services](./services.md), and
  [components](./components.md) — legacy surface inventories; verify each entry
  against current code before treating it as an operational promise.

## Current stack

| Boundary | Technology |
| --- | --- |
| Web | Next.js 16 App Router, React 19, TypeScript |
| Map | MapLibre GL JS 5, deck.gl 9, Three.js |
| Operational data | PostgreSQL; target PostGIS/TimescaleDB capabilities remain cutover-gated |
| Application schema | Drizzle ORM |
| Environmental/ML schema | SQLAlchemy + Alembic (`agri` only) |
| Tile serving | Martin 1.10.1 with allowlisted MVT functions; not yet production-active |
| Cache/events | Redis 7 for cache/pub-sub, never durable job state |
| Predictive compute | Local Python runner with durable manifests/checkpoints |
| Deployment | Railway Pro serving plane + Cloudflare R2/CDN artifacts |

## Verification commands

Run the integrated checks after completing a batch of changes:

```powershell
npm run lint
npm run type-check
npm run check:data-boundary
npm test
npm run build

Set-Location services/agri-data-service
uv sync --locked --all-extras
uv run ruff check .
uv run mypy src
uv run pytest
```

Production deployment is separately gated. A successful local/CI build does not
authorize database migration, Martin publication, or Railway cutover.
