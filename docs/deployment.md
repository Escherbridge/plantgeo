# PlantGeo Railway Operations Guide

This guide describes the existing PlantGeo production boundary in Railway. It is
an operations runbook, not a clean-room tutorial. Do not run `railway init`, add
generic database templates, or infer ownership from the shared project name.

## Production boundary

Railway project ID: `6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990`

The Railway dashboard calls the containing project `Aevani`, but service names
define the security boundary:

**Scheduler owner directive, verified 2026-09-02:** `plantgeo-job-executor` is the sole production
scheduler and durable invocation owner. Railway cron scheduling is rejected. Release
`e4490c3c2f2e23f75cc9d6e297f4be646e0e00a1` is on `main`; executor deployment
`b1f35a20-6e05-48ff-9801-5235c9753a01` is `SUCCESS` at that exact commit with 37 active executable
lanes. The six legacy writer service objects have `cronSchedule: null`, a no-op start command and
`restartPolicyType: NEVER`; they are fences and credential-reference holders, not schedulers.
Rollback disables an executor lane and never restores a cron service or schedule. See the Conductor
[`scheduler-handoff-20260902.md`](../conductor/tracks/gapless_parquet_publication_20260901/evidence/scheduler-handoff-20260902.md).

**One dated inventory.** Every row below is the 14-service production census read on
**2026-09-02** and recorded in `scheduler-handoff-20260902.md`. Service IDs are quoted because a
name is mutable and an ID is not. Anything that read records as fenced is written as fenced here;
anything it does not establish is marked **unproven by this inventory** rather than guessed.

| Service | Service ID | PlantGeo responsibility | State on 2026-09-02 |
| --- | --- | --- | --- |
| `plantgeo-main` | `fa08a3aa-6d1d-43eb-846b-15dbfd887d61` | Next.js application | Active deployment `f232fb54` `SUCCESS`. Railway's GitHub integration deploys it from `main`, and no other service is deployed by a web release. |
| `plantgeo-job-executor` | `565ecaad-9946-48f1-8a0b-28fa60494a16` | Sole continuous scheduler for independently registered source, maintenance, gap-repair, MTBS, SoilGrids, fire and water lanes | Dedicated `railway.job-executor.json`; continuous `agri-service ops jobs-executor`, `ON_FAILURE`, no Railway schedule. Deployment `b1f35a20` `SUCCESS` at exact `main` release `e4490c3`, 37 active executable lanes plus one terminal snapshot-only responsibility. Environment `scheduled == []`. **Correction — 2026-09-03:** `b1f35a20` was reached by a manual redeploy, not the push. The push-triggered build at the same commit, deployment `003bfc6e`, FAILED — Railway discovered the repository-root `railway.json` and built the Next.js image instead of `infra/job-executor/Dockerfile`. See "Current mechanics" below for the full failure list and remedy. |
| `plantgeo-parquet-api` | `33aed861-af76-4fdd-a95e-784bdcc95e55` | Private published-reader Parquet API on port `8080`; no public domain | Active deployment `91b791ab` `SUCCESS`. Classified serving, not a scheduler target. |
| `plantgeo-martin` | `fe6ef46e-7b4c-41ef-8b64-5100a344c526` | Vector-tile service | Active deployment `dc48f11a` `SUCCESS`, classified **serving**. This supersedes the older "provisioned but stopped/crashed" note. Whether a public domain is attached is **unproven by this inventory**, which records status and classification only. |
| `plantgeo-spatiotemporal-db` | `1e166530-9c8a-4d4a-b685-a70c801fc449` | The production database (PostgreSQL 18 + PostGIS 3.6) | Active deployment `1f33637e` `SUCCESS`, classified data-bearing, never removed by a cleanup. TimescaleDB was dropped 2026-08-25 after holding only an empty hypertable with no continuous aggregate. Extensions measured that day: btree_gist, hypopg, pg_buffercache, pgcrypto, plpgsql, postgis (3.6), vector. |
| `plantgeo-Redis` | `ae23c58e-b1e3-4c01-9d94-cd365550f363` | Cache, pub/sub, and non-durable wake-up transport | Active deployment `dcc757e0` `SUCCESS`. Never the durable job ledger — that is `agri.job_*`. |
| `aevani-web` | `b6c06bf1-f1f4-4733-a33d-0f88d178c2fc` | Different repository/application | Active deployment `de0db5ba` `SUCCESS`. Out of scope. |
| `Aevani-Postgress` | `3e0ea761-f509-474e-97cf-0086acd9ab7a` | Parent affiliate/UGC/monetization data | Active deployment `b3a6ab66` `SUCCESS`. Out of scope. Never query, migrate, reset, grant, or reference it from PlantGeo. |
| `plantgeo-ingest-cron` | `3ae3cc37-c398-43fe-b74c-83e4da130423` | **Fenced** legacy composite writer | `cronSchedule: null`, start command `sh -c 'echo retired-to-plantgeo-job-executor; exit 0'`, `restartPolicyType: NEVER`, zero retries. Inert credential/reference holder: keep until its hidden CDS secrets and service references move to stable owners. |
| `plantgeo-cron-mtbs` | `a683cc83-2b49-4276-a136-941e1b2cbe24` | **Fenced** legacy MTBS writer | Same fence. Remove only after an observed `mtbs-forward` executor success. |
| `plantgeo-cron-soilgrids` | `0960aa81-4499-4cb1-9daa-3350eed4d654` | **Fenced** legacy SoilGrids warmer | Same fence. Remove only after an observed `soilgrids-cache-warm` success. |
| `plantgeo-fire-detections-forward` | `f4ad61fe-e71a-4776-b9d5-0b153c9ee5b7` | **Fenced** legacy direct fire writer | Same fence. Direct executor publication already succeeded; retain until a removal receipt is recorded. |
| `plantgeo-water-gauges-forward` | `40cb252b-e21c-4140-8d94-5db77eb2398d` | **Fenced** legacy direct water writer | Same fence. Remove only after the `2026-09-02` direct-ownership boundary and terminal-day proof. |
| `plantgeo-soil-moisture-parquet-load` | `4a1413f1-5f96-44ea-853c-6a379c7673c4` | **Fenced** completed one-shot load | No schedule; terminal receipt deployment `29c54089` completed 1,556 days / 6,861,960 rows through `2026-08-02`. Remove after an immutable-artifact re-read receipt. |

