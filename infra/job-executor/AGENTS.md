# `plantgeo-job-executor` image

This is the only image allowed to host scheduled PlantGeo data work. The service is continuous
(`agri-service ops jobs-executor`, `ON_FAILURE`) and contains both the Python service runtime and the
existing Node SoilGrids cache warmer. The Node addition preserves the reviewed TypeScript response
validation, scaling, governed no-data and `(lat, lon)` upsert semantics; do not fork that source logic
into a second implementation merely to make the image single-runtime.

The build context is the repository root. In Railway, `plantgeo-job-executor` must therefore use
Root Directory `/`, Config-as-code path
`/services/agri-data-service/railway.job-executor.json`, and Dockerfile path
`infra/job-executor/Dockerfile` as one coordinated change. A deployment that resolves a different
root or config is not the release candidate even if it reaches `SUCCESS`.

The image omits Alembic and database migration artifacts deliberately. Scheduler ownership never
widens into schema-migration authority. It preserves the source commands, bounded repair commands,
R2 publication logic and cache warmer; only duplicate Railway scheduling configs/images are retired.

Railway cron schedules are prohibited. Do not add `cronSchedule`, a shell fan-out, an infinite drain
loop or a second periodic service. Source cadence belongs in the executor registry and durable state
belongs in `agri.job_*`; source/domain checkpoints remain in their existing database rows, manifests
and marker-last R2 objects.

Rollback removes the affected lane from `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` or pauses its
`agri.job_definition` row, then waits for its fenced lease/process to end. Never restore, reconnect or
recreate a retired Railway cron/one-shot writer service. Never delete PostgreSQL/R2 data, manifests,
cache rows or checkpoints during scheduler rollback.
