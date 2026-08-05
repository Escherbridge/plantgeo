# PlantGeo Railway Operations Guide

This guide describes the existing PlantGeo production boundary in Railway. It is
an operations runbook, not a clean-room tutorial. Do not run `railway init`, add
generic database templates, or infer ownership from the shared project name.

## Production boundary

Railway project ID: `6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990`

The Railway dashboard calls the containing project `Aevani`, but service names
define the security boundary:

| Service | PlantGeo responsibility | Current gate |
| --- | --- | --- |
| `plantgeo-main` | Next.js application | Running; Railway's GitHub integration deploys it from `main`, and no other service is deployed by a web release. |
| `plantgeo-ingest-cron` | Hourly `agri-cli ingest-all` run against Postgres/Redis directly | Running; config-as-code in `infra/cron-ingest/`. Root Directory dashboard change pending — see below. **Superseded by the eight per-layer services below** ("Per-layer ingestion cron services"); pause or delete it once those are confirmed running, or the warehouse gets both an hourly `ingest-all` sweep and each source's own tighter cadence on top of it. |
| `plantgeo-ingest-streamflow` | `agri-cli ingest-streamflow` every 30 minutes | Not yet created (owner action). Config-as-code ready in `infra/cron-streamflow/`. |
| `plantgeo-ingest-weather` | `agri-cli ingest-weather` hourly at `:10` | Not yet created (owner action). Config-as-code ready in `infra/cron-weather/`. |
| `plantgeo-ingest-fire-perimeters` | `agri-cli ingest-fire-perimeters` hourly at `:20` | Not yet created (owner action). Config-as-code ready in `infra/cron-fire-perimeters/`. |
| `plantgeo-ingest-firms` | `agri-cli ingest-firms` every 3 hours at `:30` | Not yet created (owner action). Config-as-code ready in `infra/cron-firms/`. |
| `plantgeo-ingest-drought` | `agri-cli ingest-drought` Thursdays at 14:00 UTC | Not yet created (owner action). Config-as-code ready in `infra/cron-drought/`. |
| `plantgeo-ingest-ndvi` | `agri-cli ingest-ndvi` daily at 05:00 UTC | Not yet created (owner action). Config-as-code ready in `infra/cron-ndvi/`. |
| `plantgeo-ingest-sensors` | `agri-cli ingest-sensors` hourly at `:40` | Not yet created (owner action). Config-as-code ready in `infra/cron-sensors/`. |
| `plantgeo-ingest-evacuation-zones` | `agri-cli ingest-evacuation-zones` every 15 minutes | Not yet created (owner action). Config-as-code ready in `infra/cron-evacuation-zones/`. |
| `plantgeo-dataservice` | Bounded Python API and publication receiver | Running; Alembic owns only the `agri` schema. |
| `plantgeo-Redis` | Cache, pub/sub, and non-durable wake-up transport | Running; never use it as the durable job ledger. |
| `Plantgeo` | Legacy PlantGeo PostgreSQL 18.3 database | Running, but the last audit found no required geospatial/time-series extensions. |
| `plantgeo-spatiotemporal-db` | Replacement database candidate using the TimescaleDB HA PostgreSQL 18 image | Running; extensions, roles, and migrations are not yet verified. It is not the production target yet. |
| `plantgeo-martin` | Private vector-tile service | Provisioned but stopped/crashed. Its initial target lacked PostGIS; the sealed database reference now points to the replacement candidate for a reviewed redeploy. |
| `Aevani-Postgress` | Parent affiliate/UGC/monetization data | Out of scope. Never query, migrate, reset, grant, or reference it from PlantGeo. |

Automation and operator scripts must use the exact PlantGeo allowlist above and
must reject `Aevani-Postgress`. Use Railway reference variables rather than
copying resolved public proxy credentials between services.

## Phase-one compute boundary

Training, 30-day Monte Carlo forecasts, inference, and long preaggregations run
on the operator-controlled machine. They do not run in a Railway web replica,
cron service, Celery worker, or forecast worker.

The local runner:

1. creates a deterministic run identity from the job version, schedule,
   immutable release-set checksum, model/recipe version, partitions, shards,
   and expected output coverage;
2. records checksummed shard checkpoints so interrupted work can resume on the
   next invocation;
3. binds each output report to exact bytes, then validates the complete frozen
   output and coverage set with a run-level report;
4. uploads bounded artifacts through the authenticated data-service API; and
5. advances an immutable database publication pointer only after the complete
   artifact set is revalidated in one transaction.

Publication is at-least-once and idempotent. Local manifests are authoritative
before publication; PostgreSQL lineage and publication pointers are
authoritative afterward. A computer that is powered off cannot run forecasts or
raise proactive alerts, so unattended operation eventually needs a dedicated
operator host or an explicitly budgeted external monitor.

Operational acquisition endpoints may perform bounded, authenticated provider
fetches only when they validate and persist the result before display. The
target state moves long-running acquisition and backfills into the local runner;
browser code never calls environmental providers directly.