Two service names that older revisions of this guide and `infra/railway/README.md` treated as live
do **not** appear anywhere in the 2026-09-02 census: **`plantgeo-dataservice`** and **`Plantgeo`**.
Treat both as absent. In particular, a variable written `${{Plantgeo.DATABASE_URL}}` cannot resolve
against this inventory; `plantgeo-spatiotemporal-db` is the only PlantGeo database service it
records. The handoff evidence deliberately records variable *names* only, so the resolved value of
`plantgeo-main`'s `DATABASE_URL` is **unproven by this inventory** and must be read from Railway
before being relied on.

Automation and operator scripts must use the exact PlantGeo allowlist above and
must reject `Aevani-Postgress`. Use Railway reference variables rather than
copying resolved public proxy credentials between services.

**Current scheduler blockers are pre-existing data/source failures newly surfaced by the
executor, not handoff failures.** `jobs-matview-refresh` sees 200 standing dead letters and current
attempts report `matview_refresh_failed` because `geo.mv_feature_observation_day_axis` and
`geo.mv_signal_cell_daily` are absent. Classify and repair those relations before requeueing the
affected work items; do not clear the dead-letter census merely to make a tick green.
`postgres-fire-perimeters` also entered retry backoff on its first executor turn with
`UpstreamPayloadError: upstream response exceeded the byte limit`; fix or bound the WFIGS payload
before forcing another run.

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

$run = uv run agri-service ops local init `
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

uv run agri-service ops local status $runId
```

The run plan accepts exactly `partitions`, `expected_shards`, and
`expected_outputs`; output entries accept exactly `output_key`, `kind`,
`covered_shards`, and `covered_partitions`. Arrays must be non-empty, sorted,
and unique, and the outputs together must cover every declared shard and
partition. The command returns the deterministic `run_id` and its run directory.
Algorithms should checkpoint after bounded shards and reuse the verified cursor
after interruption:

```powershell
uv run agri-service ops local checkpoint $runId `
  --shard-key colorado-west `
  --cursor-file .\cursor.json `
  --progress 0.25

uv run agri-service ops local resume $runId --shard-key colorado-west
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
uv run agri-service ops local register-output $runId `
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
uv run agri-service ops local finalize $runId `
  --run-validation-report "$runDirectory\validation\run.json"
```

Only then publish the frozen set:

```powershell

$env:LOCAL_PUBLISH_API_URL = "https://<data-service-domain>/api/v1/local-execution"
$env:LOCAL_PUBLISH_TOKEN = "<strong-dedicated-token>"

