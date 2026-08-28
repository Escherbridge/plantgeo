# PlantGeo

An open-source geospatial action-network platform for publishing, exploring, and
coordinating evidence-backed environmental work. The repository contains a
Next.js/MapLibre application, PostGIS-backed operational APIs, a Martin
vector-tile boundary, and a Python/Alembic service for governed environmental
data and locally executed predictive workflows.

PlantGeo is in a hardening and data-foundation phase, not feature-complete
production. Environmental read paths return persisted facts or an explicit
unavailable state; they never substitute demonstration data. See
[docs/README.md](./docs/README.md) for the full operational status and the
documentation index.

## Repository layout

| Path | Contents |
| --- | --- |
| `src/app/` | Next.js App Router pages and route handlers |
| `src/components/map/` | MapLibre GL JS, deck.gl, and Three.js map surface |
| `src/lib/server/` | Drizzle schema, tRPC routers, server-side services |
| `services/agri-data-service/` | Python/Sanic data service, `agri-service`, Alembic `agri` schema |
| `infra/` | Compose files, Martin config, local-warehouse bootstrap SQL |
| `docs/` | Architecture, runbooks, contracts, environment reference |

## Cold start

Two planes, and they are worth separating before you type anything:

- **The application plane** — Next.js plus its own PostgreSQL database, managed
  by Drizzle. This is all you need to run the app and see the map.
- **The warehouse plane** — a dedicated local PostgreSQL container on loopback
  port `5442`, managed by Alembic, that holds governed environmental data. This
  is what you need to *build the dataset*. It is deliberately separate from the
  application database and from any other database on your machine.

Nothing here requires Railway, Cloudflare R2, or any hosted account. What you
cannot reproduce from a clone is listed in
[docs/rebuilding-the-dataset.md](./docs/rebuilding-the-dataset.md).

### Prerequisites

Node.js 22, npm, Podman (or Docker) with Compose, Python 3.12, and
[`uv`](https://docs.astral.sh/uv/). A `psql` client is useful for the warehouse
bootstrap gates.

### 1. Run the application

```powershell
git clone <this-repository>
Set-Location plantgeo

Copy-Item .env.example .env.local
# Set POSTGRES_PASSWORD and the other local-only values.

npm install
npm run docker:up
npm run db:migrate
npm run dev
```

The development server listens on `http://localhost:3001`. The production
container defaults to port `3000` and honors Railway's `PORT`.

`npm run docker:up` runs `podman compose up -d`. If you use Docker rather than
Podman, run `docker compose up -d` directly — the npm scripts hardcode `podman`.

### 2. Run the data service

```powershell
Set-Location services/agri-data-service
Copy-Item .env.example .env
uv sync --locked --all-extras
uv run agri-service ops db-status
```

`db-status` reports whether the service can reach its database and which Alembic
revision is applied. To serve the API:

```powershell
uv run sanic agri_data_service.app:create_app --factory --dev --port 8000
```

Do not point local reset or migration experiments at a hosted database. The
Python service intentionally provides no destructive database-reset command.

### 3. Build the dataset

The map layers are empty until you ingest something. The warehouse bootstrap,
the per-source credential table, the plan-regeneration step every historical
backfill depends on, and the honest limits are all in one place:

**[docs/rebuilding-the-dataset.md](./docs/rebuilding-the-dataset.md)**

Read it before running any `agri-service data ingest-*` or `agri-service data historical-*` verb. Most
sources need no credential at all; exactly one hosted account (Copernicus, for
ERA5-Land) requires a licence accepted in a browser.

## Verification

Run the integrated checks after a batch of changes, not between individual
edits:

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

## Stack

| Boundary | Technology |
| --- | --- |
| Web | Next.js 16 App Router, React 19, TypeScript |
| Map | MapLibre GL JS 5, deck.gl 9, Three.js |
| Application schema | Drizzle ORM on PostgreSQL |
| Environmental/ML schema | SQLAlchemy + Alembic (`agri` schema only) |
| Tile serving | Martin with allowlisted MVT functions |
| Cache/events | Redis 7 for cache and pub/sub, never durable job state |
| Predictive compute | Local Python runner with durable manifests and checkpoints |

## Documentation

Start at [docs/README.md](./docs/README.md). The pages a newcomer reaches for
first:

- [Rebuilding the dataset](./docs/rebuilding-the-dataset.md) — credentials,
  warehouse bootstrap, ingestion verbs, and what cannot be reproduced.
- [Architecture](./docs/architecture.md) — runtime boundaries and data flows.
- [Environment variables](./docs/env-vars.md) — server/client separation.
- [Historical ingestion runbook](./docs/historical-backfill-runbook.md) — the
  four-year backfill in full detail.
- [Data ingestion and serving contract](./docs/data-ingestion-and-serving-contract.md)
  — warehouse-first custody and browser boundaries.
