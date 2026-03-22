# Environment Variables Reference

Complete reference for all environment variables used in PlantGeo, organized by service and deployment environment.

## Overview

PlantGeo uses environment variables for:
- Service configuration (database, cache, tile servers)
- API credentials (external services)
- Authentication secrets
- Feature flags and tuning parameters

**Safety Rule:** Never commit `.env.local` or `.env.production` files. Use `.env.example` as template.

## Database & Cache

### DATABASE_URL

PostgreSQL connection string.

```
# Format
postgresql://[user]:[password]@[host]:[port]/[database]

# Local development
DATABASE_URL=postgresql://geo:geopass@localhost:5432/plantgeo

# Railway production
DATABASE_URL=postgresql://postgres:PASSWORD@postgres.PROJECT.railway.internal:5432/railway

# With SSL
DATABASE_URL=postgresql://user:pass@host:5432/db?sslmode=require

# With connection pooling
DATABASE_URL=postgresql://user:pass@host:5432/db?sslmode=require&pool=10
```

**Required:** Yes (all environments)
**Type:** String
**How to get:**
- Local: Set in docker-compose
- Railway: Auto-generated, shown in Variables tab

---

### POSTGRES_PASSWORD

PostgreSQL superuser password (local Docker only).

```
POSTGRES_PASSWORD=geopass
```

**Required:** Yes (development only)
**Type:** String
**How to get:** Generate random password, store in docker-compose.yml

---

### REDIS_URL

Redis connection string for caching and pub/sub.

```
# Format
redis://[user]:[password]@[host]:[port]/[db]

# Local development
REDIS_URL=redis://localhost:6379

# Railway production
REDIS_URL=redis://redis.PROJECT.railway.internal:6379

# With authentication
REDIS_URL=redis://user:password@host:6379
```

**Required:** Yes (all environments)
**Type:** String
**How to get:**
- Local: Redis runs in Docker
- Railway: Auto-generated, shown in Redis service variables

---

## Map Tile Serving

### MARTIN_URL

URL of Martin tile server (Rust MVT server).

```
# Local development
MARTIN_URL=http://localhost:3100

# Railway production
MARTIN_URL=http://martin.PROJECT.railway.internal:3100
```

**Required:** Yes (server-side only)
**Type:** String
**Usage:** Fetching dynamic tiles from PostGIS

---

### NEXT_PUBLIC_MAP_STYLE_URL

Public URL of Martin style.json for MapLibre GL JS.

```
# Local development
NEXT_PUBLIC_MAP_STYLE_URL=http://localhost:3100/style.json

# Railway production
NEXT_PUBLIC_MAP_STYLE_URL=http://martin.PROJECT.railway.internal:3100/style.json

# Via proxy
NEXT_PUBLIC_MAP_STYLE_URL=https://tiles.plantgeo.app/style.json
```

**Required:** Yes (client and server)
**Type:** String (URL)
**Note:** Prefix `NEXT_PUBLIC_` makes it available in browser

---

### NEXT_PUBLIC_PMTILES_URL

Public URL of PMTiles basemap archive (Cloudflare R2).

```
# Cloudflare R2
NEXT_PUBLIC_PMTILES_URL=https://plantgeo-tiles.r2.dev/basemap.pmtiles

# Custom domain (via Workers)
NEXT_PUBLIC_PMTILES_URL=https://tiles.plantgeo.app/basemap.pmtiles

# Local development (if self-hosting)
NEXT_PUBLIC_PMTILES_URL=http://localhost:3100/pmtiles/basemap.pmtiles
```

**Required:** Yes (client only)
**Type:** String (URL)
**How to get:**
1. Upload to Cloudflare R2
2. Get public URL from R2 bucket settings
3. Or create Cloudflare Worker proxy

---

### NEXT_PUBLIC_TERRAIN_URL

URL of terrain/elevation tiles (DEM - Digital Elevation Model).

