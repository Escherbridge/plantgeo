---
type: specification
---

# Conductor Track Specification: In-App Postgres Job Runner & Platform Admin Control Panel

## Track ID: `inapp_job_runner_admin_20260814`

### Overview
This track replaces individual external cloud crons (e.g. Railway crons) with an in-app, durable PostgreSQL-backed Job Runner engine running inside `services/agri-data-service`. It builds upon the existing `agri.job_*` ledger and introduces a persistent schedule configuration table (`agri.job_schedules`), an automated asyncio worker loop with work-item leasing, and a Next.js **Platform Admin Control Panel** (`/admin/jobs`) equipped with stream toggles, manual trigger controls, cron cadence editing, and live execution logging.

### Objectives
1. Eliminate external cloud cron dependencies (Railway cron services) by embedding an in-system, durable job runner and scheduler into `services/agri-data-service`.
2. Introduce Postgres schema `agri.job_schedules` to persist stream ingestion schedules (FIRMS fire alerts, USGS streamflow, ERA5 weather, SoilGrids, USDM drought) with active toggles (`enabled: true/false`), target cadences, rate budgets, and last-run timestamps.
3. Build an asyncio scheduler loop with fenced lease locks and slice budgets, preventing concurrent worker collisions across instances.
4. Develop Next.js Admin UI (`/admin/jobs` & `JobRunnerDashboard.tsx`) with real-time stream status, manual backfill trigger toggles, schedule editing modals, and live job execution logs via tRPC.

### Key Deliverables
- **Postgres DDL / Alembic Migration**: `agri.job_schedules` table with schedule metadata, enable toggles, cron expressions/cadences, and foreign key relations to `agri.job_runs`.
- **Python Job Engine (`agri_data_service/jobs/scheduler.py`)**: Asyncio runner executing scheduled and manually triggered work items with leased locks and slice budgets.
- **tRPC Router (`src/lib/server/trpc/routers/jobs.ts`)**: API for fetching job statuses, triggering immediate manual backfills, toggling stream ingestion on/off, and updating schedule cadences.
- **Admin Control Panel UI (`src/app/admin/jobs/page.tsx` & `JobRunnerDashboard.tsx`)**: Platform Admin UI featuring stream toggle controls, execution timeline visualizer, manual trigger buttons, and log inspector.
- **Verification Tests**: Pytest & Vitest integration tests validating leased job execution, concurrency safety, and tRPC admin control mutations.
