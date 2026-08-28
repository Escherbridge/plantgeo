# PlantGeo Railway production configuration

PlantGeo currently runs in the shared Railway project named `Aevani`. The
project boundary is not a database boundary: every service-to-service variable
must name the intended PlantGeo service explicitly.

## Resource boundary

| Railway service | Status and allowed use |
| --- | --- |
| `plantgeo-main` | Next.js application built from the repository root. |
| `plantgeo-dataservice` | Python data service built from `services/agri-data-service`. |
| `plantgeo-Redis` | PlantGeo cache, pub/sub, and non-durable wake-up transport. |
| `Plantgeo` | Existing PlantGeo PostgreSQL 18.3 database. It was not extension-ready in the July 2026 audit. |
| `plantgeo-spatiotemporal-db` | **The production database since the cutover.** PostgreSQL 18.4 + PostGIS 3.6, reached on the `switchback.proxy.rlwy.net:37967` TCP proxy. Was provisioned from `timescale/timescaledb-ha:pg18`; TimescaleDB and timescaledb_toolkit were **dropped 2026-08-25** after holding one always-empty hypertable (`tracking.positions`, 0 rows, 0 chunks) and no continuous aggregate. Installed extensions, measured that day: btree_gist, hypopg, pg_buffercache, pgcrypto, plpgsql, postgis, vector. |
| `plantgeo-martin` | Provisioned private service. Its bootstrap image crashed against the legacy database because PostGIS was absent; its sealed reference now targets the replacement for the next reviewed deployment. Keep it private until extension and migration verification succeeds. |
| `Aevani-Postgress` | Aevani parent/affiliate/UGC data. Never reference, query, migrate, reset, or grant it to a PlantGeo service. |

**Historical, July 2026 audit — the `Plantgeo` service no longer exists in this project; the row above supersedes it.** The existing `Plantgeo` database reported PostgreSQL 18.3, database `railway`,
role `postgres`, only the `public` schema, and none of `postgis`, `timescaledb`,
`vector`, `pgcrypto`, or `uuid-ossp`. That is an observation, not permission to
install extensions or run migrations against it.

## Service configuration

### `plantgeo-main`

- Repository root: `/`
- Config-as-code: `/railway.json`
- Dockerfile: `/Dockerfile`
- Liveness endpoint: `GET /api/health`
- Rollout readiness endpoint: `GET /api/ready` (auth configuration + PostgreSQL + Redis)
- Runtime port: `3000`

Required service references:

```dotenv
DATABASE_URL=${{Plantgeo.DATABASE_URL}}
REDIS_URL=${{plantgeo-Redis.REDIS_URL}}
AGRI_PARQUET_SERVICE_URL=http://${{plantgeo-dataservice.RAILWAY_PRIVATE_DOMAIN}}:8000
```

`DATABASE_URL` remains on `Plantgeo` until a reviewed data migration and
cutover explicitly replaces it. Public `NEXT_PUBLIC_*` map values are compiled
into the Next.js bundle during the Docker build; they must be configured before
a production build. `AGRI_PARQUET_SERVICE_URL` is server-only and must remain a
private Railway reference. If it is absent or invalid, the Parquet reader fails
visibly and must not retry PostgreSQL or use a public data-service domain.

### Data-service receiver and published-reader profiles

- Repository root: `/services/agri-data-service`
- Config-as-code: `/services/agri-data-service/railway.json`
- Dockerfile: `/services/agri-data-service/Dockerfile`
- Liveness endpoint: `GET /health`
- Rollout readiness endpoint: `GET /ready`

Production uses two service instances built from the same source. The private
receiver sets `SERVICE_PROFILE=receiver_writer` and only
`RECEIVER_WRITER_DATABASE_URL`; it mounts the authenticated local-publication
and historical-promotion routes. The serving instance sets
`SERVICE_PROFILE=published_reader` and only
`PUBLISHED_READER_DATABASE_URL`; it mounts the published forecast read route.
Neither profile falls back to `DATABASE_URL`, and `combined_local` always fails
rollout readiness. Do not inject receiver tokens into the reader service.

Both profiles target the selected private PlantGeo database with different
least-privilege login roles. They do not open Redis in phase one: publication
commits a durable database outbox record. Alembic is the sole migration authority
for `agri`; neither runtime may create or reset database objects.

