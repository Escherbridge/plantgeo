# PlantGeo — Railway Pro Service Configuration

This document describes all services deployed on Railway Pro and the required environment variables for each.

## Services

### 1. Next.js Web Service (`web`)
The main application server built from the root `Dockerfile`.

- **Builder**: Dockerfile
- **Start command**: `node server.js`
- **Health check**: `GET /api/health`
- **Port**: 3000

### 2. PostgreSQL + PostGIS Plugin (`postgres`)
Managed PostgreSQL instance with PostGIS and TimescaleDB extensions.

- **Plugin**: Railway PostgreSQL plugin
- **Image**: `timescale/timescaledb-ha:pg16`
- **Volume**: Persistent disk mounted at `/home/postgres/pgdata/data`
- **Init scripts**: Upload `infra/db/init/` scripts as start-up SQL

### 3. Redis Plugin (`redis`)
Managed Redis instance used for caching and pub/sub.

- **Plugin**: Railway Redis plugin
- **Version**: Redis 7
- **Config**: `maxmemory 512mb`, `maxmemory-policy allkeys-lru`, AOF enabled

### 4. Martin Tile Server (`martin`)
Rust-based vector tile server that reads from PostGIS.

- **Image**: `ghcr.io/maplibre/martin:latest`
- **Command**: `--config /config/martin.yaml`
- **Port**: 3000 (mapped internally)
- **Config**: Mount `infra/martin/martin.yaml` as `/config/martin.yaml`
- **Depends on**: `postgres` service healthy

### 5. (Optional) Valhalla Routing (`valhalla`)
Multi-modal routing engine. Deploy only after building tiles.

- **Image**: `ghcr.io/gis-ops/docker-valhalla/valhalla:latest`
- **Port**: 8002
- **Volume**: Persistent disk for pre-built routing tiles

### 6. (Optional) Photon Geocoder (`photon`)
Elasticsearch-backed geocoding service using OpenStreetMap data.

- **Image**: `komoot/photon:latest`
- **Port**: 2322
- **Volume**: Persistent disk for geocoding index

---

## Required Environment Variables

Set these in the Railway dashboard under each service's "Variables" tab.

### `web` service

| Variable | Description | Example |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string (injected by Railway plugin) | `postgresql://geo:pass@postgres.railway.internal/plantgeo` |
| `REDIS_URL` | Redis connection string (injected by Railway plugin) | `redis://redis.railway.internal:6379` |
| `NEXT_PUBLIC_PMTILES_URL` | Public URL of the PMTiles file on Cloudflare R2 | `https://tiles.example.com/basemap.pmtiles` |
| `NEXT_PUBLIC_TERRAIN_URL` | Terrain tile URL template | `https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png` |
| `MARTIN_URL` | Internal URL of the Martin tile service | `http://martin.railway.internal:3000` |
| `VALHALLA_URL` | Internal URL of the Valhalla routing service | `http://valhalla.railway.internal:8002` |
| `PHOTON_URL` | Internal URL of the Photon geocoding service | `http://photon.railway.internal:2322` |
| `NEXTAUTH_SECRET` | Random 32-byte secret for NextAuth.js session signing | *(generate with `openssl rand -base64 32`)* |
| `NEXTAUTH_URL` | Canonical URL of the deployed app | `https://plantgeo.up.railway.app` |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID | *(from Google Cloud Console)* |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret | *(from Google Cloud Console)* |
| `GITHUB_CLIENT_ID` | GitHub OAuth App client ID | *(from GitHub Developer Settings)* |
| `GITHUB_CLIENT_SECRET` | GitHub OAuth App client secret | *(from GitHub Developer Settings)* |
| `MAPILLARY_ACCESS_TOKEN` | Mapillary API access token | *(from Mapillary dashboard)* |
| `NASA_FIRMS_KEY` | NASA FIRMS fire data API key | *(from firms.modaps.eosdis.nasa.gov)* |
| `R2_BUCKET` | Cloudflare R2 bucket name | `plantgeo-tiles` |
| `R2_ENDPOINT` | Cloudflare R2 S3-compatible endpoint | `https://<account-id>.r2.cloudflarestorage.com` |
| `R2_ACCESS_KEY_ID` | R2 API token access key ID | *(from Cloudflare dashboard)* |
| `R2_SECRET_ACCESS_KEY` | R2 API token secret | *(from Cloudflare dashboard)* |

### `martin` service

| Variable | Description |
|---|---|
| `DATABASE_URL` | Same PostgreSQL connection string as `web` |

---

## Internal Networking

Railway Pro services on the same project communicate over a private network using the hostname format `<service-name>.railway.internal`. No public ports are needed for internal services (Martin, Redis, etc.).

## Deployment Order

1. `postgres` — must be healthy before other services start
2. `redis` — can start in parallel with `postgres`
3. `martin` — depends on `postgres`
4. `web` — depends on `postgres`, `redis`, and `martin`
5. `valhalla`, `photon` — optional, independent