```
# AWS S3 (Terrarium format)
NEXT_PUBLIC_TERRAIN_URL=https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png

# Alternative: Nextzen elevation tiles
NEXT_PUBLIC_TERRAIN_URL=https://tile.nextzen.org/tileset/elevation/v1/512/{z}/{x}/{y}.png?api_key=YOUR_KEY

# Local Martin server
NEXT_PUBLIC_TERRAIN_URL=http://localhost:3100/data/terrain/{z}/{x}/{y}.png
```

**Required:** No (optional, enables 3D terrain)
**Type:** String (URL with placeholders `{z}`, `{x}`, `{y}`)
**Note:** Terrarium format: RGB pixels encode elevation as -32768 + ((R*256 + G + B/256))

---

## Routing

### VALHALLA_URL

URL of Valhalla routing engine.

```
# Local development
VALHALLA_URL=http://localhost:8002

# Railway production
VALHALLA_URL=http://valhalla.PROJECT.railway.internal:8002
```

**Required:** Yes (server-side only)
**Type:** String
**Usage:** Route calculations, isochrones, distance matrices

---

### VALHALLA_PBF_URL

OpenStreetMap PBF data source for Valhalla (used on initial setup).

```
# Geofabrik regional extracts
VALHALLA_PBF_URL=https://download.geofabrik.de/north-america/us/california-latest.osm.pbf

# Planet
VALHALLA_PBF_URL=https://planet.openstreetmap.org/pbf/planet-latest.osm.pbf

# Artisanal Builds
VALHALLA_PBF_URL=https://builds.valhalla.mapzen.com/valhalla_tiles.tar
```

**Required:** Only on first Valhalla setup
**Type:** String (HTTP URL to .osm.pbf or .tar)
**How to get:** List at https://download.geofabrik.de/

---

## Geocoding

### PHOTON_URL

URL of Photon geocoding server (Nominatim wrapper).

```
# Local development
PHOTON_URL=http://localhost:2322

# Public instance
PHOTON_URL=https://photon.komoot.io
```

**Required:** Yes (server-side only)
**Type:** String
**Note:** Photon is built on Nominatim data (OpenStreetMap)

---

## Cloud Storage (Cloudflare R2)

### R2_BUCKET

R2 bucket name for storing PMTiles and user uploads.

```
R2_BUCKET=plantgeo-tiles
```

**Required:** Yes (server-side, for uploads)
**Type:** String
**How to get:**
1. Go to Cloudflare Dashboard → R2
2. Create bucket
3. Copy bucket name

---

### R2_ENDPOINT

R2 API endpoint (S3-compatible).

```
# Format
https://<ACCOUNT_ID>.r2.cloudflarestorage.com

# Example
R2_ENDPOINT=https://abc123def456.r2.cloudflarestorage.com
```

**Required:** Yes (server-side, for uploads)
**Type:** String
**How to get:**
1. Cloudflare Dashboard → R2 → API Tokens
2. Copy "S3 API Endpoint"
3. Or construct from Account ID

---

### R2_ACCESS_KEY_ID

R2 API access key ID (like AWS Access Key).

```
R2_ACCESS_KEY_ID=abc123def456ghi789
```

**Required:** Yes (server-side, for uploads)
**Type:** String
**How to get:**
1. Cloudflare Dashboard → R2 → API Tokens
2. Create API Token
3. Copy Access Key ID (shown only once)

---

### R2_SECRET_ACCESS_KEY

R2 API secret key (like AWS Secret Access Key).

```
R2_SECRET_ACCESS_KEY=very_secret_key_abc123_do_not_share
```

**Required:** Yes (server-side, for uploads)
**Type:** String
**Security:** Never commit, rotate periodically
**How to get:** Cloudflare Dashboard → R2 → API Tokens

---

## Authentication (NextAuth.js)

### NEXTAUTH_SECRET

NextAuth.js session encryption secret.

```bash
# Generate using OpenSSL
openssl rand -base64 32
# Output: AbCdEfGhIjKlMnOpQrStUvWxYz1234567890ABCD=

# Or with Node
node -e "console.log(require('crypto').randomBytes(32).toString('base64'))"
```