Forecast publication has two separate contracts. The generic local-publication
receiver stores validated artifacts and advances an `artifact_only`
`publication_pointer`; that is custody evidence, not a typed or serving
forecast. A metric forecast becomes serveable only after its release-pinned
rows pass the revision `20260722_0005` quality gates and any required
`20260722_0006` historical hindcast gate, `20260722_0007` calibration-horizon
guard, and `20260722_0008` active-policy/calibration-sample plus versioned-receipt
guards, its
`forecast_receipt` is finalized, and its `forecast_publication` is published.
Agents and map/chart consumers read the typed forecast API backed by
`agri.v_forecast_series_serving`, never the generic artifact pointer. See the
[SQL forecasting contract](../../docs/sql-forecasting-framework.md) and the
reviewed [first metric rehearsal](../local-warehouse/first-metric-forecast.sql).
`strategy_selection` remains gated and is not part of this deployment path.

### `plantgeo-martin`

- Config-as-code: `/infra/railway/martin.railway.json`
- Dockerfile: `/Dockerfile.martin`
- Health endpoint: `GET /health`
- Set `PORT=3000` explicitly for a stable private-network URL.

The service may be provisioned before cutover, but it must remain private and
must not be treated as healthy until the target database passes the verification
gate. Its variables must use an explicit PlantGeo service reference:

```dotenv
DATABASE_URL=<sealed DSN for a Martin-only login on plantgeo-spatiotemporal-db>
PORT=3000
TILE_CORS_ORIGIN=https://plantgeo.aevani.com
MARTIN_CACHE_SIZE_MB=128
MARTIN_POOL_SIZE=8
```

Never use `${{Aevani-Postgress.DATABASE_URL}}`. Martin's configuration
allowlists four migration-owned MVT functions and disables automatic table and
function publication. Before assigning a public domain, replace any bootstrap
admin connection with a login restricted to database connect, `geo` schema
usage, and execute on the four allowlisted functions. Put the public domain
behind CDN/WAF request limits; CORS is not authentication. Static PMTiles are
served directly from R2/CDN and are not mounted into the Railway container.

**`plantgeo-main`'s domain set and Martin's `cors.origin` list are coupled —
adding a domain to one without the other is a silent, browser-only outage for
every dynamic layer** (`curl` sees a clean `200`; only a request carrying a real
`Origin` header sees the missing `Access-Control-Allow-Origin`). `TILE_CORS_ORIGIN`
above is only the custom-domain half; `infra/martin/martin.yaml` also carries a
`TILE_CORS_ORIGIN_RAILWAY_DOMAIN` placeholder defaulted to the Railway service
domain, so that half needs no variable here unless the domain changes. See
`docs/deployment.md`'s "Martin CORS allow-list is coupled to the domain set" for
the full reproduction command and the verified basis for this design.

The web service uses the private URL below for server calls, while browser code
uses Martin's generated public/custom HTTPS domain:

```dotenv
MARTIN_URL=http://${{plantgeo-martin.RAILWAY_PRIVATE_DOMAIN}}:3000
NEXT_PUBLIC_DYNAMIC_TILES_URL=https://${{plantgeo-martin.RAILWAY_PUBLIC_DOMAIN}}
```

## Future PostgreSQL 18 pre-deploy and cutover checklist

This checklist is a future operator-controlled change gate. It does not
authorize a Railway command, extension install, migration, role change, deploy,
or cutover. Every evidence item must name the exact Railway project,
environment, target service ID, database, image digest, migration commit, UTC
time, and operator/change record. The target service name must be exactly
`plantgeo-spatiotemporal-db`; `Aevani-Postgress` is always out of scope.

### 1. Prove PostgreSQL 18 extension parity

- Record `server_version`, `server_version_num`, image digest, and both
  available and installed versions of `postgis`, `vector`, and
  `pgcrypto`. A package being present in an image is not an installed extension.
- Compare the PG18 versions and behavior with the pinned PostgreSQL 16 image
  used by the operator-run governance rehearsal in `services/agri-data-service`
  (`uv run alembic upgrade`, then `uv run pytest`, against a disposable
  database). The rehearsal exercises the historical forecast contract at
  `20260722_0008`, then migrates the same disposable database through the
  current head and proves the constrained-loader and declarative-schema
  contracts. It is not PG18 production evidence. There is no GitHub Actions
  pipeline; see `docs/deployment.md` — "Deployment workflow".
