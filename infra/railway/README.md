# PlantGeo Railway production configuration

PlantGeo runs in Railway project `Aevani` (`6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990`), production
environment `b7cfa813-8a5c-4fcd-80f2-cab736d840a7`.

## Current PlantGeo resources

| Resource | Purpose |
| --- | --- |
| `plantgeo-main` | Next.js application and public API |
| `plantgeo-parquet-api` | Governed environmental Parquet reads and agent tools |
| `plantgeo-job-executor` | Sole scheduler for source-direct Parquet publication and repair |
| `plantgeo-martin` | `geo.intervention_tiles` only |
| `plantgeo-spatiotemporal-db` | Transactional state, job control, interventions, and bounded lookups |
| `plantgeo-Redis` | Cache and pub/sub |
| `plantgeo-parquet` | Governed Parquet object storage |

`aevani-web`, `Aevani-Postgress`, and `aevani-images` share the Railway project but are outside
PlantGeo ownership.

## Scheduling boundary

Railway cron scheduling is unused. Every service has `cronSchedule: null`. The long-lived
`plantgeo-job-executor` owns schedules and durable retry state. Do not create source-specific cron
services or one-shot backfill services; add current work to the executor or run a bounded source
repair that writes directly to governed Parquet.

## Data boundary

Environmental writers publish directly from the upstream source to governed Parquet. The Parquet
API, application, and agent tools read those publications. Missing coverage returns a typed
unavailable or governed-absence response and is repaired from the source.

PostgreSQL is not an environmental observation store, serving fallback, or ingestion waypoint.
Martin has `auto_publish: false` and publishes only `geo.intervention_tiles`.

## Deployment descriptors

- `plantgeo-main`: repository root `Dockerfile`; readiness `/api/ready`.
- `plantgeo-parquet-api`: `services/agri-data-service/railway.json`; readiness `/ready`.
- `plantgeo-job-executor`: `services/agri-data-service/railway.job-executor.json`.
- `plantgeo-martin`: `infra/railway/martin.railway.json`; health `/health`.

The main app receives `AGRI_PARQUET_SERVICE_URL` through the Parquet API private Railway domain.
The Parquet API and executor share the governed object-store credentials. Database variables must
reference `plantgeo-spatiotemporal-db` explicitly.
