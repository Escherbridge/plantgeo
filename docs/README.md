# PlantGeo Documentation

PlantGeo is an open-source geospatial action-network platform for publishing,
exploring, and coordinating evidence-backed environmental work. The repository
contains a Next.js/MapLibre application, PostGIS-backed operational APIs, a
Martin vector-tile boundary, and a Python/Alembic foundation for governed
environmental data and locally executed predictive workflows.

## Architecture pivot in progress — read this first

On 2026-08-22 the owner decided to move every data plane out of Postgres into
day-partitioned Parquet on Railway object storage, computed at ingestion time
and read by DuckDB (spatial extension) + Polars. **Postgres is retained for
community features only.** Martin keeps serving tiles, from generated PMTiles
instead of PostGIS functions. ML is frozen and moving to
`services/agri-data-service/src/agri_data_service/ml/`, eventually a separate
Mojo service. The map may break during the transition — the owner has
explicitly accepted that.

The decision is `conductor/RUNBOOK.md` §0.23; the concurrent-stream execution
plan is §0.24. The binding per-lane style guide for the new architecture is
`conductor/code_styleguides/layer-lanes.md`. Implementation has barely started
as of this writing — most of the documents below still describe the
architecture actually running in production today, and are marked accordingly.

This `docs/` tree was reconciled against the §0.23/§0.24 pivot on **2026-08-22**:
documents whose claims the pivot will make wrong now carry a dated status
banner at the top pointing back to RUNBOOK §0.23/§0.24, their bodies otherwise
untouched. See "Documentation index" below for which is which.

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

