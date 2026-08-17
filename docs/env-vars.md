# Environment Variables

`.env.example` and `services/agri-data-service/.env.example` are the executable
templates. This document explains ownership and production policy; it does not
contain usable credentials.

Never commit `.env`, `.env.local`, `.env.production`, Railway-resolved values,
or local publication tokens. A variable prefixed with `NEXT_PUBLIC_` is embedded
in the browser bundle and must be treated as public.

## Web application

### Required runtime dependencies

| Variable | Scope | Policy |
| --- | --- | --- |
| `DATABASE_URL` | server | PlantGeo PostgreSQL DSN. Production currently references `${{Plantgeo.DATABASE_URL}}`; change only during the reviewed replacement cutover. |
| `MIGRATION_DATABASE_URL` | server, optional | DDL-capable DSN for the `preDeployCommand` (`scripts/migrate.mjs`). Unset means the step reuses `DATABASE_URL`; set it once the runtime role loses `CREATE` on the database. Never the Martin role. |
| `REDIS_URL` | server | `${{plantgeo-Redis.REDIS_URL}}` in Railway. Cache/pub-sub only, never the durable job ledger. |
| `NEXTAUTH_SECRET` | server | Unique high-entropy production secret. Rotating it invalidates sessions. |
| `NEXTAUTH_URL` | server | Canonical application origin, `http://localhost:3001` in development. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | server | Optional pair; configure both or omit the provider. |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | server | Optional pair; configure both or omit the provider. |

Use separate least-privilege database identities for the web application,
Python publisher, pre-deploy migration, and Martin. Runtime services must not
use the database owner.

### Public map configuration

| Variable | Policy |
| --- | --- |
| `NEXT_PUBLIC_PMTILES_URL` | Public, immutable basemap PMTiles URL with HTTP Range support. Mirror to a reviewed R2/CDN origin for production. |
| `NEXT_PUBLIC_TERRAIN_URL` | Optional reviewed terrain URL template containing `{z}`, `{x}`, and `{y}`. |
| `NEXT_PUBLIC_DYNAMIC_TILES_URL` | Public/custom HTTPS Martin origin. Leave unset until Martin passes its database, role, catalog, tile, CORS, and rate-limit gates. |
| `NEXT_PUBLIC_APP_URL` | Public canonical origin used in links and email, `http://localhost:3001` in development. |
| `NEXT_PUBLIC_INGEST_BBOX` | Optional. Same `west,south,east,north` format as `INGEST_BBOX`; set it identically so the opening map camera (computed client-side before `getIngestionCoverage` resolves, see `src/lib/map/coverage-region.ts`) matches the server's real coverage box. Falls back to a hardcoded box mirroring current production `INGEST_BBOX` when unset, so behavior is correct today regardless — but drifts from a future `INGEST_BBOX` change until this is set. Not yet forwarded as a Docker build ARG; see `.env.example` for the required Dockerfile addition. |

Never place a credential, provider token, `localhost` production value, or
`*.railway.internal` hostname in a public variable. Environmental tile layers
remain disabled until a server-issued database publication catalog supplies an
immutable approved URL, release, and checksum. Next.js compiles public
values during image build, so changing a Railway runtime value without rebuilding
does not update the client.

### Internal services

| Variable | Scope | Current status |
| --- | --- | --- |
| `MARTIN_URL` | server | Private Martin URL. Keep unset in production until `plantgeo-martin` is activated. |
| `TILE_CORS_ORIGIN` | Martin | Exact web origin allowed to request public dynamic tiles; CORS is not authentication. Fills one entry in `infra/martin/martin.yaml`'s `cors.origin` list -- the custom-domain entry. That list is a genuine multi-entry list, one placeholder per origin, because Martin cannot expand a single env var into several list entries. A domain active on `plantgeo-main` but missing from the list is a silent, browser-only outage: `curl` returns a clean `200` because it never sends an `Origin` header. See `docs/deployment.md`'s "Martin CORS allow-list is coupled to the domain set". |
| `TILE_CORS_ORIGIN_RAILWAY_DOMAIN` | Martin | Second `cors.origin` entry, for the Railway-provided service domain (as opposed to the custom domain covered by `TILE_CORS_ORIGIN` above). Defaults in `martin.yaml` to the current Railway domain, so production needs no value set here unless that domain is renamed or rotated. |
| `VALHALLA_URL` | server | Optional/deferred routing service; absence must produce an unavailable response. |
| `VALHALLA_PBF_URL` | operator | Optional graph-build input, not a web runtime setting. Pin the release/checksum before use. |
| `PHOTON_URL` | server | Optional/deferred geocoder. Production should use an owned or contractually approved service, not an undocumented public instance. |