- If any extension is unavailable, incompatible, or requires a restart, stop.
  An approved operator must install extensions before migration; neither a
  service start command nor Alembic may create them.

Read-only catalog evidence:

```sql
SELECT current_setting('server_version') AS server_version,
       current_setting('server_version_num') AS server_version_num;

SELECT available.name, available.default_version, installed.extversion
FROM pg_available_extensions AS available
LEFT JOIN pg_extension AS installed ON installed.extname = available.name
WHERE available.name IN ('postgis', 'vector', 'pgcrypto')
ORDER BY available.name;
```

### 2. Verify backup and restore before migration

- Take a full, consistent source backup in custom format, encrypt it at rest,
  record its byte size and SHA-256 checksum, and retain the matching global
  role/grant inventory separately. A Railway snapshot alone is not the restore
  proof.
- Restore that exact backup into a separately named disposable PostgreSQL 18
  database with the same extension versions. Record restore duration and logs.
- On the restored copy, compare schema inventories, Alembic/Drizzle versions,
  row counts, validated release-set manifests, artifact checksums, historical
  source variants, and any finalized/published forecast receipt checksums with
  the source. Resolve every mismatch before continuing.
- Keep the pre-migration backup and restored rehearsal database until the
  production observation window closes.

### 3. Guard and rehearse migrations through `20260725_0013`

- Require the integrated CI checks plus the PostgreSQL 16 governance rehearsal
  to pass at the exact commit proposed for production.
- Run Drizzle and Alembic against the restored PG18 rehearsal database using a
  short-lived migration identity. Accept only the reviewed linear predecessors
  `20260720_0004`, `20260722_0005`, `20260722_0006`, `20260722_0007`,
  `20260722_0008`, `20260723_0009`, `20260723_0010`, `20260725_0011`, or
  `20260725_0012`, then apply their strict successors in order through
  `20260725_0013`. An already-current `20260725_0013` database may only be an
  idempotent no-op; any other starting revision is a stop condition.
- After rehearsal, assert the single Alembic head is `20260725_0013`, readiness
  expects `20260725_0013`, all four extensions remain installed, the
  representative forecast PostgreSQL contract passes with
  `AGRI_TEST_DATABASE_URL` pointed only at a disposable database migrated to
  that head, and the constrained-loader and declarative-schema contracts pass
  against that same database (see `services/agri-data-service/tests/conftest.py`).
- Capture migration SQL/logs, elapsed time, lock observations, and before/after
  schema fingerprints. Set bounded lock and statement timeouts and stop on the
  first error. Never run migrations from a long-lived service start command.
- A future Railway pre-deploy job is allowed only after its image contains the
  pinned migration tool, locked dependencies, migration files, and the
  declarative `db/` objects loaded by those migrations; it must use an isolated
  migration DSN and preserve the same revision guard. The current Next.js
  runtime image does not satisfy that requirement.

### 4. Forecast capability roles are retired -- do not provision them

Until 2026-08-08 this step installed and audited the four least-privilege
forecast capability roles from `create-forecast-roles.sql`. Alembic revision
`20260808_0019` retired that family (the roles had no members and no DSN ever
authenticated through them), and applications now connect with the single
owner credential. Following the old provisioning steps would re-create exactly
what the migration drops and fail the teardown contract test
(`test_security_definer_lockdown_postgresql.py`).

What still holds on a rehearsal cluster:

- `plantgeo_loader` remains a plain production login (deployed cron ingest and
  the local archive walks authenticate as it); no repo script provisions or
  audits it any more.