```
NEXTAUTH_SECRET=AbCdEfGhIjKlMnOpQrStUvWxYz1234567890ABCD=
```

**Required:** Yes (all environments)
**Type:** String (base64-encoded)
**Security:** Never share, regenerate on security incidents
**Note:** Must be ≥32 bytes when decoded

---

### NEXTAUTH_URL

NextAuth.js callback URL (where app is hosted).

```
# Local development
NEXTAUTH_URL=http://localhost:3000

# Production
NEXTAUTH_URL=https://plantgeo.example.com

# Railway
NEXTAUTH_URL=https://plantgeo-production.up.railway.app
```

**Required:** Yes (all environments)
**Type:** String (HTTP/HTTPS URL)
**Must match:** OAuth provider redirect URI configuration

---

## OAuth Providers

### Google OAuth

```
GOOGLE_CLIENT_ID=123456789-abc.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-AbCdEfGhIjKlMnOpQr
```

**Required:** Optional (for Google login)
**How to get:**
1. Go to https://console.cloud.google.com
2. Create project
3. Enable Google+ API
4. Create OAuth 2.0 credential (Web application)
5. Add redirect URI: `{NEXTAUTH_URL}/api/auth/callback/google`
6. Copy Client ID and Secret

---

### GitHub OAuth

```
GITHUB_CLIENT_ID=Iv1.abc123def456
GITHUB_CLIENT_SECRET=abcdef1234567890fedcba0987654321
```

**Required:** Optional (for GitHub login)
**How to get:**
1. Go to GitHub Settings → Developer settings → OAuth Apps
2. New OAuth App
3. Authorization callback URL: `{NEXTAUTH_URL}/api/auth/callback/github`
4. Copy Client ID and Secret

---

## External APIs

### NASA FIRMS

```
NASA_FIRMS_KEY=abc123def456ghi789jkl
```

**Required:** Yes (for fire detections)
**Type:** String (API key)
**How to get:**
1. Go to https://earthdata.nasa.gov/
2. Login or create account
3. FIRMS → Request Data Access
4. Generate API token
5. Copy key

**Rate limit:** 1 request/minute

---

### Mapillary Imagery

```
MAPILLARY_ACCESS_TOKEN=MLY|123456|abc123def456
```

**Required:** Optional (for street view imagery)
**Type:** String (API token)
**How to get:**
1. Go to https://www.mapillary.com/developer
2. Create application
3. Generate access token
4. Copy token

---

### Anthropic Claude API

```
ANTHROPIC_API_KEY=sk-ant-v1-abc123def456...
ANTHROPIC_MODEL=claude-haiku-4-5-20251001
```

**Required:** Yes (for AI regional intelligence)
**Type:** String
**How to get:**
1. Go to https://console.anthropic.com
2. Create API key
3. Copy key
4. Choose model from available options

**Models available:**
- `claude-haiku-4-5-20251001` (fastest, cheapest)
- `claude-opus-4-1` (most capable)
- Latest versions at https://docs.anthropic.com/claude/reference/models-overview

---

### Additional Weather/NOAA APIs

```
# OpenWeatherMap (alternative to NOAA)
OPENWEATHER_API_KEY=abc123def456

# NOAA (public, no key needed for basic access)
# Built into services, no env var required
```

**Required:** No (NOAA is public)
**How to get:** OpenWeatherMap signup at https://openweathermap.org/api

---

## PlantCommerce Integration

### PLANTCOMMERCE_API_URL

Base URL of PlantCommerce API.

```
PLANTCOMMERCE_API_URL=https://plantcommerce.example.com
```

**Required:** Optional (only if using PlantCommerce integration)
**Type:** String (HTTPS URL)

---

### PLANTCOMMERCE_WEBHOOK_SECRET

HMAC-SHA256 secret for verifying webhook signatures from PlantCommerce.

```bash
# Generate using OpenSSL
openssl rand -32 | base64
```

```
PLANTCOMMERCE_WEBHOOK_SECRET=abc123def456ghi789jkl...
```