### Ingestion and provider credentials

| Variable | Policy |
| --- | --- |
| `INGEST_SECRET` | Dedicated bearer secret for authenticated ingestion routes. Required in production for those routes. |
| `INGEST_BBOX` | Required for scheduled acquisition; `west,south,east,north`, capped at 30° longitude by 20° latitude. Read by the authenticated `/api/ingest/*` routes and by every ingestion cron container — `plantgeo-ingest-cron`'s `agri-cli ingest-all` and each per-layer service's single verb. Start with one reviewed region and set it identically on every cron service; a mismatched bbox across services silently narrows one source's coverage relative to the others. |
| `NASA_FIRMS_KEY` | Server-only; required only when running the FIRMS acquisition path — the web routes, `plantgeo-ingest-cron`'s `ingest-all`, and `plantgeo-ingest-firms`'s `ingest-firms` all read it. No other per-layer service needs it. |
| `CDSAPI_URL` / `CDSAPI_KEY` | Server-only; required only by `agri-cli historical-era5-backfill` (ERA5-Land volumetric soil moisture). Resolved environment-first, then `Settings`/`.env` — since 2026-08-08 a `.env` entry is enough and the old export-only requirement is gone; a blank export is treated as unset. Needs a free Copernicus CDS account with the ERA5-Land dataset licence accepted in a browser; no API retry clears an unaccepted licence. |
| `OPEN_METEO_API_KEY` | **Optional.** Server-only; read only by `agri-cli historical-open-meteo-backfill` (the ERA5-Land archive lane). Absent is fully supported and is the default: the lane calls the keyless free host `archive-api.open-meteo.com`, which is what the published repo and the existing 16-cell probe use. Present switches the real request to the Professional host `customer-archive-api.open-meteo.com` and appends `apikey=<key>`. Read from `os.environ` directly, not through pydantic-settings, so a `.env` entry does NOT reach it — export it into the shell running the command. It is not part of any plan and does not change `plan_checksum`, so setting or clearing it never orphans a checkpoint or the local raw cache. The key is never written to a plan, checkpoint, cache receipt, log line, or the warehouse; the persisted `source_release.query_parameters.request_url` records the host that answered and nothing else. |
| `FIRMS_DAY_RANGE` | Optional, cron container only. FIRMS lookback window in days; must be a plain non-negative integer string or it silently falls back to the default. Clamped to 1-5 (the API answers 400 above 5), default 2. |
| `INGEST_MAX_SOURCE_RECORDS` | Optional, cron container only. Caps the number of records accepted from a single ingestion source per run. Clamped to 1,000-50,000, default 10,000. |
| `WEATHER_SAMPLE_SPACING_DEGREES` | Optional, cron container only. Starting grid spacing (degrees) for the Open-Meteo sampling sweep. Clamped to 0.25-5.0, default 1.0; the sweep coarsens spacing until `columns * rows <= 150`, it never slices the grid. |
| `MAPILLARY_ACCESS_TOKEN` | Server-only imagery proxy token. Mapillary remains a licensed imagery exception, not a warehouse bypass. |
| `FIRES_LAYER_ID` | **Web routes only** (`POST /api/ingest/fires`). Existing layer name/UUID for submitted fire perimeters; default `fire-perimeters`. The cron container's WFIGS producer reads `FIRE_PERIMETERS_LAYER_ID` instead — both default to `fire-perimeters`, so nothing breaks until someone repoints one of them. |
| `FIRMS_LAYER_ID` | Existing layer name/UUID for persisted detections; default `fire-detections`. |
| `SENSORS_LAYER_ID` | Existing layer name/UUID for sensor writes; default `sensors`. |
| `WATER_GAUGES_LAYER_ID` | Existing layer name/UUID for gauge observations; default `water-gauges`. |
| `WEATHER_LAYER_ID` | Existing layer name/UUID for weather observations; default `weather-observations`. |

