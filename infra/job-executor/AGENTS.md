# `plantgeo-job-executor` image

This is the only image allowed to host scheduled PlantGeo data work. The service is continuous
(`agri-service ops jobs-executor`, `ON_FAILURE`) and contains only the Python service runtime. The
retired SoilGrids cache warmer, its Node runtime, and its package dependency stage must not return.

The build context is the repository root. In Railway, `plantgeo-job-executor` must therefore use
Root Directory `/`, Config-as-code path
`/services/agri-data-service/railway.job-executor.json`, and Dockerfile path
`infra/job-executor/Dockerfile` as one coordinated change. A deployment that resolves a different
root or config is not the release candidate even if it reaches `SUCCESS`.

The image omits Alembic and database migration artifacts deliberately. Scheduler ownership never
widens into schema-migration authority. It preserves the source commands, bounded repair commands,
R2 publication logic; duplicate Railway scheduling configs and retired cache-warming code stay absent.

The runtime installs `libexpat1` for Rasterio/GDAL's `libexpat.so.1` dependency, matching the
data-service image. The final unprivileged build check imports Rasterio as well as loading
DuckDB extensions; executor startup can otherwise defer the native-library failure until a lane runs.

The runtime copies `scripts/complete_partial_ladders.py` explicitly. This is the receipt-pinned,
publication-locked repair for physically present base partitions whose derived rungs or legacy
completion receipts are incomplete. Run it inside the executor over Railway SSH so its advisory
locks use the private database network; `railway run` is local and must not be made to work by
exporting production credentials. No other maintenance script enters the runtime implicitly.

Railway cron schedules are prohibited. Do not add `cronSchedule`, a shell fan-out, an infinite drain
loop or a second periodic service. Source cadence belongs in the executor registry and durable state
belongs in `agri.job_*`; source/domain checkpoints remain in their existing database rows, manifests
and marker-last R2 objects.

Before merging a lane or tier retirement, reconcile three surfaces as one change: the executable
catalogue in `LANE_SPECS`, the files and runtimes copied by this Dockerfile, and the production
`PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` value. The production value is the intersection of its prior
value with the new catalogue; cleanup never activates a previously inactive lane. Search the image
and Railway watch paths for every deleted helper name so a stale `COPY` cannot fail after the code is
gone. The release path is a merge to `main` through Railway's GitHub integration, followed by build
and startup verification. See `docs/layer-lane-standard.md` §13.1.

Rollback removes the affected lane from `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` or pauses its
`agri.job_definition` row, then waits for its fenced lease/process to end. Never restore, reconnect or
recreate a retired Railway cron/one-shot writer service. Never delete PostgreSQL/R2 data, manifests,
cache rows or checkpoints during scheduler rollback.