- the replacement Railway database verification for PostGIS, pgvector, pgcrypto,
  roles, and migrations is complete; TimescaleDB was removed on 2026-08-25 after
  holding only an empty hypertable with no continuous aggregates;
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
uv run agri-service ops db-status
uv run sanic agri_data_service.app:create_app --factory --dev --port 8000
```

Do not point local reset/migration experiments at Railway. The Python service
does not provide a destructive database-reset command.

The map layers stay empty until data is ingested. To populate them on your own
machine, follow [Rebuilding the dataset from a clone](./rebuilding-the-dataset.md);
it covers the separate local warehouse on port `5442`, which credentials each
source needs, and the plan-generation step that every historical backfill
depends on.

## Documentation index

Grouped by how much you should trust each document's claims about the
**current or upcoming architecture**, not by topic. A CURRENT document may
still describe today's Postgres-based system accurately while being on a path
to supersession — read its status, if any, before relying on it.

### Current — architecture-neutral or unaffected by the pivot

- [Layer lane standard](./layer-lane-standard.md) — the pre-pivot per-layer
  "definition of done." Carries a 2026-08-22 relationship note at the top:
  its Postgres-specific mechanics are superseded by
  `conductor/code_styleguides/layer-lanes.md` (the binding contract for new
  lane work), but its process-level requirements — gap detection, catalogue
  registration, slider wiring, agent-tool exposure — have no restated
  Parquet-era equivalent yet and remain the operative guidance for those
  outcomes. Read the note before either document.
- [Ecological knowledge source register](./ecological-knowledge-source-register.md)
  — evidence tiers and governed sources for strategies, amendments, plants,
  seeds, and companion claims; unaffected by storage engine.
- [North America intervention data source matrix](./north-america-intervention-source-matrix.md),
  [Upstream datasets we are not pulling](./unused-upstream-datasets.md),
  [Finer climate resolution options](./finer-climate-resolution-options.md),
  [Seasonal forecast scoping](./seasonal_forecast_scoping.md) — source-system
  facts (coverage, resolution, licensing); these don't go stale from a storage
  backend change.
- [Environment variables](./env-vars.md) — server/client separation and
  production configuration.
- [Railway operations](./deployment.md) — shared-project allowlist and deploy
  sequence; largely process, not storage-backend-specific, but re-verify the
  Martin/database cutover language against RUNBOOK §0.23 before trusting it.
- [Local PlantGeo data load and future promotion](./local-data-promotion.md) —
  which local databases are eligible promotion sources; a local-Postgres
  concern independent of the pivot's target architecture.
- [API reference](./api-reference.md), [components](./components.md), and
  [diagrams.md](./diagrams.md) — application-surface inventories; verify each
  entry against current code before treating it as an operational promise.
- [`docs/runbooks/`](./runbooks/) — operational runbooks:
  [durable backfill lanes](./runbooks/durable-backfill-lanes.md) (the
  `agri.job_*` ledger — an orchestration mechanism the pivot has not addressed
  either way), [PMTiles PNW basemap](./runbooks/pmtiles-pnw.md) (R2 keeps
  serving basemap tiles per RUNBOOK §0.23.4, unaffected), and
  [USGS sentinel-value cleanup](./runbooks/usgs-sentinel-cleanup.md) (a still-open
  proposed `DELETE` against `geo.features` — now also relevant to run *before*
  that table is exported to Parquet, per RUNBOOK §0.23.8 step 5).

### Flagged — accurate for today, will mislead as the pivot proceeds

Each carries a dated status banner at the top pointing to
`conductor/RUNBOOK.md` §0.23/§0.24. Bodies are untouched.

- [Architecture](./architecture.md) — major runtime boundaries and data flows;
  PostGIS/Martin framing is what's moving.
- [Database](./database.md) and [DBML](./schema.dbml) — PostgreSQL+PostGIS
  schema; only the auth/community tables stay in Postgres scope.
- [Data ingestion and serving contract](./data-ingestion-and-serving-contract.md)
  — its Governing rule ("publish to the platform PostgreSQL database") is the
  specific rule the pivot replaces.
- [Rebuilding the dataset from a clone](./rebuilding-the-dataset.md) — the
  local-warehouse Postgres bootstrap path predates the pivot; no Parquet-era
  rebuild guide exists yet.
- [Backend services reference](./services.md) — some of the 30+ services read
  or write Postgres planes targeted for migration.
- [Holonic and Kimball modeling standard](./holonic-kimball-modeling.md) — the
  Postgres materialized-view warehouse this describes is what Parquet now
  replaces.
- [SQL-first forecasting framework](./sql-forecasting-framework.md) — ML is
  frozen and each lane's own Monte Carlo forecast now serves forecasted values
  instead of this framework.
- [Historical ingestion runbook](./historical-backfill-runbook.md) — targets
  the Postgres warehouse the pivot is leaving.
- [Predictive environmental intelligence](./predictive-environmental-intelligence-spec.md)
  — ML domain (frozen) and assumes "PlantGeo's single PostGIS database."
- [Strategy-selection training contract](./strategy-selection-training.md) —
  ML domain (frozen); Postgres substrate assumption is not current.
- [Pending migration 0029](./pending-migrations/0029-pre-aggregation.md) and
  [0033](./pending-migrations/0033-features-partitioning.md) — optimize
  Postgres relations (`geo.mv_signal_cell_daily`, `geo.features`) that are
  named in RUNBOOK §0.23.2 as export targets; confirm relevance before
  applying either.

### Historical — point-in-time records, not current guidance

- [`docs/reports/`](./reports/) and [`docs/reviews/`](./reviews/) — dated
  session reports, audits, and passoffs. Kept in place per this tree's
  existing convention (`<topic>-<date>.md`); none are indexes of the current
  system. [`data-readiness-2026-08-02.md`](./reviews/data-readiness-2026-08-02.md)
  in particular is flagged doubly stale — unverified even at the time it was
  written, and now also pre-pivot.
  `docs/reports/passoff-2026-08-07.md` and
  `docs/reports/railway-deployment-plan-2026-08-14.md` were moved here from
  the top level of `docs/` on 2026-08-22 as dead, unlinked material — see
  their archive notes for why.
- [`docs/research/timescale-pivot-2026-08-17/`](./research/timescale-pivot-2026-08-17/)
  — the 14-agent research pass that produced the "no OLAP engine can replace
  PostGIS" verdict. `report.md` and `BRIEF.md` carry a 2026-08-22 note: RUNBOOK
  §0.23.3 explicitly supersedes that verdict for a differently-scoped
  question (ingestion-time compute vs. in-place replacement); the underlying
  measurements remain part of the evidence the pivot rests on. The rest of the
  bundle (`FACTS.md`, `persona-findings/`, `synthesis/`, `evidence/`) is raw
  research input and still an accurate record of what was found.

## Current stack

This describes what is running today. It does not yet reflect the 2026-08-22
architecture pivot (see above) — Parquet/DuckDB/Polars adoption has barely
started, so the table below is still accurate, not aspirational.

| Boundary | Technology |
| --- | --- |
| Web | Next.js 16 App Router, React 19, TypeScript |
| Map | MapLibre GL JS 5, deck.gl 9, Three.js |
| Operational data | PostgreSQL 18.4 + PostGIS 3.6 on `plantgeo-spatiotemporal-db` (formerly with TimescaleDB until 2026-08-25, which held only an empty hypertable and no continuous aggregates) |
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

Deploying `plantgeo-main` is a push to `main`: Railway builds the image (the
build stage runs the data-boundary, type, lint, and test gates), applies pending
Drizzle migrations through `preDeployCommand`, then gates traffic on
`/api/ready`. A green deploy does not authorize a database cutover, Martin
publication, or a data-release certification.