Layer references must exist before ingestion. A missing key, source, or layer
must fail closed and preserve the last valid publication; it must not generate a
sample observation.

`CRON_SECRET` is retired: every ingestion cron container — `plantgeo-ingest-cron` (`agri-cli
ingest-all`) and, once created, each of the eight per-layer services in `docs/deployment.md`'s
"Per-layer ingestion cron services" (`plantgeo-ingest-streamflow`, `-weather`, `-fire-perimeters`,
`-firms`, `-drought`, `-ndvi`, `-sensors`, `-evacuation-zones`) — is a Python container that reaches
Postgres and Redis directly, instead of calling an HTTP route with an `x-cron-secret` header. There
is no cron ingress secret left to configure once `src/app/api/cron/ingest/route.ts` is removed, on
any of them.

**Every ingestion cron container needs a database DSN in either
`LOCAL_SOURCE_LOADER_DATABASE_URL` or `DATABASE_URL`.** This applies identically whether the
container runs `ingest-all` or a single per-layer verb: every `ingest-*` verb opens
`ingest_session()`, which calls `settings.require_local_source_loader_database_url()`. Since the
2026-08-08 role teardown that reader returns the loader variable when set and `DATABASE_URL`
otherwise — the host/port allowlist, the database-name rule, the login assertion, and the "must
not equal `DATABASE_URL`" rule are all gone. Setting both to the same string is accepted, and a
blank or whitespace-only value counts as unset. Two failures remain: having *neither* variable
set, and a DSN that is not a complete `postgresql+asyncpg://` URL (scheme, username, host, port,
and database name all present). Either raises outside any per-source isolation, so such a
container dies with an unhandled traceback on its first tick and ingests nothing.

The deployed services set `LOCAL_SOURCE_LOADER_DATABASE_URL` to the Railway **public proxy** DSN
(`postgresql+asyncpg://postgres:<password>@switchback.proxy.rlwy.net:37967/plantgeo`). Prefer the
public proxy host over `postgres.railway.internal`, and write the `postgresql+asyncpg://` scheme
explicitly rather than relying on `config.py`'s `postgres://`/`postgresql://` auto-rewrite —
neither is enforced any more, but both are what the working configuration uses. `REDIS_URL` is
still required, for the realtime publisher. See
`services/agri-data-service/src/agri_data_service/ingest/AGENTS.md`.

| Variable | Policy |
| --- | --- |
| `LOCAL_SOURCE_LOADER_DATABASE_URL` | Optional override for `agri-cli ingest-*`/`jobs-*`/`source-ingest`; defaults to `DATABASE_URL`. Nothing about it is validated since the 2026-08-08 role teardown (`20260808_0019`) — any host, port, database name, and login are accepted, as is a value identical to `DATABASE_URL`. Set it on the ingestion cron services (which is what they do today) or set `DATABASE_URL` instead; one of the two must exist. |
| `FIRE_PERIMETERS_LAYER_ID` | Optional, cron container only. Existing layer name/UUID for WFIGS perimeters; default `fire-perimeters`. This is the variable the Python `wfigs` module reads — `FIRES_LAYER_ID` below is read only by the web route `POST /api/ingest/fires`. Set both if you repoint the layer, or the two writers silently diverge. |
| `VEGETATION_LAYER_ID` | Optional, cron container only. Layer for Sentinel-2 NDVI grid samples; default `vegetation`. |
| `EVACUATION_ZONES_LAYER_ID` | Optional, cron container only. Layer for Oregon OEM evacuation areas; default `evacuation-zones`. |
| `NWS_API_USER_AGENT` | Optional, cron container only. Contact string the NWS API requires of API clients; has a default, but set a real contact before running at volume. |
| `SENSOR_STATION_STATES` | Optional, cron container only. Comma-separated US state codes the NWS station sweep covers. |
| `SENSOR_STATION_NETWORKS` | Optional, cron container only. Comma-separated NWS observation networks to accept. |
| `SENSOR_MAX_STATIONS` | Optional, cron container only. Caps stations polled per run. |
| `DROUGHT_RETAINED_RELEASES` | Optional, cron container only. How many USDM weekly releases survive the post-run prune. Raise it before running `ingest-drought-history`, or the next ordinary tick deletes the history you just walked. |