See [Predictive Environmental Intelligence](./predictive-environmental-intelligence-spec.md)
and [Data Ingestion and Serving Contract](./data-ingestion-and-serving-contract.md).

## Local forecast workflow

Use the locked Python environment from `services/agri-data-service`:

```powershell
uv sync --locked --all-extras

@'
{
  "partitions": ["colorado-west"],
  "expected_shards": ["colorado-west"],
  "expected_outputs": [
    {
      "output_key": "danger-forecast-colorado-west",
      "kind": "danger_forecast",
      "covered_shards": ["colorado-west"],
      "covered_partitions": ["colorado-west"]
    }
  ]
}
'@ | Set-Content -Encoding utf8 .\run-plan.json

$run = uv run agri-cli local init `
  --job-name danger-forecast `
  --job-version 1 `
  --scheduled-for 2026-07-20T00:00:00-06:00 `
  --release-set-id <release-set-uuid> `
  --release-set-manifest-checksum <64-character-lowercase-sha256> `
  --model-version <model-version> `
  --run-plan .\run-plan.json | ConvertFrom-Json

$runId = $run.run_id
$runDirectory = $run.run_directory
New-Item -ItemType Directory -Force `
  "$runDirectory\artifacts", "$runDirectory\validation" | Out-Null

uv run agri-cli local status $runId
```

The run plan accepts exactly `partitions`, `expected_shards`, and
`expected_outputs`; output entries accept exactly `output_key`, `kind`,
`covered_shards`, and `covered_partitions`. Arrays must be non-empty, sorted,
and unique, and the outputs together must cover every declared shard and
partition. The command returns the deterministic `run_id` and its run directory.
Algorithms should checkpoint after bounded shards and reuse the verified cursor
after interruption:

```powershell
uv run agri-cli local checkpoint $runId `
  --shard-key colorado-west `
  --cursor-file .\cursor.json `
  --progress 0.25

uv run agri-cli local resume $runId --shard-key colorado-west
```

Every artifact and validation report must be a file beneath the returned run
directory. An output validation report is strict JSON with no extra fields and
this version-2 shape:

```json
{
  "schema_version": 2,
  "status": "passed",
  "output_key": "danger-forecast-colorado-west",
  "artifact_sha256": "<64-character-lowercase-sha256>",
  "artifact_size_bytes": 12345,
  "artifact_row_count": 500,
  "run_plan_checksum": "<run-plan-sha256-from-manifest>",
  "release_set_manifest_checksum": "<release-set-manifest-sha256>",
  "validator": "danger-forecast-validator-v1",
  "validated_at": "2026-07-20T08:00:00Z",
  "checks": [
    {
      "name": "forecast-schema",
      "status": "passed",
      "summary": "Required columns, types, bounds, and uniqueness passed."
    }
  ],
  "metrics": {"row_count": 500}
}
```

The validator must calculate these bindings from the final stable bytes; the
example placeholders are not accepted. Registering an output verifies the
artifact checksum, byte size, optional row count, frozen run-plan checksum, and
release-set checksum, but it does not validate the whole run:

```powershell
uv run agri-cli local register-output $runId `
  --output-key danger-forecast-colorado-west `
  --kind danger_forecast `
  --artifact "$runDirectory\artifacts\danger-forecast-colorado-west.parquet" `
  --validation-report "$runDirectory\validation\danger-forecast-colorado-west.json" `
  --media-type application/vnd.apache.parquet `
  --row-count 500
```

After every declared shard has a 100% checkpoint and every expected output is
registered, the algorithm writes a strict version-2 run validation report under
`$runDirectory\validation`. It binds `run_id`, `logical_run_key`,
`run_plan_checksum`, `release_set_id`, `release_set_manifest_checksum`, and the
canonical `output_manifest_checksum`, plus the same `status`, `validator`,
`validated_at`, non-empty `checks`, and optional scalar `metrics` fields shown
above:

```json
{
  "schema_version": 2,
  "status": "passed",
  "run_id": "<run-uuid>",
  "logical_run_key": "local:v2:<64-character-lowercase-sha256>",
  "run_plan_checksum": "<run-plan-sha256-from-manifest>",
  "release_set_id": "<release-set-uuid>",
  "release_set_manifest_checksum": "<release-set-manifest-sha256>",
  "output_manifest_checksum": "<canonical-output-manifest-sha256>",
  "validator": "danger-forecast-run-validator-v1",
  "validated_at": "2026-07-20T08:05:00Z",
  "checks": [
    {
      "name": "complete-run-coverage",
      "status": "passed",
      "summary": "Every planned shard, partition, and output is complete."
    }
  ],
  "metrics": {"output_count": 1}
}
```

Finalization re-hashes all evidence and refuses incomplete coverage:

```powershell
uv run agri-cli local finalize $runId `
  --run-validation-report "$runDirectory\validation\run.json"
```

Only then publish the frozen set:

```powershell

$env:LOCAL_PUBLISH_API_URL = "https://<data-service-domain>/api/v1/local-execution"
$env:LOCAL_PUBLISH_TOKEN = "<strong-dedicated-token>"

uv run agri-cli local publish $runId `
  --product danger_forecast_artifacts `
  --scope-key colorado-west
```

Do not put `LOCAL_PUBLISH_TOKEN` in a manifest, command history, source file, or
browser variable. The server derives `published_by` from
`LOCAL_PUBLISH_ACTOR`; the client cannot supply it. Keep `.agri-local-runs/` on
reliable local storage and include it in the operator backup plan; it is
intentionally ignored by Git and Docker.

## Database replacement gate

`plantgeo-spatiotemporal-db` replaces `Plantgeo`; it is not intended to remain a
second steady-state analytics database. Both are billable only during a bounded
copy, verification, and rollback-observation window.

Do not cut over until every item below has evidence:

1. Confirm the target service is exactly `plantgeo-spatiotemporal-db` and the
   source is exactly `Plantgeo`.
2. Take a restorable backup of `Plantgeo` and perform a restore drill.
3. Use read-only catalog queries to verify PostgreSQL, PostGIS, TimescaleDB,
   pgvector, and pgcrypto on the target. An image label is not proof that an
   extension is installed in the database.
4. Create least-privilege application, data-service publication, and Martin
   roles. Do not use the database owner at runtime.
5. Run Drizzle and Alembic against a disposable database with the same image and
   extension set. Drizzle owns `public`, `geo`, and `tracking`; Alembic owns
   `agri`.
6. Copy data and compare counts, stable checksums, spatial validity, indexes,
   revisions, and representative read/write queries.
7. Prove `/api/ready`, the data publication contract, Martin `/health` and
   `/catalog`, and an allowlisted MVT request.
8. Freeze writes briefly, apply the final delta, and switch all PlantGeo
   references together. Record the revision and timestamp.
9. Observe errors, latency, publication age, tiles, and rollback signals for the
   agreed window. Roll all references back together if any gate fails.
10. After explicit approval, retain the backup but stop/remove `Plantgeo` so
    duplicate compute and storage charges do not persist.

