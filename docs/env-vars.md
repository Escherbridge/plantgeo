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
| `REDIS_URL` | server | `${{plantgeo-Redis.REDIS_URL}}` in Railway. Cache/pub-sub only, never the durable job ledger. |
| `NEXTAUTH_SECRET` | server | Unique high-entropy production secret. Rotating it invalidates sessions. |
| `NEXTAUTH_URL` | server | Canonical application origin, `http://localhost:3001` in development. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | server | Optional pair; configure both or omit the provider. |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | server | Optional pair; configure both or omit the provider. |

Use separate least-privilege database identities for the web application,
Python publisher, migration workflow, and Martin. Runtime services must not use
the database owner.

### Public map configuration

| Variable | Policy |
| --- | --- |
| `NEXT_PUBLIC_PMTILES_URL` | Public, immutable basemap PMTiles URL with HTTP Range support. Mirror to a reviewed R2/CDN origin for production. |
| `NEXT_PUBLIC_TERRAIN_URL` | Optional reviewed terrain URL template containing `{z}`, `{x}`, and `{y}`. |
| `NEXT_PUBLIC_DYNAMIC_TILES_URL` | Public/custom HTTPS Martin origin. Leave unset until Martin passes its database, role, catalog, tile, CORS, and rate-limit gates. |
| `NEXT_PUBLIC_APP_URL` | Public canonical origin used in links and email, `http://localhost:3001` in development. |

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
| `TILE_CORS_ORIGIN` | Martin | Exact web origin; CORS is not authentication. |
| `VALHALLA_URL` | server | Optional/deferred routing service; absence must produce an unavailable response. |
| `VALHALLA_PBF_URL` | operator | Optional graph-build input, not a web runtime setting. Pin the release/checksum before use. |
| `PHOTON_URL` | server | Optional/deferred geocoder. Production should use an owned or contractually approved service, not an undocumented public instance. |

### Ingestion and provider credentials

| Variable | Policy |
| --- | --- |
| `INGEST_SECRET` | Dedicated bearer secret for authenticated ingestion routes. Required in production for those routes. |
| `CRON_SECRET` | Dedicated bearer secret for the bounded cron ingress route. It does not enable a forecast/training worker. |
| `INGEST_BBOX` | Required for scheduled FIRMS/water acquisition; `west,south,east,north`, capped by code. Start with one reviewed region. |
| `NASA_FIRMS_KEY` | Server-only; required only when running the FIRMS acquisition path. |
| `MAPILLARY_ACCESS_TOKEN` | Server-only imagery proxy token. Mapillary remains a licensed imagery exception, not a warehouse bypass. |
| `FIRES_LAYER_ID` | Existing layer name/UUID for submitted fire perimeters; default `fire-perimeters`. |
| `FIRMS_LAYER_ID` | Existing layer name/UUID for persisted detections; default `fire-detections`. |
| `SENSORS_LAYER_ID` | Existing layer name/UUID for sensor writes; default `sensors`. |
| `WATER_GAUGES_LAYER_ID` | Existing layer name/UUID for gauge observations; default `water-gauges`. |
| `WEATHER_LAYER_ID` | Existing layer name/UUID for weather observations; default `weather-observations`. |

Layer references must exist before ingestion. A missing key, source, or layer
must fail closed and preserve the last valid publication; it must not generate a
sample observation.

### AI, email, commerce, and administration

| Variable | Policy |
| --- | --- |
| `REGIONAL_INTELLIGENCE_ENABLED` | Defaults false. Keep false until all context is publication-backed and model/evidence review passes. |
| `REGIONAL_INTELLIGENCE_MAX_CONCURRENT_PER_REPLICA` | Optional bounded AI concurrency; defaults to `2` and is capped at `16` per replica. |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | Server-only and relevant only when regional intelligence is enabled. Pin a supported model deliberately. |
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

The 30-day Monte Carlo iteration commands require an explicit loopback-only
evaluation writer. It never falls back to the API DSN and may target a named
`plantgeo*` disposable clone:

```dotenv
FORECAST_ITERATION_DATABASE_URL=postgresql+asyncpg://plantgeo_local_developer:<password>@127.0.0.1:5442/plantgeo_forecast_test
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
resolved reference in CI. See [Railway Operations](./deployment.md) for the full
service allowlist and cutover gate.