**Required:** Optional (only if using PlantCommerce webhooks)
**Type:** String (base64-encoded)
**Security:** Share with PlantCommerce team for webhook configuration

---

### PLANTCOMMERCE_WEBHOOK_URL

Public URL where PlantCommerce sends webhooks.

```
PLANTCOMMERCE_WEBHOOK_URL=https://plantgeo.example.com/api/webhooks/plantcommerce
```

**Required:** Optional
**Type:** String (HTTPS URL)
**Note:** Tell PlantCommerce team this URL

---

## Admin & Internal

### ADMIN_API_TOKEN

Token for admin-only API endpoints.

```bash
# Generate using OpenSSL
openssl rand -32 | base64
```

```
ADMIN_API_TOKEN=abc123def456ghi789jkl...
```

**Required:** Yes (server-side, for admin endpoints)
**Type:** String (base64-encoded)
**Security:** Very sensitive, never expose in logs

---

## Next.js Configuration

### NEXT_TELEMETRY_DISABLED

Disable Next.js telemetry (for privacy).

```
NEXT_TELEMETRY_DISABLED=1
```

**Required:** No
**Type:** Boolean (1 or 0)
**Default:** 0 (telemetry enabled)

---

### NODE_ENV

Node.js environment.

```
# Development
NODE_ENV=development

# Production
NODE_ENV=production
```

**Required:** Yes
**Type:** Enum: "development" | "production" | "test"
**Set by:** Railway or docker-compose automatically

---

## Feature Flags

### FEATURE_FLAGS_* (Optional)

Enable/disable experimental features.

```
# Example: enable wildfire enhancements
FEATURE_WILDFIRE_ANALYSIS=true

# Example: enable AI regional intelligence
FEATURE_AI_INTELLIGENCE=true

# Example: beta community features
FEATURE_BETA_COMMUNITY=false
```

**Required:** No
**Type:** Boolean
**Usage:** Check in code: `if (process.env.FEATURE_X === 'true')`

---

## Tuning Parameters

### CACHE_TTL_*

Redis cache time-to-live in seconds.

```
# Fire detections cache (30 min)
CACHE_TTL_FIRE=1800

# Weather data cache (1 hour)
CACHE_TTL_WEATHER=3600

# Layer data cache (24 hours)
CACHE_TTL_LAYERS=86400
```

**Required:** No
**Type:** Integer (seconds)
**Default:** Service-specific defaults used if not set

---

### BACKGROUND_JOB_INTERVAL_*

Milliseconds between background job runs.

```
# Alert dispatcher (5 minutes)
BACKGROUND_JOB_INTERVAL_ALERTS=300000

# Water refresh (1 hour)
BACKGROUND_JOB_INTERVAL_WATER=3600000

# Priority zone refresh (24 hours)
BACKGROUND_JOB_INTERVAL_PRIORITY_ZONES=86400000
```

**Required:** No
**Type:** Integer (milliseconds)

---

### RATE_LIMIT_*

Rate limiting configuration.

```
# API requests per hour per key
RATE_LIMIT_API_DEFAULT=1000

# Geocoding per hour
RATE_LIMIT_GEOCODE=5000

# Routing per hour
RATE_LIMIT_ROUTING=1000
```

**Required:** No
**Type:** Integer
**Default:** 1000 per hour

---

## Environment-Specific Templates

### Local Development (.env.local)