uv run agri-service ops local publish $runId `
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
3. Use read-only catalog queries to verify PostgreSQL, PostGIS, pgvector, and
   pgcrypto on the target. An image label is not proof that an extension is
   installed in the database. (Note: TimescaleDB was removed on 2026-08-25.)
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
- Config-as-code: none since 2026-09-03 — the root `railway.json` was deleted and its exact settings live on
  the service (Dockerfile `Dockerfile`, pre-deploy `node scripts/migrate.mjs`, start `node server.js`,
  healthcheck `/api/ready` / 60 s, `ON_FAILURE` / 5). Railway deprecated config-as-code (repo files stop being
  read 2026-12-01; new services cannot opt in), and while the root file existed every push made
  `plantgeo-job-executor` build the Next.js Dockerfile. Follow-up: migrate the remaining legacy files with
  `railway config migrate` to `.railway/railway.ts`.
- Liveness: `GET /api/health`
- Rollout readiness: `GET /api/ready` (auth configuration plus bounded PostgreSQL and Redis probes)
- Runtime port: Railway-provided `PORT`, default `3000`

Minimum private references:

```dotenv
DATABASE_URL=${{Plantgeo.DATABASE_URL}}
REDIS_URL=${{plantgeo-Redis.REDIS_URL}}
MARTIN_URL=http://${{plantgeo-martin.RAILWAY_PRIVATE_DOMAIN}}:3000
AGRI_PARQUET_SERVICE_URL=http://${{plantgeo-parquet-api.RAILWAY_PRIVATE_DOMAIN}}:8080
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

`AGRI_PARQUET_SERVICE_URL` is a Railway dashboard/reference variable, not a
hard-coded application URL. It must resolve to the private `plantgeo-parquet-api` hostname;
do not create or substitute a public domain. The Parquet client deliberately has
no production default: a missing binding is a visible typed configuration fault,
not permission to read PostgreSQL. Production activation on 2026-08-28 verified the
reference above resolves inside Railway to `http://plantgeo-parquet-api.railway.internal:8080`;
the private `/ready` call returned HTTP 200 without exposing a public API domain.

### Data-service receiver and published-reader instances

- Repository root: `/services/agri-data-service`
- Dockerfile: `/services/agri-data-service/Dockerfile`
- Config-as-code: `/services/agri-data-service/railway.json`
- Liveness: `GET /health`
- Rollout readiness: `GET /ready` (profile-specific identity, exact Alembic
  revision `20260808_0019`, all four required extensions, and one capability
  conjunct: the connected login can see schema `agri` and the serving view
  exists. The role-grant matrices this probe once asserted were retired with
  the forecast capability roles in `20260808_0019`.)
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

Both DSNs may authenticate the same login: since `20260808_0019` applications
connect with the single owner credential, and readiness asks about capability
(schema visibility, the serving view) rather than identity. Do not configure
`DATABASE_URL` as a production data-service fallback, and do not inject receiver
tokens or actors into the published reader. `SERVICE_PROFILE=combined_local` is
development compatibility only and always fails rollout readiness.

The publication token must differ from application admin and ingestion secrets.
The actor is a server-controlled audit identity bound to that credential; the
workstation cannot choose it. `DATABASE_URL_SYNC` belongs only in an approved,
short-lived Alembic migration context and must use a synchronous PostgreSQL
driver. Do not inject the migration DSN into either long-lived data-service
container.

**`/ready` no longer audits a privilege matrix.** Until 2026-08-08 this paragraph
described one: each runtime login holding only `CONNECT`, `USAGE` (not `CREATE`)
on `public` and `agri`, `SELECT` on `public.alembic_version`, two named sequences
for the receiver and nothing else, with the reader inheriting only
`plantgeo_forecast_reader` and both profiles audited over every `agri` relation,
sequence and function including column grants, memberships and ownership paths.
Revision `20260808_0019` retired the `plantgeo_forecast_*` capability family —
zero members, no DSN, no `USAGE` on `agri` — and applications now connect with
the single owner credential, so an "exactly these privileges and no more"
assertion about an owner would be vacuous. `/ready` reports `database_profile`,
`receiver_identity`, `extensions`, `migration` and `serving_surface`; the pinned
Alembic revision is still asserted in both directions. See
`docs/reports/migration-decision-packet-2026-08-08.md` § Resolution.

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
TILE_CORS_ORIGIN=https://plantgeo.aevani.com
MARTIN_CACHE_SIZE_MB=128
MARTIN_POOL_SIZE=8
```

The configuration disables automatic table and function publication and
allowlists only migration-owned MVT functions. Before creating a public domain,
grant the Martin role only database connect, `geo` schema usage, and execute on
those functions; place the public endpoint behind CDN/WAF rate limits. CORS is
not authentication. Static PMTiles belong in R2/CDN, not the Martin container.
See "Martin tile-function reload after a migration" below for the required
restart after any migration that adds or renames a tile function, and "Martin
CORS allow-list is coupled to the domain set" immediately below for what must
change alongside a domain edit.

### Martin CORS allow-list is coupled to the domain set

**Adding or removing a domain on `plantgeo-main` is not complete until
`infra/martin/martin.yaml`'s `cors.origin` list agrees with it.** Martin matches
the browser's `Origin` header against that list with exact string equality; a
domain missing from the list is not degraded, it is a total outage for every
dynamic layer on that domain, and it fails silently — `curl` gets a clean `200`
because `curl` never sends an `Origin` header, so this class of bug only shows
up in a real browser. This is exactly what happened on 2026-08-17: `plantgeo-main`
carries two active domains (the custom domain and the Railway-provided service
domain), `cors.origin` carried only one entry, and switching that one entry from
one domain to the other only ever fixes one of the two.

`cors.origin` is now a genuine multi-entry list, one placeholder per allowed
origin — see the comment in `infra/martin/martin.yaml` for why Martin cannot
expand a single `TILE_CORS_ORIGIN` value into several list entries (verified
against the pinned `subst` crate and Martin's own config source for `1.10.1`,
the version this repo runs). `TILE_CORS_ORIGIN` above sets the custom-domain
entry. A second variable, `TILE_CORS_ORIGIN_RAILWAY_DOMAIN`, covers the Railway
service domain; its default in `martin.yaml` already matches
`https://plantgeo-main-production.up.railway.app`, so it needs no Railway
variable at all unless that domain is later rotated. **A third domain added to
`plantgeo-main` needs a third placeholder added to `martin.yaml` in the same
change** — Martin's list has no way to grow itself from configuration alone.