- `PUBLIC` must still have no database/schema create privilege and no
  execution path to the covariate functions (`20260802_0016`'s revokes stand);
  the readiness probe's `serving_surface_ready` conjunct is the surviving
  capability check.
- The retired bootstrap scripts in `infra/local-warehouse/` carry
  `-- RETIRED 2026-08-08 -- DO NOT RUN` headers naming the revision that drops
  what they create.

### 5. Require promotion and forecast receipts

- Verify typed historical promotion receipts and their source-release,
  release-set, manifest, payload, support, and transform checksums before a
  forecast uses the promoted history.
- For the first metric forecast, retain the registered series/source variant,
  native and output support, deterministic training/holdout cutoff, SQL linear
  baseline, naive last-value metrics, quality-policy result, empirical residual
  quantiles, feature checksum, model/code/parameter checksums, and job outputs.
- Publish only when the run is validated, the policy passes, every value belongs
  to a finalized immutable receipt, and the publication checksum matches. A
  failed or ineligible backtest remains durable evidence and publishes nothing.
- Assert the promotion contains no fabricated ML, recommendation, or
  `strategy_selection` output. A metric forecast receipt is not a strategy
  recommendation.

### 6. Prove typed serving before consumer cutover

- From the forecast-reader identity, verify typed API responses and
  `agri.v_forecast_series_serving` agree on publication/receipt/series IDs,
  point count, issue time, valid-time bounds, point and empirical-band values,
  units, native/output support, quality metrics, and checksums.
- Verify unpublished, failed, staged, or checksum-mismatched receipts return no
  serving rows. Verify the API rejects or returns unavailable for unknown and
  unauthorized scopes.
- Exercise one agent query and one map/chart query through their production-like
  private service paths. Neither consumer may read a generic artifact-only
  publication or infer a recommendation from a metric forecast.
- For Martin, separately verify `/health`, `/catalog`, and a representative
  allowlisted MVT request with its Martin-only login before any public domain is
  assigned.

### 7. Establish observability and staleness gates

- Record and alert on readiness revision/privilege failures, migration errors,
  forecast validation/publication failures, checksum mismatches, denied role
  operations, oldest published forecast age, latest issue/valid time, source
  release age, serving query latency/error rate, connection saturation, lock
  waits, database storage, and backup age.
- Establish explicit warning and stop-serve thresholds per metric/series. An
  absent or stale forecast must produce an unavailable/stale response; it must
  never silently fall back to a demo, generic artifact, or strategy output.
- Keep migration, promotion, forecast receipt, publication, manual refresh, and
  consumer smoke-test evidence correlated by immutable IDs/checksums rather than
  mutable service names alone.

### 8. Keep materialized-view refresh explicit

Revisions `20260722_0005` through `20260722_0008` create no refresh schedule. The reviewed MV-refresher
identity may run the following only as a deliberate post-publication operation,
with the invocation and resulting row/freshness evidence recorded:

```sql
SELECT agri.refresh_forecast_ml_daily_serving();
```

The first SQL metric forecast reads `agri.v_forecast_series_serving` directly;
the materialized view filters to opted-in published ML series, so refreshing it
does not publish or aggregate the SQL baseline. Do not add a Railway cron or
background forecast worker for this operation.

### 9. Cut over incrementally and retain restore-based rollback

- Update one consumer at a time with explicit Railway reference variables:
  data service, private Martin, then web/API. Observe readiness, serving proofs,
  database metrics, and stale-data gates between changes. Keep the previous
  database intact and prevent new writes to it once the cutover boundary is
  declared.
- Define rollback triggers before cutover: readiness/revision mismatch,
  extension or privilege drift, failed receipt/serving checksum, missing rows,
  unacceptable latency/error/lock pressure, or restore verification failure.
- Rollback is restore based: provision a clean compatible target, install the
  reviewed extension set, restore the last verified backup plus any separately
  captured approved post-backup changes, reapply the reviewed role matrix, and
  re-run receipt/serving proofs before repointing consumers. Do not downgrade
  Alembic in place and do not treat a reference-variable flip without data
  reconciliation as recovery.
- Record recovery-point and recovery-time results from the rehearsal. Do not
  remove the prior database or backups until the observation window and rollback
  acceptance criteria are complete.

## Deployment order

1. Verify the PlantGeo database and migration plan.
2. Verify `plantgeo-Redis` authentication over the private reference URL.
3. Deploy and verify `plantgeo-dataservice`.
4. Redeploy `plantgeo-martin` only after the database gate passes; verify `/health`,
   `/catalog`, and a composite MVT request.
5. Build `plantgeo-main` with final public R2 and Martin URLs, then verify the
   browser network panel uses no localhost, placeholder, or private hostnames.

Railway has no `depends_on` equivalent. Services must retry transient startup
connections, and readiness checks should cover critical dependencies without
turning liveness checks into cascading restarts.