```bash
# Database
DATABASE_URL=postgresql://geo:geopass@localhost:5432/plantgeo
POSTGRES_PASSWORD=geopass

# Cache
REDIS_URL=redis://localhost:6379

# Tile & Routing
MARTIN_URL=http://localhost:3100
NEXT_PUBLIC_MAP_STYLE_URL=http://localhost:3100/style.json
VALHALLA_URL=http://localhost:8002
PHOTON_URL=http://localhost:2322

# Tiles
NEXT_PUBLIC_PMTILES_URL=http://localhost:3100/pmtiles/basemap.pmtiles
NEXT_PUBLIC_TERRAIN_URL=https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png

# Auth
NEXTAUTH_SECRET=dev-secret-not-secure
NEXTAUTH_URL=http://localhost:3000

# OAuth (optional)
# GOOGLE_CLIENT_ID=
# GOOGLE_CLIENT_SECRET=
# GITHUB_CLIENT_ID=
# GITHUB_CLIENT_SECRET=

# External APIs
# NASA_FIRMS_KEY=
# MAPILLARY_ACCESS_TOKEN=
# ANTHROPIC_API_KEY=

# R2 (optional for uploads)
# R2_BUCKET=
# R2_ENDPOINT=
# R2_ACCESS_KEY_ID=
# R2_SECRET_ACCESS_KEY=
```

### Railway Production (.env.production)

Set via Railway Dashboard → Variables tab:

```
# Auto-generated by Railway
DATABASE_URL=postgresql://postgres:PASSWORD@postgres.PROJECT.railway.internal:5432/railway
REDIS_URL=redis://redis.PROJECT.railway.internal:6379

# Internal URLs
MARTIN_URL=http://martin.PROJECT.railway.internal:3100
VALHALLA_URL=http://valhalla.PROJECT.railway.internal:8002
PHOTON_URL=http://photon.PROJECT.railway.internal:2322

# Public URLs
NEXT_PUBLIC_MAP_STYLE_URL=https://tiles.plantgeo.app/style.json
NEXT_PUBLIC_PMTILES_URL=https://plantgeo-tiles.r2.dev/basemap.pmtiles
NEXT_PUBLIC_TERRAIN_URL=https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png

# Auth
NEXTAUTH_SECRET=(your-generated-secret)
NEXTAUTH_URL=https://plantgeo.example.com

# OAuth
GOOGLE_CLIENT_ID=(your-id)
GOOGLE_CLIENT_SECRET=(your-secret)
GITHUB_CLIENT_ID=(your-id)
GITHUB_CLIENT_SECRET=(your-secret)

# External APIs
NASA_FIRMS_KEY=(your-key)
MAPILLARY_ACCESS_TOKEN=(your-token)
ANTHROPIC_API_KEY=(your-key)

# Cloudflare R2
R2_BUCKET=plantgeo-tiles
R2_ENDPOINT=https://account-id.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=(your-key)
R2_SECRET_ACCESS_KEY=(your-secret)

# PlantCommerce (if integrated)
PLANTCOMMERCE_API_URL=https://plantcommerce.example.com
PLANTCOMMERCE_WEBHOOK_SECRET=(your-secret)

# Admin
ADMIN_API_TOKEN=(your-token)

# Tuning
NODE_ENV=production
NEXT_TELEMETRY_DISABLED=1
```

## Validation & Testing

### Validate Environment

```bash
# Create validation script
cat > scripts/validate-env.js << 'EOF'
const required = [
  'DATABASE_URL',
  'REDIS_URL',
  'MARTIN_URL',
  'VALHALLA_URL',
  'NEXTAUTH_SECRET',
  'NEXTAUTH_URL',
];

const missing = required.filter(key => !process.env[key]);

if (missing.length > 0) {
  console.error('Missing required env vars:', missing);
  process.exit(1);
}

console.log('✓ All required env vars present');
EOF

node scripts/validate-env.js
```

### Test Connections

```bash
# Test database
psql $DATABASE_URL -c "SELECT 1"

# Test Redis
redis-cli -u $REDIS_URL ping

# Test Martin
curl $MARTIN_URL/health

# Test Valhalla
curl $VALHALLA_URL/status
```

## Security Checklist

- [ ] `.env.local` in `.gitignore`
- [ ] No secrets in code or logs
- [ ] NEXTAUTH_SECRET ≥32 bytes
- [ ] OAuth secrets kept private
- [ ] API keys rotated regularly
- [ ] HTTPS enforced in production
- [ ] Rate limiting configured
- [ ] Admin token secure and unique
- [ ] R2 credentials scoped to least privilege
- [ ] Database passwords strong and unique