Reproduce or verify a CORS allow-list gap with the browser's real origin, not a
bare `curl`:

```
curl -sSI -H "Origin: https://plantgeo.aevani.com" \
  https://plantgeo-martin-production.up.railway.app/fire_risk_tiles,sensor_tiles,evacuation_zone_tiles,burn_severity_tiles,intervention_tiles,watershed_tiles/6/11/22
```

Read the response for `access-control-allow-origin`. Present and equal to the
`Origin` you sent means that origin is allowed; a `200` with `vary: … Origin …`
and no `access-control-allow-origin` header at all means Martin's CORS layer is
active but did not match — that origin is blocked. Repeat with an `OPTIONS`
request and an `Access-Control-Request-Method: GET` header to see the browser's
actual preflight outcome; a mismatched origin returns `400 Bad Request` there.

### Martin tile-function reload after a migration

Martin enumerates PostGIS tile functions from `infra/martin/martin.yaml` and the target
database's `geo` schema **only at process start**; it does not watch the schema or the config
file afterward. `plantgeo-martin` also runs a prebuilt image (`Dockerfile.martin`), so a `git
push` that only adds or renames a migration-owned tile function never rebuilds or restarts it on
its own. Any migration that adds, renames, or drops a `geo` tile function must be followed by:

```
railway service restart --service plantgeo-martin --yes
```

**A missing function is a total outage, not a partial one.** `src/lib/map/sources.ts` groups all
function-backed layers into one comma-joined composite request — `createMartinDynamicSource`
requests `fire_risk_tiles,sensor_tiles,evacuation_zone_tiles,intervention_tiles,building_tiles`
as a single TileJSON/tile call (see that file's own comment on why function and table sources
cannot share a composite). Martin resolves a composite request against its in-memory catalog as
one request; if even one named function is absent from that catalog, Martin returns `404` for
the entire composite, not just the missing layer. MapLibre then drops every layer bound to that
source, so the map silently shows nothing for any of them — there is no partial degradation to
notice mid-incident.

**Diagnose by distinguishing 404 from 204.** Curl the catalog, then the composite:

```
curl https://<martin-domain>/catalog
curl https://<martin-domain>/fire_risk_tiles,sensor_tiles,evacuation_zone_tiles,intervention_tiles,building_tiles
```

- **404** on the composite (or a name missing from `/catalog`) means Martin's in-memory catalog
  does not have that function — either it was never created, or a migration created/renamed it
  after Martin last started. Restart `plantgeo-martin`. Querying a name Martin has never had,
  such as `burn_severity` (no `burn_severity` tile function exists at HEAD), 404s for exactly
  this reason.
- **204** on the composite means every named function exists and executed, and at least one
  returned zero rows for the requested tile — e.g. `intervention_tiles` and `building_tiles`,
  whose backing tables (`interventions`, `geo.osm_buildings`) are currently empty. That is a data
  gap, not a Martin problem, and does not need a restart.

### Retired `plantgeo-ingest-cron` topology

> **Historical operational detail only.** The 2026-09-02 owner directive supersedes every action in
> this section that would configure, restore, deploy or retain this Railway cron. The executor
> registry preserves the commands and source cadences. Do not follow the dashboard/config steps
> below; rollback disables executor lanes and never rebuilds this service.

