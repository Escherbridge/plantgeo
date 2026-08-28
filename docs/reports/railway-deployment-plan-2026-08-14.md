# PlantGeo Railway Deployment Plan

> **ARCHIVED 2026-08-22.** Moved here from `docs/railway-deployment-plan.md`
> during a docs audit; not referenced from `docs/README.md` or elsewhere in the
> tree. This plan's core mechanism — an `agri.job_schedules` table via migration
> `0026_agri_job_schedules.sql`, replacing `infra/cron-*` with a single in-app
> scheduler loop — does not match the repo: no `0026` migration or
> `job_schedules` table exists anywhere in `drizzle/` or
> `services/agri-data-service/alembic/versions/`, and current infra
> (`infra/cron-ingest/`, `infra/cron-mtbs/`, `infra/cron-soilgrids/`) plus the
> actually-adopted `agri.job_*` ledger design (`docs/runbooks/durable-backfill-lanes.md`)
> took a different shape — one lane, one `JobRun`, Railway cron triggers per
> lane family, not a consolidated scheduler. This plan appears to have been
> superseded before it was ever implemented, independent of the 2026-08-22
> architecture pivot (`conductor/RUNBOOK.md` §0.23/§0.24), which additionally
> retires the Postgres data planes this plan assumed. Kept as a historical
> record only.

## 1. Overview & Architectural Consolidation
This deployment plan defines the transition from legacy individual Railway cron services to the **Postgres-backed In-App Job Runner** running inside `plantgeo-dataservice`.

### Key Benefits
- **Eliminates Container Overhead**: Replaces 20+ individual Railway cron services (`infra/cron-*`) with a single background scheduler loop inside `plantgeo-dataservice`.
- **Multi-Instance Pod Safety**: Uses Postgres transaction-bound advisory locks (`pg_try_advisory_xact_lock`) to prevent race conditions across redundant worker pods.
- **Admin Control & Dynamic Cadence**: Allows real-time toggling of stream lanes, cron cadence updates, and manual "Run Now" triggers via `/admin/jobs`.

---

## 2. Target Railway Topology

| Service Name | Purpose | Configuration & Command |
| --- | --- | --- |
| `plantgeo-main` | Next.js 15 App & Admin UI | `npm start` (Runs tRPC `jobsRouter` & `/admin/jobs` dashboard) |
| `plantgeo-dataservice` | Python Sanic Service & Scheduler | `uv run sanic agri_data_service.app:create_app --factory` (Runs `InAppScheduler` loop & `/api/v1/jobs/trigger`) |
| `plantgeo-martin` | Vector Tile Server | `martin` (Serves `geo.strategy_recommendations_tiles` MVT) |
| `plantgeo-spatiotemporal-db` | TimescaleDB / PostGIS Database | PostgreSQL 16 + PostGIS 3.4 (Holds `agri.job_schedules` & materialized views) |

---

## 3. Deprecated Railway Cron Services Sunset
The following Railway cron services are superseded by `agri.job_schedules` entries in `plantgeo-dataservice`:

- `plantgeo-ingest-firms` -> Replaced by `firms-fire` job schedule
- `plantgeo-ingest-streamflow` -> Replaced by `usgs-streamflow` job schedule
- `plantgeo-ingest-weather` -> Replaced by `era5-weather` job schedule
- `plantgeo-ingest-drought` -> Replaced by `usdm-drought` job schedule
- `plantgeo-ingest-sensors` -> Replaced by `usda-soil` job schedule

### Action Items for Railway Operator
1. Delete or pause legacy `plantgeo-ingest-*` cron services in the Railway dashboard.
2. Confirm `agri.job_schedules` table is populated via migration `0026_agri_job_schedules.sql`.
3. Verify `plantgeo-dataservice` environment has `DATABASE_URL` / `COMBINED_LOCAL_DATABASE_URL` set.

---

## 4. Admin Access & User Promotion
The account `atoozmc@gmail.com` has been promoted to `admin` with `verified = true` via migration `0026_agri_job_schedules.sql` and `scripts/promote-user.ts`.
- **Access URL**: `https://<plantgeo-domain>/admin/jobs`
- **Moderation Queue**: `https://<plantgeo-domain>/moderation`

---

## 5. Verification & Rollout Steps
1. Push `main` branch to GitHub (`Escherbridge/plantgeo`).
2. Railway executes pre-deploy migration script `scripts/migrate.mjs` applying `0026` and `0027`.
3. Health check `/api/ready` confirms Drizzle migration contract `0027_ml_strategy_materialized_views`.
4. Open `/admin/jobs` dashboard and verify live stream lane status and manual trigger controls.