### AI, email, commerce, and administration

| Variable | Policy |
| --- | --- |
| `REGIONAL_INTELLIGENCE_MAX_CONCURRENT_PER_REPLICA` | Optional bounded AI concurrency; defaults to `4` and is capped at `16` per replica. |
| `ANTHROPIC_API_KEY` | **Required** for the regional-intelligence agent. Absent, the route returns 503 and the panel stays inert. Server-only. |
| `ANTHROPIC_MODEL` | Optional model pin; defaults to `claude-opus-5`. Set deliberately — it is the dominant cost lever for this feature. |
| `JINA_API_KEY` | Optional. Enables the agent's `search_web` tool (`s.jina.ai` + `r.jina.ai`). Absent, the agent runs offline and says so in its system prompt rather than failing. |
| `REDIS_URL` | Load-bearing for AI quota. The per-user reservation is a Redis ZSET; in production an unreachable Redis fails the request closed rather than serving unmetered. |
| `EMAIL_PROVIDER` | Optional `resend` or `sendgrid`; absence keeps delivery disabled. |
| `EMAIL_FROM` | Verified sender identity. |
| `RESEND_API_KEY`, `SENDGRID_API_KEY` | Server-only provider credential; configure only the selected provider. |
| `PLANTCOMMERCE_API_URL` | Optional server-only Aevani supplier endpoint. Use HTTPS or an explicit `.railway.internal` HTTP host; responses and outbound affiliate links are schema/size/scheme checked. Do not confuse it with the Aevani database. |
| `PLANTCOMMERCE_WEBHOOK_URL`, `PLANTCOMMERCE_WEBHOOK_SECRET` | Optional signed outbound webhook configuration. |
| `ADMIN_API_TOKEN` | Dedicated admin-route bearer token. Never reuse an ingestion or publication token. |
| `ALLOW_LEGACY_BCRYPT_API_KEYS` | Temporary one-way API-key migration gate; default false and disable after reissue/upgrade. |

### Legacy worker quarantine

```dotenv
ENABLE_LEGACY_BULLMQ_JOBS=false
SERVICE_ROLE=web
```

Both values are deliberate. Legacy import-time workers must not start inside a
web replica. Redis-backed job modules are not production-approved durable
execution and there is no Railway training/forecast worker in phase one.

## R2/operator upload settings

These values belong only in the operator/CI context that uploads immutable
artifacts; the browser does not need them:

| Variable | Purpose |
| --- | --- |
| `R2_BUCKET` | Target bucket name. |
| `R2_ENDPOINT` | Account-specific S3-compatible endpoint. |
| `R2_ACCESS_KEY_ID` | Least-privilege write identity. |
| `R2_SECRET_ACCESS_KEY` | Secret for that identity. |

Prefer separate read/public and write identities. Record object checksum,
source/model version, license, and publication metadata in PostgreSQL.

## Python data service and local runner

Local compatibility uses the shared async SQLAlchemy URL below. This profile
mounts the legacy combined surface and deliberately cannot pass rollout
readiness:

```dotenv
SERVICE_PROFILE=combined_local
DATABASE_URL=postgresql+asyncpg://geo:<password>@localhost:5432/plantgeo
DB_POOL_MIN=2
DB_POOL_MAX=5
SANIC_HOST=0.0.0.0
SANIC_PORT=8000
SANIC_DEBUG=true
CORS_ORIGINS=http://localhost:3001
```

Production runs two separately configured instances from the same image. The
private receiver/writer mounts only the authenticated publication and historical
promotion receivers:

```dotenv
SERVICE_PROFILE=receiver_writer
RECEIVER_WRITER_DATABASE_URL=postgresql+asyncpg://<receiver-writer>:<password>@<private-host>:5432/plantgeo
LOCAL_PUBLICATION_RECEIVER_ENABLED=true
LOCAL_PUBLISH_TOKEN=<dedicated strong token>
LOCAL_PUBLISH_ACTOR=plantgeo-local-forecast-publisher
```

The published reader mounts only the typed forecast read route and receives no
receiver credentials:

```dotenv
SERVICE_PROFILE=published_reader
PUBLISHED_READER_DATABASE_URL=postgresql+asyncpg://<published-reader>:<password>@<private-host>:5432/plantgeo
```

The two DSNs must authenticate different least-privilege login roles. Neither
production profile accepts `DATABASE_URL` as a fallback. Disable debug in
production. An explicitly approved migration process uses the separate
synchronous administrative identity below; the Alembic environment reads this
value directly and does not fall back to any runtime DSN:

```dotenv
DATABASE_URL_SYNC=postgresql+psycopg2://<migration-role>:<password>@<host>/<database>
```

Do not inject `DATABASE_URL_SYNC` into the long-lived API service.

Phase-one compute policy is fail-closed:

```dotenv
EXECUTION_BACKEND=local
CELERY_DISPATCH_ENABLED=false
CLOUD_TRAINING_ENABLED=false
LOCAL_EXECUTION_ROOT=.agri-local-runs
```

The 30-day Monte Carlo iteration commands use `DATABASE_URL` unless this optional
override is set. Set it to aim them at a disposable clone instead; no host, port,
database name, or login is asserted:

```dotenv
FORECAST_ITERATION_DATABASE_URL=postgresql+asyncpg://plantgeo_owner:<password>@127.0.0.1:5442/plantgeo_forecast_test
```

Iteration rows are evaluation/ML-signal evidence only. This credential does not
authorize a forecast publication, a Railway mutation, or a scheduled job.

The operator machine sets the URL and shared credential:

```dotenv
LOCAL_PUBLISH_API_URL=https://<data-service-domain>/api/v1/local-execution
LOCAL_PUBLISH_TOKEN=<dedicated strong token>
```

The receiver/writer service receives the same token, explicitly enables the
receiver, and binds the credential to a server-controlled audit identity. It
does not receive the client API URL:

```dotenv
LOCAL_PUBLICATION_RECEIVER_ENABLED=true
LOCAL_PUBLISH_TOKEN=<same dedicated strong token>
LOCAL_PUBLISH_ACTOR=plantgeo-local-forecast-publisher
```

The workstation cannot supply or override the actor. Non-loopback publication
requires HTTPS. The token must contain at least 32 diverse non-whitespace
characters and must not appear in manifests or logs. `/ready` reports only
boolean checks; it never returns the token, actor, DSN, role name, or database
host.

Bounded defaults:

```dotenv
LOCAL_PUBLISH_MAX_ARTIFACT_BYTES=5000000
LOCAL_PUBLISH_MAX_MANIFEST_BYTES=512000
LOCAL_PUBLISH_MAX_VALIDATION_BYTES=256000
LOCAL_PUBLISH_MAX_OUTPUTS=256
LOCAL_PUBLISH_MAX_RUN_ARTIFACT_BYTES=100000000
LOCAL_PUBLISH_MAX_RUN_VALIDATION_BYTES=10000000
LOCAL_PUBLISH_REQUEST_OVERHEAD_BYTES=64000
LOCAL_PUBLISH_RETRY_ATTEMPTS=5
LOCAL_PUBLISH_RETRY_BASE_SECONDS=0.5
```

Raising any limit requires request-memory, database-size, transaction-duration,
and abuse testing. Large artifacts should move to immutable object storage with
a checksum-addressed database reference rather than being forced through inline
publication.

## Railway policy

Production private references use exact service names. The web application
continues to use its existing database reference, while the two data-service
profiles receive separately sealed least-privilege DSNs:

```dotenv
DATABASE_URL=${{Plantgeo.DATABASE_URL}}
REDIS_URL=${{plantgeo-Redis.REDIS_URL}}
MARTIN_URL=http://${{plantgeo-martin.RAILWAY_PRIVATE_DOMAIN}}:3000
RECEIVER_WRITER_DATABASE_URL=<sealed receiver/writer DSN>
PUBLISHED_READER_DATABASE_URL=<sealed published-reader DSN>
```

The first reference remains on `Plantgeo` until the replacement cutover is
approved. Never reference `${{Aevani-Postgress.DATABASE_URL}}`. Do not print a
resolved reference in a build or deploy log. See
[Railway Operations](./deployment.md) for the full service allowlist, the
single-path deploy sequence, and the cutover gate.