The former service used `/infra/cron-ingest/Dockerfile` and
`/infra/cron-ingest/railway.json`. Both tracked files are retired. Do not restore their dashboard
settings or recover them from git history; the equivalent commands are independently registered in
the executor.

Historically, the container installed the `agri-data-service` package (uv, locked sync, `--no-dev` runtime —
the same multi-stage pattern as `services/agri-data-service/Dockerfile`, minus its quality-gate
stage, which this image does not need to re-run). Its `ENTRYPOINT` runs both halves of the hourly
pulse directly against Postgres and Redis on the private network:

```
/bin/sh -c "agri-service data ingest-all; ingest_status=$?; agri-service ops jobs-pulse --time-budget-seconds 600; pulse_status=$?; [ $ingest_status -eq 0 ] && [ $pulse_status -eq 0 ]"
```

Historically, `ingest-all` ran the eight forward ingestion sources plus the geometry-repair pass to completion,
isolating each source's failure; `jobs-pulse` then visits every dispatchable lane (`jobs/dispatch.py`'s
`LANE_DISPATCH` registry — the same path `POST /api/v1/jobs/trigger` takes) and every durable archive
definition this database's ledger has ever written, bounded to a 600-second time budget per tick. The
two verbs are joined with `;`, not `&&`, and the run's exit code is their AND: `jobs-pulse` still gets
its turn even when a single ingestion source (say, a FIRMS outage) fails, because an unrelated upstream
outage must not starve the durable lanes for an hour — and the run is red only if either half actually
failed. There is no HTTP hop through `plantgeo-main`: no `GET /api/cron/ingest` call, no
`x-cron-secret` header, no `202`/`409` status mapping, and `CRON_SECRET` is retired (see
`docs/env-vars.md`).

**Consolidated 2026-08-14.** This service previously ran on no schedule at all — see "Cron
consolidation, 2026-08-14" below for the fan-out of one-cron-per-source-and-per-lane it replaces,
and why. Its final schedule was `"0 * * * *"`. That schedule is now null and its start command is a
no-op; the executor owns the former responsibilities as independent failure domains.

**Geometry repair is folded back into `ingest-all`.** `ingest-all`'s last step is still
`ingest-geometry-repair`, which links newly-ingested `geo.features` rows to the Type-2
`geo.geometry` dimension (`ingest/runner.py`'s `run_all_ingestion_jobs` docstring: "It is the last
job because it should claim anything this run's own sources failed to link."). The dedicated
`plantgeo-cron-geometry-repair` service that once gave this step a scheduled caller — back when
`ingest-all` itself ran on no schedule — is gone; see "Cron consolidation, 2026-08-14" below.
The executor lane `postgres-geometry-repair` now supplies geometry repair's hourly cadence.

**Do not repair this service's build configuration.** It is deliberately fenced. The executor uses
repository root `/`, config path `/services/agri-data-service/railway.job-executor.json`, and
Dockerfile `infra/job-executor/Dockerfile`.

**Required variables.** Every `ingest-*` verb opens `db/engine.ingest_session()`, which calls
`settings.require_local_source_loader_database_url()` (`config.py`). Since 2026-08-08 that reader
returns `LOCAL_SOURCE_LOADER_DATABASE_URL` when set and `DATABASE_URL` otherwise, so this
container works with either variable — or both set to the same string, a blank value counting as
unset. Having *neither*, or a DSN that is not a complete `postgresql+asyncpg://` URL, raises
outside `run_isolated_job`, so it is an unhandled traceback and a red deployment rather than a
per-source `failed` summary.

| Variable | Value on this service |
| --- | --- |
| `LOCAL_SOURCE_LOADER_DATABASE_URL` | The Railway **public proxy** DSN, scheme `postgresql+asyncpg://` (see `docs/env-vars.md`): `postgresql+asyncpg://postgres:<password>@switchback.proxy.rlwy.net:37967/plantgeo`. This is what the deployed service sets. |
| `DATABASE_URL` | Optional. Used as the loader DSN when the variable above is unset; setting both to the same string is accepted. |
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

**Verbs this image can run.** `agri-service data ingest-all` is the hourly one (see "Consolidated 2026-08-14"
above): eight sources followed by the geometry repair pass, each isolated, one JSON summary per job.
`agri-service ops jobs-pulse` runs immediately after it in the same `ENTRYPOINT` — see "Cron consolidation,
2026-08-14" below. The other verbs are operator tools on the same image — `ingest-<source>` for a
single source, `ingest-geometry-repair` to link orphaned `geo.features.geometry_id` rows on demand,
`ingest-backfill --source … --since … --until …` to walk a date-ranged history for the sources that
publish one (`nws-sensors`, `sentinel2-ndvi`), `ingest-drought-history --years N` to walk the USDM
archive week by week, and `ingest-mtbs`, which this image also carried but which now runs as the
executor's `mtbs-forward` lane rather than from a Railway cron (see below). Do not run
`ingest-geometry-repair` concurrently with `ingest-all`: both are safe individually and take their
locks in the same order, but the second one to arrive simply waits.

### Cron consolidation, 2026-08-14

> **Historical topology.** This consolidation was superseded by the 2026-09-02 executor-only owner
> directive. It explains command provenance but is not deployment guidance. No service or schedule
> in this section may be recreated.

The nine-service, one-cron-per-source split documented above (and, separately, two per-lane
archive services) was itself reversed on 2026-08-14. Owner directive, quoted verbatim from
`execution/jobs_pulse_command.py`'s module docstring: *"we should not need all the individual
crons, maybe just one to keep a pulse on the job runner."* Before this, each durable lane —
`jobs-run --lane firms-archive`, `jobs-run --lane streamflow-archive`, and the HTTP-triggered
`strategy-mv-refresh` — needed its own scheduled cron service, on top of the eight per-source
services and `plantgeo-cron-geometry-repair` above. `agri-service ops jobs-pulse` replaces that fan-out
with one process that visits every lane once per tick and reports one row per lane; folded into
`plantgeo-ingest-cron`'s `ENTRYPOINT` alongside `ingest-all` (see "`plantgeo-ingest-cron`"
above), it is what let all eleven of those other services be deleted. Twenty `infra/cron-*/`
directories were deleted in the same pass; only three remain — `cron-ingest`, `cron-mtbs`, and
`cron-soilgrids` — matching the three services left in the "Production boundary" table.