Do not run database migrations in a long-lived service start command. Drizzle
migrations are applied by the Railway `preDeployCommand` described in
[Deployment workflow](#deployment-workflow), which runs once per release in the
runtime image before the deployment receives traffic. A cutover still needs
backup evidence, a target-service guard, and operator authorization; the
pre-deploy step applies the committed migrations, it does not certify data.

## Service configuration

### `plantgeo-main`

- Repository root: `/`
- Dockerfile: `/Dockerfile`
- Config-as-code: `/railway.json`
- Liveness: `GET /api/health`
- Rollout readiness: `GET /api/ready` (auth configuration plus bounded PostgreSQL and Redis probes)
- Runtime port: Railway-provided `PORT`, default `3000`

Minimum private references:

```dotenv
DATABASE_URL=${{Plantgeo.DATABASE_URL}}
REDIS_URL=${{plantgeo-Redis.REDIS_URL}}
MARTIN_URL=http://${{plantgeo-martin.RAILWAY_PRIVATE_DOMAIN}}:3000
```

`DATABASE_URL` remains on `Plantgeo` until the replacement gate passes. Public
map values are embedded during the Next.js build and therefore must be final
before enabling a production deployment:

```dotenv
NEXT_PUBLIC_PMTILES_URL=https://<first-party-r2-or-cdn>/basemap.pmtiles
NEXT_PUBLIC_TERRAIN_URL=https://<reviewed-terrain-origin>/{z}/{x}/{y}.png
NEXT_PUBLIC_DYNAMIC_TILES_URL=https://<martin-public-or-custom-domain>
PLANTGEO_PMTILES_ALLOWED_HOST=<first-party-r2-or-cdn-host>
PLANTGEO_TERRAIN_ALLOWED_HOST=<reviewed-terrain-host>
PLANTGEO_DYNAMIC_TILES_ALLOWED_HOST=<martin-public-or-custom-domain-host>
```

Never place a `*.railway.internal` hostname or a credential in `NEXT_PUBLIC_*`.
The three `PLANTGEO_*_ALLOWED_HOST` build variables must contain the exact
lowercase hostname for their corresponding public URL; the production image
rejects a URL with a different host, IP literal, credentials, query, or
fragment. Keep these variables server-side rather than `NEXT_PUBLIC_*`.
Keep the dynamic-tile variable unset until Martin is verified. Environmental
raster tiles remain disabled until a database-backed publication catalog—not an
environment variable—provides the immutable URL, release, and checksum. The UI
must render a clear unavailable state rather than inventing data.

Server-only credentials include database/Redis URLs, ingestion secrets, provider
tokens, OAuth secrets, and `MAPILLARY_ACCESS_TOKEN`. Provider credentials must
never be exposed as `NEXT_PUBLIC_*`.

### Data-service receiver and published-reader instances

- Repository root: `/services/agri-data-service`
- Dockerfile: `/services/agri-data-service/Dockerfile`
- Config-as-code: `/services/agri-data-service/railway.json`
- Liveness: `GET /health`
- Rollout readiness: `GET /ready` (profile-specific identity, exact Alembic
  revision `20260725_0013`, all four required extensions, route-touched
  objects, and the exact least-privilege runtime and forecast-role grants)
- Runtime port: Railway-provided `PORT`

Deploy the same image as two separately configured service instances. The
private receiver/writer instance mounts only the local-publication and
historical-promotion receivers:

```dotenv
SERVICE_PROFILE=receiver_writer
RECEIVER_WRITER_DATABASE_URL=<async least-privilege receiver/writer DSN>
EXECUTION_BACKEND=local
CELERY_DISPATCH_ENABLED=false
CLOUD_TRAINING_ENABLED=false
LOCAL_PUBLICATION_RECEIVER_ENABLED=true
LOCAL_PUBLISH_TOKEN=<strong dedicated secret>
LOCAL_PUBLISH_ACTOR=plantgeo-local-forecast-publisher
```

The published-reader instance mounts only the typed published forecast route:

```dotenv
SERVICE_PROFILE=published_reader
PUBLISHED_READER_DATABASE_URL=<async least-privilege published-reader DSN>
EXECUTION_BACKEND=local
CELERY_DISPATCH_ENABLED=false
CLOUD_TRAINING_ENABLED=false
```

The DSNs must authenticate different login roles. Do not configure
`DATABASE_URL` as a production data-service fallback, and do not inject receiver
tokens or actors into the published reader. `SERVICE_PROFILE=combined_local` is
development compatibility only and always fails rollout readiness.

The publication token must differ from application admin and ingestion secrets.
The actor is a server-controlled audit identity bound to that credential; the
workstation cannot choose it. `DATABASE_URL_SYNC` belongs only in an approved,
short-lived Alembic migration context and must use a synchronous PostgreSQL
driver. Do not inject the migration DSN into either long-lived data-service
container.

Each runtime login must have only `CONNECT`; `USAGE` (not `CREATE`) on `public`
and `agri`; `SELECT` on `public.alembic_version`; and the exact route-specific
grants audited by `/ready`. The receiver login must not inherit the published
reader capability or read the serving views. It requires `USAGE, SELECT` only on
`agri.signal_observation_id_seq` and
`agri.drought_polygon_snapshot_id_seq`; every other `agri` sequence is denied.
The reader login must inherit only `plantgeo_forecast_reader` and must have no
writer, publisher, refresher, table write, or sequence privilege. Both profiles
are audited over every `agri` relation, sequence, and function, including
column-only grants, memberships, and ownership paths. Neither login may own
database objects, hold grant options, or have `SUPERUSER`, `CREATEDB`,
`CREATEROLE`, `REPLICATION`, or `BYPASSRLS`. Revoke prior broad grants before
applying the reviewed matrices.

Set request and aggregate size limits explicitly; the defaults in
`.env.example` are conservative starting points, not load-test evidence.

### `plantgeo-martin`

- Dockerfile: `/Dockerfile.martin` (Martin `1.10.1`)
- Config-as-code: `/infra/railway/martin.railway.json`
- Runtime config: `/infra/martin/martin.yaml`
- Health: `GET /health`
- Port: `3000`

Keep Martin private and stopped until the database gate passes. Then configure:

```dotenv
DATABASE_URL=<sealed DSN for the Martin-only target role>
PORT=3000
TILE_CORS_ORIGIN=https://plantgeo-main-production.up.railway.app
MARTIN_CACHE_SIZE_MB=128
MARTIN_POOL_SIZE=8
```

The configuration disables automatic table and function publication and
allowlists only migration-owned MVT functions. Before creating a public domain,
grant the Martin role only database connect, `geo` schema usage, and execute on
those functions; place the public endpoint behind CDN/WAF rate limits. CORS is
not authentication. Static PMTiles belong in R2/CDN, not the Martin container.

### `plantgeo-ingest-cron`

- Repository root: **must move to `/`** (currently `/infra/cron-ingest`) — see "Required
  dashboard change" below. The image cannot build until this lands.
- Dockerfile: `/infra/cron-ingest/Dockerfile`
- Config-as-code: `/infra/cron-ingest/railway.json` (`cronSchedule: 0 * * * *`,
  `restartPolicyType: NEVER`)

The container installs the `agri-data-service` package (uv, locked sync, `--no-dev` runtime —
the same multi-stage pattern as `services/agri-data-service/Dockerfile`, minus its quality-gate
stage, which this image does not need to re-run) and runs `agri-cli ingest-all` directly against
Postgres and Redis on the private network. It runs every ingestion source to completion and
**exits non-zero if any source failed** — an unhandled failure is a red Railway deployment, not a
line in a server log. There is no HTTP hop through `plantgeo-main` any more: no
`GET /api/cron/ingest` call, no `x-cron-secret` header, no `202`/`409` status mapping, and
`CRON_SECRET` is retired (see `docs/env-vars.md`). `restartPolicyType: NEVER` together with the
hourly `cronSchedule` is now the concurrency guard that the deleted in-memory
`ingestionInFlight` boolean used to provide: a failed run does not restart, and the next tick
simply starts a fresh container. This service is the only scheduler for ingestion.

**Required dashboard change (owner action, blocks the build):** a Python image needs
`services/agri-data-service/{pyproject.toml,uv.lock,src/}` in its build context, which the
service's current Railway Root Directory (`/infra/cron-ingest`) cannot see. In the Railway
dashboard for `plantgeo-ingest-cron`, set:

- Root Directory → `/`
- Dockerfile path → `infra/cron-ingest/Dockerfile`
- Config-as-code path → `infra/cron-ingest/railway.json`

This repoints only `plantgeo-ingest-cron`'s build context; it does not touch the repo-root
`railway.json`, which belongs to `plantgeo-main`. Until the Root Directory change lands, builds of
this service will fail — its `Dockerfile`'s `COPY services/agri-data-service/...` lines cannot
resolve from the old root.

**Required variables.** The DSN variable this container reads is
`LOCAL_SOURCE_LOADER_DATABASE_URL`, **not** `DATABASE_URL`. Every `ingest-*` verb opens
`db/engine.ingest_session()`, which calls `settings.require_local_source_loader_database_url()`
(`config.py`), and that reader has no fallback: with only `DATABASE_URL` set the run dies before
any source is fetched with

```
ValueError: source-ingest requires LOCAL_SOURCE_LOADER_DATABASE_URL; DATABASE_URL is never a loader fallback
```

The raise happens outside `run_isolated_job`, so it is an unhandled traceback and a red hourly
deployment, not a per-source `failed` summary — zero rows ingested on every tick.

| Variable | Value on this service |
| --- | --- |
| `LOCAL_SOURCE_LOADER_DATABASE_URL` | **Required.** The Railway **public proxy** DSN, scheme `postgresql+asyncpg://` (mandatory — see `docs/env-vars.md`): `postgresql+asyncpg://postgres:<password>@switchback.proxy.rlwy.net:37967/plantgeo`. |
| `DATABASE_URL` | **Must NOT be set.** `require_local_source_loader_database_url` rejects a loader DSN equal to `DATABASE_URL`, and both fields are normalised to `postgresql+asyncpg://` before comparison, so identical raw strings compare equal and the run fails. |
| `REDIS_URL` | Required, for the realtime publisher. |
| `INGEST_BBOX` | Required. Without it every source returns a `skipped` summary and writes nothing. |
| `NASA_FIRMS_KEY` | Required for the FIRMS source. |

`_INGEST_SOURCE_LOADER_ALLOWED_TARGETS` (`config.py`) accepts only `127.0.0.1:5442` with role
`plantgeo_loader` and `switchback.proxy.rlwy.net:37967` with role `postgres`. The private-network
DSN (`postgres.railway.internal:5432`) therefore **fails validation** — this service must use the
public proxy host. See `services/agri-data-service/src/agri_data_service/ingest/AGENTS.md`.

Optional: `FIRMS_DAY_RANGE`, `INGEST_MAX_SOURCE_RECORDS`, `WEATHER_SAMPLE_SPACING_DEGREES`, and
the layer-id and sensor-selection overrides in `docs/env-vars.md`. See that file for policy and
defaults on each.

**Verbs this image can run.** `agri-cli ingest-all` is the scheduled one: eight sources followed by
the geometry repair pass, each isolated, one JSON summary per job. The other verbs are operator
tools on the same image — `ingest-<source>` for a single source, `ingest-geometry-repair` to link
orphaned `geo.features.geometry_id` rows on demand, `ingest-backfill --source … --since … --until …`
to walk a date-ranged history for the sources that publish one (`nws-sensors`, `sentinel2-ndvi`), and
`ingest-drought-history --years N` to walk the USDM archive week by week. Do not run
`ingest-geometry-repair` concurrently with `ingest-all`: both are safe individually and take their
locks in the same order, but the second one to arrive simply waits.

### Per-layer ingestion cron services

`plantgeo-ingest-cron`'s single hourly `ingest-all` run polls every source at the same cadence,
which is wrong for all of them: USDM publishes one release a week, so an hourly poll is 168 requests
for one usable answer, while streamflow and fire perimeters change inside the hour and an hourly poll
under-samples them. The eight services below replace it with one service per source, each on the
cadence that matches how often its upstream actually publishes.

| Service (proposed name) | Directory | CLI verb | `cronSchedule` | Why this cadence |
| --- | --- | --- | --- | --- |
| `plantgeo-ingest-streamflow` | `infra/cron-streamflow/` | `ingest-streamflow` | `*/30 * * * *` | USGS NWIS gauges report on the order of minutes; every 30 minutes tracks that without over-polling. |
| `plantgeo-ingest-weather` | `infra/cron-weather/` | `ingest-weather` | `10 * * * *` | Open-Meteo current conditions refresh hourly; offset to `:10` so it does not stack with the other services' top-of-hour ticks. |
| `plantgeo-ingest-fire-perimeters` | `infra/cron-fire-perimeters/` | `ingest-fire-perimeters` | `20 * * * *` | WFIGS interagency perimeters redraw on the order of tens of minutes during active incidents; hourly at `:20`. |
| `plantgeo-ingest-firms` | `infra/cron-firms/` | `ingest-firms` | `30 */3 * * *` | The job fans out across all three VIIRS NRT products (`firms.py`'s full-constellation query), and NRT products land a handful of times a day per satellite, not continuously; every 3 hours tracks new overpasses without repeatedly re-requesting a product that has not refreshed since the last poll. |
| `plantgeo-ingest-drought` | `infra/cron-drought/` | `ingest-drought` | `0 14 * * 4` | USDM publishes one release a week, Thursdays; polling hourly (168x/week) for a weekly release was the motivating waste for this whole change. `_require_tuesday` in `usdm.py` derives the *requested* date from the Tuesday the release covers, not from when this cron fires, so a Thursday poll is simply "check whether Thursday's usual release is up yet." |
| `plantgeo-ingest-ndvi` | `infra/cron-ndvi/` | `ingest-ndvi` | `0 5 * * *` | Sentinel-2 L2A revisits the Pacific Northwest every 2-5 days and a scene needs cloud-free daylight to be usable; a daily check at 05:00 UTC (before the bulk of daytime PNW acquisitions are typically processed) is enough to catch each new clear scene without adding request volume a multi-day revisit cannot use. |
| `plantgeo-ingest-sensors` | `infra/cron-sensors/` | `ingest-sensors` | `40 * * * *` | **Proposed.** `sensors.py`'s own identity-builder docstring states NOAA NWS ground-station readings arrive hourly ("its one geometry chain is confirmed and versioned as the hourly readings arrive"); polling faster buys nothing since a station's `timestamp` only changes once an hour, and polling by the reading's own natural key makes an extra poll a no-op, not a duplicate. Offset to `:40` so 591+ stations are not polled in the same minute as the other hourly services. |
| `plantgeo-ingest-evacuation-zones` | `infra/cron-evacuation-zones/` | `ingest-evacuation-zones` | `*/15 * * * *` | **Proposed.** `evacuation_zones.py` documents that Oregon's OEM sync re-stamps an unchanged area's edit clock "every few minutes" (which is why `observed_at` deliberately keys off `created_date`, never that edit clock) — so the upstream feed itself is genuinely live during an active incident. This is the one layer in the set that is life-safety information (active wildfire evacuation orders), so it is polled close to the fire-perimeters cadence rather than hourly; every 15 minutes balances that against not hammering Oregon's ArcGIS endpoint. |

**Mechanics shared by all eight services:**

- **Shared Dockerfile, varied verb.** All eight `railway.json` files point `build.dockerfilePath` at
  the same `infra/cron-ingest/Dockerfile` used by `plantgeo-ingest-cron` — there is no per-service
  copy. `infra/cron-ingest/Dockerfile` ends in `ENTRYPOINT ["agri-cli", "ingest-all"]`; each
  per-layer `railway.json` sets `deploy.startCommand` to its own verb, e.g.
  `"startCommand": "agri-cli ingest-streamflow"`.
- **This was verified, not assumed.** Railway's own documentation
  (`docs.railway.com/deployments/start-command`, "Dockerfiles & images" section) states: *"Dockerfile
  / Image: the start command overrides the image's `ENTRYPOINT` in exec form."* That is an override,
  not an append — Docker's own `docker run image <args>` semantics would instead pass `<args>` as
  arguments *to* the existing `ENTRYPOINT` (`agri-cli ingest-all ingest-streamflow`, which the CLI
  would reject as an unexpected argument), so this only works because Railway explicitly documents
  that it replaces the entrypoint rather than appending to it. No local Docker run was available to
  double-check against Railway's own runtime, so this is a documentation-sourced verification, not an
  execution-sourced one; if a deployed service ever runs `ingest-all` instead of the verb its
  `railway.json` names, this is the first thing to re-examine.
- **The migration-safety property is untouched.** `infra/cron-ingest/Dockerfile` never copies
  `alembic/`, `db/`, or `alembic.ini` (see its own header comment), so none of these eight containers
  can run a migration regardless of `startCommand` — the property holds by what the image does not
  contain, not by trusting any command string.
- **Env var contract is identical across all eight**, and identical to `plantgeo-ingest-cron` above:
  `LOCAL_SOURCE_LOADER_DATABASE_URL` (required, public proxy DSN — see below), `REDIS_URL` (required),
  `INGEST_BBOX` (required), and `NASA_FIRMS_KEY` (required, but only on `plantgeo-ingest-firms`).
  `CRON_SECRET` is retired; do not set it on any of them. Each source's own optional
  layer-id/tuning overrides (`FIRE_PERIMETERS_LAYER_ID`, `VEGETATION_LAYER_ID`,
  `EVACUATION_ZONES_LAYER_ID`, `SENSOR_STATION_STATES`, `SENSOR_STATION_NETWORKS`,
  `SENSOR_MAX_STATIONS`, `DROUGHT_RETAINED_RELEASES`, `FIRMS_DAY_RANGE`,
  `INGEST_MAX_SOURCE_RECORDS`, `WEATHER_SAMPLE_SPACING_DEGREES`) are documented per-variable in
  `docs/env-vars.md` and apply only to the service that runs the matching verb.

**Dashboard runbook (owner action — none of this is possible from the CLI; see "Required dashboard
change" above for why).** For each of the eight services in the table:

1. Create the Railway service (or repoint a placeholder) named per the table above.
2. In that service's Settings, set exactly two fields:
   - **Root Directory** → `/`
   - **Config-as-code path** → `infra/cron-<name>/railway.json` (the directory column in the table
     above — e.g. `infra/cron-streamflow/railway.json` for `plantgeo-ingest-streamflow`)
3. Set the env vars from "Env var contract" above (`LOCAL_SOURCE_LOADER_DATABASE_URL`, `REDIS_URL`,
   `INGEST_BBOX`, and `NASA_FIRMS_KEY` on the FIRMS service only), plus any optional overrides that
   service's row needs.
4. Deploy. The service's own `railway.json` supplies `build.dockerfilePath`,
   `deploy.cronSchedule`, `deploy.restartPolicyType: "NEVER"`, and `deploy.startCommand` — nothing
   else needs to be set in the dashboard.
5. Once all eight are confirmed running (check each service's logs for the one `to_summary()` JSON
   line its verb emits), pause or delete `plantgeo-ingest-cron`. Leaving it running alongside the
   eight per-layer services does not corrupt anything — every write path is idempotent by
   `properties->>'id'` — but it does mean the warehouse pays for both an hourly `ingest-all` sweep
   and each source's own tighter schedule on top of it, and USDM goes right back to being polled far
   more often than it publishes.

None of the eight services could be created or configured from this pass: `railway service` has no
`create` or `update` subcommand, `RAILWAY_DOCKERFILE_PATH` cannot override the resolved config-as-code
path, and `RAILWAY_CONFIG_PATH` is not honoured as a variable (see "Required dashboard change" above,
which found the same thing for `plantgeo-ingest-cron`). Steps 1-3 above are therefore blocked on the
owner.

### Deferred services

Valhalla and Photon are not part of the currently provisioned PlantGeo
allowlist. Add them only after a capacity, image-digest, data-volume, licensing,
privacy, and operating-cost review. Never deploy a floating `:latest` image.

## Deployment workflow

Railway is the only deployment path. There is no GitHub Actions pipeline; the
repository has no `.github/workflows/`. A push to `main` on
`Escherbridge/plantgeo` triggers Railway's own GitHub integration for
`plantgeo-main`, and one release proceeds in this order:

1. **Build.** Railway builds `/Dockerfile`. The `build` stage runs
   `npm run check:data-boundary`, `npm run type-check`, `npm run lint`, and
   `npm test` before `next build`, so a failing gate fails the image and no
   release is produced. The production public-URL gate runs in the same stage.
2. **Pre-deploy migration.** `railway.json` sets
   `deploy.preDeployCommand` to `node scripts/migrate.mjs`. Railway runs it in
   the new runtime image, once, before any traffic is routed. A non-zero exit
   aborts the deployment and the previous release keeps serving.
3. **Healthcheck.** Railway polls `GET /api/ready`, which requires the exact
   Drizzle migration pinned in `src/lib/server/db/migration-contract.ts`, so
   schema drift fails the rollout rather than reaching users.
4. **Traffic.** Only a deployment that built, migrated, and reported ready
   receives traffic.

Railway deploys only `plantgeo-main` from the repository root. The data service,
Martin, and the ingest cron service each have their own root, Dockerfile, and
`railway.json`; none of them is redeployed by a web release.

### The pre-deploy migration step

`scripts/migrate.mjs` applies migrations with `drizzle-orm`'s postgres-js
migrator against the `drizzle/` folder copied into the runtime image.
`drizzle-kit` stays a devDependency and never ships.

- **Idempotency.** The migrator reads `drizzle/meta/_journal.json`, hashes each
  `.sql` file with sha256, and applies only entries whose journal timestamp is
  greater than the newest `created_at` already in
  `drizzle.__drizzle_migrations`. That is the same hash and timestamp pair that
  `migration-contract.ts` and `/api/ready` assert, so an already-applied
  migration is never re-run or duplicated.
- **Failure behavior.** A missing DSN, an unreachable database, or a failing
  statement exits non-zero, which aborts the deployment.
- **DSN and role.** The step reuses the service's `DATABASE_URL` unless
  `MIGRATION_DATABASE_URL` is set, in which case that DSN wins. The DSN must
  belong to a role with DDL rights that owns the `geo` schema: the migrator
  issues `CREATE SCHEMA IF NOT EXISTS drizzle` and `CREATE TABLE IF NOT EXISTS
  drizzle.__drizzle_migrations` on every run, and PostgreSQL checks `CREATE` on
  the database even when both already exist. When `plantgeo-main`'s runtime role
  is reduced to least privilege, set `MIGRATION_DATABASE_URL` to a
  migration-capable DSN scoped to the exact production database. Never point it
  at the Martin tile role or at `Aevani-Postgress`.
- **TLS.** The client mirrors `src/lib/server/db/index.ts` and does not force
  TLS, because production reaches PostgreSQL over Railway's private network.
- **Revision.** The migrations, the migrator, and the application code all come
  out of the same image, so schema and code cannot skew.

Adding a Drizzle migration therefore requires updating
`src/lib/server/db/migration-contract.ts` in the same commit; otherwise the new
migration applies and `/api/ready` still fails on the old pinned hash.

For an explicitly approved manual deployment of a local revision:

```powershell
npx --yes @railway/cli@5.27.0 up `
  --project 6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990 `
  --environment production `
  --service plantgeo-main `
  --ci
```

Before any manual command, inspect `railway status --json` and stop if the
project, environment, or service is not the expected exact value.

## Observability and durability

- Railway logs are the live troubleshooting stream; application logs must be
  structured and must redact credentials, provider payloads, and precise private
  locations.
- Retain 30 days of redacted job events in partitioned PostgreSQL tables. A
  local control-plane task must create upcoming partitions and remove expired
  ones. Run `uv run agri-cli job-logs-maintain --retention-days 30
  --future-days 7` after migration and at least daily from the operator machine.
  The command takes a nonblocking transaction fence before any table lock,
  reports `maintenance busy` instead of waiting behind another invocation,
  drains matching rows from the default partition before attachment, and
  reports any rows still in the default. Treat a nonzero remainder, repeated
  busy result, or command failure as an alert; the default partition is loss
  protection, not the normal steady state.
- Alert on web/data-service readiness, database saturation, Redis failures,
  Martin health after activation, oldest successful source publication, oldest
  model publication, repeated local-publication rejection, and forecast
  staleness.
- Keep prediction lineage and model metadata longer than operational logs; a
  30-day debugging window is not a scientific audit-retention policy.
- Redis is never the sole copy of a durable task, cursor, artifact, or
  publication state.

## Cost controls

Phase one provisions no Railway forecast/training worker, so its Railway compute
line is `$0`. Railway charges the higher of the Pro subscription minimum or
resource usage; volumes are billed on actual stored bytes, not configured
capacity. The most recent snapshot showed roughly 1.1 GB in `Plantgeo`, 1.9 GB
in `plantgeo-Redis`, and only a few MB in the replacement database. Measure again
before cutover.

Planning envelopes, including temporary database overlap and R2 storage/egress,
are maintained in the
[production cost envelope](./predictive-environmental-intelligence-spec.md#production-cost-envelope).
Current estimates are approximately `$27–35/month` at a lightly used idle floor,
about `$45/month` for a lean regional deployment, and about `$152/month` for the
base scenario. They are budgets, not vendor quotes; actual resident memory,
vCPU, storage, egress, and retention determine the bill.

## Backup, rollback, and incident rules

- Verify backup availability and a restore before database migration. Never
  assume a plan includes a particular schedule or retention period without
  checking the live Railway configuration.
- Record the source/target service IDs, schema revisions, data checksums, and
  reference-variable changes for every cutover.
- Roll back application, data-service, and Martin database references as one
  unit; a partial rollback can corrupt publication lineage or expose mixed data.
- Keep releases content-addressed and retain the previous valid publication so
  upstream or model failures degrade to an explicit stale state.
- Never print resolved Railway variables, connection strings, API tokens, or
  local publication tokens into build, deploy, or support logs.

## Production-readiness checklist

- [ ] `plantgeo-spatiotemporal-db` extensions and roles verified read-only.
- [ ] Disposable Drizzle and Alembic migration rehearsal passes.
- [ ] Restorable `Plantgeo` backup and rollback drill recorded.
- [ ] Data copy checksums, spatial validity, and representative queries pass.
- [ ] Local runner resumes a checksummed shard after interruption.
- [ ] Invalid/incomplete outputs cannot advance publication pointers.
- [ ] Thirty-day job-event partition maintenance and alerting are operational.
- [ ] Martin least-privilege role, `/health`, `/catalog`, MVT, CORS, and rate
      limits pass before a public domain is created.
- [ ] Production browser build contains no localhost, placeholder, private
      Railway hostname, provider credential, or unapproved provider URL.
- [ ] `/api/health` and `/api/ready` have distinct liveness/readiness behavior.
- [ ] The in-build gates and `preDeployCommand` pass at the exact revision, and
      `migration-contract.ts` pins the newest committed migration.
- [ ] Old `Plantgeo` is stopped/removed after the observation window and explicit
      approval.

Railway references: [pricing](https://docs.railway.com/pricing/plans),
[private networking](https://docs.railway.com/private-networking),
[volumes](https://docs.railway.com/volumes/reference),
[logs](https://docs.railway.com/observability/logs), and
[`railway up`](https://docs.railway.com/cli/up).