**The nine per-source services are now one hourly `ingest-all` run.**

| Former service | Former directory | Former `cronSchedule` | Why that cadence, historically |
| --- | --- | --- | --- |
| `plantgeo-ingest-streamflow` | `infra/cron-streamflow/` | `*/30 * * * *` | USGS NWIS gauges report on the order of minutes; every 30 minutes tracked that without over-polling. |
| `plantgeo-ingest-weather` | `infra/cron-weather/` | `10 * * * *` | Open-Meteo current conditions refresh hourly; offset to `:10` so it did not stack with the other services' top-of-hour ticks. |
| `plantgeo-ingest-fire-perimeters` | `infra/cron-fire-perimeters/` | `20 * * * *` | WFIGS interagency perimeters redraw on the order of tens of minutes during active incidents; hourly at `:20`. |
| `plantgeo-ingest-firms` | `infra/cron-firms/` | `30 */3 * * *` | The job fans out across all three VIIRS NRT products (`firms.py`'s full-constellation query), and NRT products land a handful of times a day per satellite, not continuously; every 3 hours tracked new overpasses without repeatedly re-requesting a product that had not refreshed since the last poll. |
| `plantgeo-ingest-drought` | `infra/cron-drought/` | `0 14 * * 4` | USDM publishes one release a week, Thursdays; the per-source split existed specifically because polling hourly (168x/week) for a weekly release was wasteful. |
| `plantgeo-ingest-ndvi` | `infra/cron-ndvi/` | `0 5 * * *` | Sentinel-2 L2A revisits the Pacific Northwest every 2-5 days and a scene needs cloud-free daylight to be usable; a daily check at 05:00 UTC was enough to catch each new clear scene. |
| `plantgeo-ingest-sensors` | `infra/cron-sensors/` | `40 * * * *` | NOAA NWS ground-station readings arrive hourly (`sensors.py`'s own identity-builder docstring); polling faster bought nothing since a station's `timestamp` only changes once an hour. |
| `plantgeo-ingest-evacuation-zones` | `infra/cron-evacuation-zones/` | `*/15 * * * *` | The one layer in the set that is life-safety information (active wildfire evacuation orders), so it was polled close to the fire-perimeters cadence rather than hourly. |
| `plantgeo-cron-geometry-repair` | `infra/cron-geometry-repair/` | `50 * * * *` | Not a source poll — existed only to give `ingest-all`'s own geometry-repair step a scheduled caller while `ingest-all` itself ran on no schedule (see "Geometry repair is folded back into `ingest-all`" above). |

All nine rows above are now covered by the single hourly `ingest-all` run on
`plantgeo-ingest-cron` (`ingest/runner.py`'s `run_all_ingestion_jobs`: the eight sources plus the
geometry-repair pass, each isolated so one source's failure never blocks another's).

**The two per-lane archive services are now `jobs-pulse`'s durable-archive namespace.**

| Former service | Former directory | Former verb | Now covered by |
| --- | --- | --- | --- |
| `plantgeo-cron-archive-firms` | `infra/cron-archive-firms/` | `jobs-run --lane firms-archive` | `jobs-pulse`'s durable-archive namespace, hourly |
| `plantgeo-cron-archive-streamflow` | `infra/cron-archive-streamflow/` | `jobs-run --lane streamflow-archive` | `jobs-pulse`'s durable-archive namespace, hourly |

`jobs-pulse` visits three namespaces per tick, in order, the first two mirroring
`jobs/dispatch.py`'s own split of responsibility: dispatchable lanes (`jobs/dispatch.py`'s
`LANE_DISPATCH` registry — the same path `POST /api/v1/jobs/trigger` takes, which is also how the
HTTP-triggered `strategy-mv-refresh` lane now gets a cron tick it never had before), every durable
archive definition this database's ledger has ever written that matches an `ingest/lanes.py` archive
lane, run through the same `run_archive_definition_slice` function `jobs-run` itself calls, and then
the data-quality pass described in "The data-quality loop, restored inside `jobs-pulse`" below. A lane paused
(`agri.job_definition.enabled = false` on every version of its name) is skipped, not attempted, and
one lane raising or dead-lettering never stops another's turn — each lane opens and closes its own
session.

**Consolidating cadences was safe, not just tidy.** Folding nine per-source cadences and two
lane-specific schedules back onto one hourly tick means sources that used to be polled less often —
USDM weekly, NDVI daily, evacuation zones every 15 minutes — are now checked hourly regardless. That
is cheap rather than wasteful because every writer's diff rejects an unchanged payload: `ingest-mtbs`'s
own docstring states this property explicitly ("the writer's diff rejects an unchanged payload and
the geometry adapter confirms an unchanged shape"), and `ingest/reconcile.py` notes the same thing for
its own re-walks ("the writer rejects an unchanged payload anyway"). An hourly re-check of a release
that has not moved since Thursday costs a request and a no-op write, not a duplicate.

**Retired survivors: `plantgeo-cron-mtbs` and `plantgeo-cron-soilgrids`.**

The executor lane `mtbs-forward` runs `agri-service data ingest-mtbs` weekly, Tuesdays at 07:55 UTC
(`55 7 * * 2`). `ingest_mtbs`'s own docstring (`ingest/commands.py`) is why it was
never folded into `ingest-all`: *"Unlike the other verbs this one is not hourly-shaped: MTBS
publishes quarterly and a fire year accretes over two to four years, so a run re-reads cohorts that
almost never move... A fire year with no established release publication date fails the run rather
than borrowing an ignition date, a run clock, or an assumed mapping lag for `observedAt`."* Chaining
that failure mode into an hourly `ingest-all` tick would turn a routine "this fire year has no
release yet" catalog gap into a permanently red hourly cron that masks real ingest failures behind
it, so `mtbs-forward` keeps its own weekly cadence and independent exit-code verdict.

The executor lane `soilgrids-cache-warm` runs `node scripts/warm-soilgrids.mjs 120` hourly at `:25`
(`25 * * * *`) from the combined executor image. `scripts/warm-soilgrids.mjs`'s own header
explains why: SoilGrids v2.0 is a static raster, not a time series, so "there is no backfill lane
for it — each cell is fetched once and stays valid," and the driver exists to finish what a
hand-invoked, 16-point-per-call API route could not: a bounded, resumable walk over the ~1568-cell
`sentinel2-ndvi-0p25deg` grid at 120 cells/hour. That is a finite warm-up, not a recurring
ingestion lane, which is why it never moved into `ingest-all` or `jobs-pulse` alongside the other
eleven services.

**Current mechanics.** The sole scheduler builds `infra/job-executor/Dockerfile` from repository
root `/` with config `/services/agri-data-service/railway.job-executor.json`. Its image contains the
Python service and the Node SoilGrids driver. A guard test rejects every tracked Railway JSON that
reintroduces `cronSchedule` or a retired cron-only path.

**Correction — 2026-09-03: that config path is REQUIRED, and production does not have it set.**
Observed via the Railway API (project `6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990`, environment
`b7cfa813-8a5c-4fcd-80f2-cab736d840a7`) on 2026-09-03: `plantgeo-job-executor`'s config-as-code field
is absent from its service configuration entirely — by contrast, `plantgeo-parquet-api` shows
`configFile: /services/agri-data-service/railway.json`. Without a config-as-code path, Railway falls
back to discovering the repository-root `railway.json` (`build.dockerfilePath: "Dockerfile"`, the
Next.js app) on every GitHub push, which overrides the dashboard's Dockerfile path and builds the
Next.js image instead of `infra/job-executor/Dockerfile`. That image dies at
`Error: NEXT_PUBLIC_PMTILES_URL must be a reviewed production URL` ([build 4/9]). Four deployments
have failed this way: `003bfc6e` at `e4490c3` (2026-09-02 17:51 UTC), `fbc4cbb7` at `e4a101f`
(2026-09-03 06:28), `9fa4c8a8` at `1da1a28` (2026-09-03 12:39), and `5523d2e8`
(2026-09-03 12:40, `railway service redeploy --from-source` — a from-source redeploy does not bypass
root discovery either). The only successful executor build, `b1f35a20` (2026-09-02 18:03, reason
"redeploy"), loaded `infra/job-executor/Dockerfile`; whatever setting made that possible no longer
exists on the service. **Remedy:** set the service's config-as-code path to
`services/agri-data-service/railway.job-executor.json` (dashboard Settings → Config-as-code, or the
Railway MCP `update-service` `railwayConfigFile` — Railway CLI 5.45.2 has no service-update verb).
**Interim consequence:** the executor is pinned at `e4490c3` while `plantgeo-main` and
`plantgeo-parquet-api` have already advanced to `1da1a28`; every push redeploys those two services
but not the executor until the config-as-code path is set.

**Nine more directories deleted with no live service behind them.** `cron-era5-land-continue`,
`cron-era5-land-coverage-fill`, `cron-era5-land-coverage-status`, `cron-nasa-power-continue`,
`cron-nasa-power-coverage-fill`, `cron-nasa-power-coverage-status`, `cron-maintain-firms`,
`cron-maintain-streamflow`, and `cron-validate` were deleted in the same pass, but none of them ever
backed a created Railway service — they were config-as-code for services that were never
provisioned, so nothing operational was lost. Their schedules and plan arguments are recoverable
from git history if one is ever needed again.

### The data-quality loop, restored inside `jobs-pulse` (2026-08-14)

Deleting `cron-maintain-firms`, `cron-maintain-streamflow` and `cron-validate` cost nothing
operationally — none had ever been provisioned — but it did leave `jobs-plan-gaps`,
`jobs-reconcile-lane` and `validate-streams` on **no schedule at all**. That mattered more than the
directories did: those three verbs are the loop that turns a *detected* gap into a *claimable* work
item, so while they were manual-only a hole in a layer could sit indefinitely with every cron green.

The executor now runs these as independent lanes: `maintenance-firms-archive-reconcile`,
`maintenance-firms-archive-plan-gaps`, `maintenance-streamflow-archive-reconcile`,
`maintenance-streamflow-archive-plan-gaps`, and `maintenance-validate-streams`. The archive workers
are likewise isolated as `jobs-firms-archive` and `jobs-streamflow-archive`. Four properties are
deliberate:

- **The lane set is derived, not listed.** Both verbs take a required `--lane`, so restoring them as
  cron services would have meant naming the lanes in a hard-coded shell string — and a hard-coded
  lane list joins to nothing the day a lane is renamed. The pass reuses the lane set the durable
  namespace already discovered from `agri.job_definition`, so a new archive lane is maintained from
  the moment its first run is planned, with no second list to update.
- **They are a pass, not dispatchable lanes.** A dispatchable lane is driven by `run_job_slice`,
  which takes a lease and writes `agri.job_work_item` rows of its own; reconcile and gap-planning
  exist to *mutate those same rows* for other lanes. Registering them would have the ledger's
  bookkeeping and its maintenance contend for one set of rows in one transaction.
- **Order is load-bearing.** Reconcile settles the windows a layer already serves *before*
  gap-planning measures holes, so gap-planning reads the settled truth rather than windows that were
  about to be marked succeeded. `validate-streams` runs last, so its verdict describes the state the
  tick leaves behind and so it is the step a spent time budget drops — an unmeasured hour is
  recoverable, an un-walked hour of backfill is not.
- **`validate-streams`' exit rule is carried through unchanged.** A stream reported `invalid` (rows
  that are there are wrong) fails the tick; a stream reported `incomplete` (a backfill in flight,
  which is weeks of correct operation) does not. The pulse reports these as distinct outcomes —
  `invalid` versus `raised` — so an operator can tell "the gap detector is broken" from "the gap
  detector works and is telling you something".

The pass is skippable with `agri-service ops jobs-pulse --skip-maintenance` for an operator draining lane
work by hand. The scheduled tick must never use it. `agri-service ops jobs-pulse --dry-run` lists every
maintenance step it would run alongside the other two namespaces, applying nothing.

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
2. **Pre-deploy migration.** The service's pre-deploy command (set on the service since 2026-09-03; formerly
   `railway.json`'s `deploy.preDeployCommand`) is `node scripts/migrate.mjs`. Railway runs it in
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
  ones. Run `uv run agri-service ops job-logs-maintain --retention-days 30
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
