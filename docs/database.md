# PlantGeo Database Schema

Complete documentation of the PostgreSQL + PostGIS database schema used in PlantGeo.

## Overview

- **Database**: PostgreSQL 16
- **Extensions**: PostGIS 3.4, TimescaleDB
- **ORM**: Drizzle ORM (TypeScript)
- **Schemas**: `public`, `geo`, `tracking`

## Connection

```bash
# Local development
psql postgresql://geo:geopass@localhost:5432/plantgeo

# Railway production
psql "$DATABASE_URL"
```

## Database Commands

```bash
# Generate migrations
npm run db:generate

# Run pending migrations
npm run db:migrate

# Push schema changes (sync mode, not recommended for production)
npm run db:push
```

## Schema Overview

```
PUBLIC SCHEMA (authentication & teams)
├── users
├── sessions
├── accounts
├── verificationTokens
├── teams
├── teamMembers
├── apiKeys
└── environmentalAlerts, waterGauges, droughtData, priorityZones

GEO SCHEMA (geospatial data)
├── layers
├── features
├── fireDetections
└── (other environmental data)

TRACKING SCHEMA (asset tracking)
├── assets
├── positions (TimescaleDB hypertable)
├── geofences
└── alerts
```

## Tables

### PUBLIC SCHEMA

#### users

User accounts and authentication.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PRIMARY KEY, DEFAULT random | User identifier |
| `name` | text | | Full name (optional) |
| `email` | text | UNIQUE, NOT NULL | Login email |
| `passwordHash` | text | | Bcrypt hash (optional, if using email auth) |
| `platformRole` | varchar(20) | DEFAULT 'contributor' | contributor, expert, admin |
| `verified` | boolean | DEFAULT false | Email verification status |
| `createdAt` | timestamp | DEFAULT now() | Account creation time |

**Indexes:**
- Primary key on `id`
- Unique index on `email`

**Sample query:**
```sql
SELECT * FROM users WHERE email = 'user@example.com';
```

---

#### sessions

Authentication sessions (NextAuth.js).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `sessionToken` | text | PRIMARY KEY | Unique session ID |
| `userId` | UUID | NOT NULL, FK → users | Owner of session |
| `expires` | timestamp | NOT NULL | Session expiration time |

**Relationships:**
- `userId` references `users.id` (ON DELETE CASCADE)

**Cleanup:** Expired sessions automatically removed by NextAuth.js

---

#### accounts

OAuth provider accounts (NextAuth.js).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `userId` | UUID | NOT NULL, FK → users | User account |
| `type` | text | NOT NULL | "oauth" or "credentials" |
| `provider` | text | NOT NULL | "google", "github", etc |
| `providerAccountId` | text | NOT NULL | Provider's user ID |
| `refresh_token` | text | | OAuth refresh token |
| `access_token` | text | | OAuth access token |
| `expires_at` | integer | | Token expiration (Unix time) |
| `token_type` | text | | "Bearer", etc |
| `scope` | text | | OAuth scopes granted |
| `id_token` | text | | OpenID Connect ID token |
| `session_state` | text | | OAuth state parameter |

**Primary Key:** `(provider, providerAccountId)`

**Relationships:**
- `userId` references `users.id` (ON DELETE CASCADE)

---

#### verificationTokens

Email verification and password reset tokens.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `identifier` | text | NOT NULL | Email or user ID |
| `token` | text | NOT NULL | Random token hash |
| `expires` | timestamp | NOT NULL | Token expiration |

**Primary Key:** `(identifier, token)`

**Cleanup:** Expired tokens should be cleaned up by a cron job

---

#### teams

Organization/team records.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PRIMARY KEY, DEFAULT random | Team identifier |
| `name` | text | NOT NULL | Team display name |
| `slug` | varchar(100) | UNIQUE | URL-friendly name (optional) |
| `description` | text | | Team bio/mission |
| `orgType` | varchar(50) | | nonprofit, cooperative, business, individual, government |
| `specialties` | jsonb | DEFAULT '[]' | Array of specializations |
| `website` | text | | Team website URL |
| `serviceArea` | jsonb | DEFAULT '{}' | GeoJSON of service coverage |
| `isVerified` | boolean | DEFAULT false | Admin verification status |
| `verifiedAt` | timestamp | | When team was verified |
| `createdBy` | UUID | FK → users | Team creator |
| `createdAt` | timestamp | DEFAULT now() | Creation timestamp |

**Indexes:**
- Primary key on `id`
- Unique index on `slug`

**Sample query:**
```sql
SELECT * FROM teams WHERE slug = 'acme-conservation';
```

---

#### teamMembers

Team membership and roles.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `teamId` | UUID | NOT NULL, FK → teams | Member of team |
| `userId` | UUID | NOT NULL, FK → users | Team member |
| `teamRole` | varchar(20) | DEFAULT 'member' | owner, member, viewer |
| `joinedAt` | timestamp | DEFAULT now() | Join date |

**Primary Key:** `(teamId, userId)`

**Relationships:**
- `teamId` references `teams.id` (ON DELETE CASCADE)
- `userId` references `users.id` (ON DELETE CASCADE)

**Sample query:**
```sql
SELECT u.name, tm.teamRole
FROM teamMembers tm
JOIN users u ON tm.userId = u.id
WHERE tm.teamId = 'team-uuid';
```

---

#### apiKeys

API keys for external access.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PRIMARY KEY, DEFAULT random | Key identifier |
| `keyHash` | text | NOT NULL | Bcrypt hash of actual key |
| `userId` | UUID | FK → users | Associated user (optional) |
| `teamId` | UUID | FK → teams | Associated team (optional) |
| `name` | text | | Friendly name (e.g., "Mobile App") |
| `permissions` | jsonb | DEFAULT '[]' | Array of permission strings |
| `rateLimit` | integer | DEFAULT 1000 | Requests per hour |
| `lastUsed` | timestamp | | Last API call timestamp |

**Security Note:** Store only hash; actual key shown only once on creation

**Permissions:** ["geocode", "routing", "analytics", "layers", etc]

---

#### environmentalAlerts

User alerts for fire, water, and environmental hazards.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PRIMARY KEY, DEFAULT random | Alert ID |
| `userId` | UUID | NOT NULL, FK → users | Alert recipient |
| `alertType` | varchar(50) | NOT NULL | fire_proximity, drought, flood, etc |
| `severity` | varchar(20) | NOT NULL | info, warning, critical |
| `title` | text | NOT NULL | Alert title |
| `body` | text | NOT NULL | Alert message |
| `metadata` | jsonb | DEFAULT '{}' | Extra data (coords, thresholds, etc) |
| `acknowledged` | boolean | DEFAULT false | User read/dismissed |
| `createdAt` | timestamp | DEFAULT now() | Alert generation time |

**Indexes:**
- Index on `(userId, createdAt DESC)` for list queries
- Index on `(alertType, createdAt)` for filtering by type

**Sample query:**
```sql
SELECT * FROM environmentalAlerts
WHERE userId = 'user-uuid'
  AND createdAt > NOW() - INTERVAL '7 days'
ORDER BY createdAt DESC;
```

---

#### waterGauges

Water gauge station data and readings.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PRIMARY KEY | Station ID |
| `usgsCode` | text | UNIQUE | USGS station code |
| `name` | text | NOT NULL | Station name |
| `lat` | double precision | | Latitude (EPSG:4326) |
| `lon` | double precision | | Longitude (EPSG:4326) |
| `lastReading` | double precision | | Latest stage (water level) |
| `lastReadingTime` | timestamp | | When reading was taken |
| `percentile` | integer | | Current percentile (0-100) |
| `metadata` | jsonb | DEFAULT '{}' | USGS metadata |
| `updatedAt` | timestamp | DEFAULT now() | Last data sync |

**Indexes:**
- Primary key on `id`
- Unique index on `usgsCode`

---

#### droughtData

Drought monitoring records.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PRIMARY KEY | Record ID |
| `lat` | double precision | | Latitude (EPSG:4326) |
| `lon` | double precision | | Longitude (EPSG:4326) |
| `droughtLevel` | integer | | 0=None, 1=Abnormal, 2=Moderate, 3=Severe, 4=Extreme |
| `vegetationHealth` | real | | NDVI value (-1 to +1) |
| `precipitationAnomaly` | real | | % deviation from normal |
| `observationDate` | date | | Date of observation |
| `source` | varchar(50) | | NOAA, USGS, etc |
| `createdAt` | timestamp | DEFAULT now() | Record creation time |

**Indexes:**
- Index on `(lat, lon, observationDate DESC)` for spatial queries

---

#### priorityZones

High-priority intervention areas.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PRIMARY KEY | Zone ID |
| `name` | text | NOT NULL | Zone name |
| `geometry` | geometry(POLYGON, 4326) | NOT NULL | Zone boundary (PostGIS) |
| `priorityScore` | real | NOT NULL | 0-100 priority ranking |
| `fireRiskFactor` | real | | Fire risk component |
| `waterStressFactor` | real | | Water stress component |
| `vegetationStressFactor` | real | | Vegetation stress component |
| `populationImpact` | integer | | Estimated affected population |
| `suggestedIntervention` | text | | Recommended action |
| `createdAt` | timestamp | DEFAULT now() | Zone identification date |
| `updatedAt` | timestamp | DEFAULT now() | Last update |

**Indexes:**
- GIST index on `geometry` for spatial queries

**Sample query:**
```sql
SELECT name, priorityScore FROM priorityZones
WHERE ST_Intersects(geometry, ST_SetSRID(ST_Point(-122.4, 37.8), 4326))
ORDER BY priorityScore DESC;
```

---

### GEO SCHEMA

#### layers

Map layer definitions.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PRIMARY KEY, DEFAULT random | Layer identifier |
| `name` | varchar(100) | UNIQUE, NOT NULL | Layer name (e.g., "Fire Detections") |
| `type` | varchar(50) | DEFAULT 'vector' | vector, raster, geojson |
| `description` | text | | Layer documentation |
| `style` | jsonb | DEFAULT '{}' | MapLibre/deck.gl style JSON |
| `isPublic` | boolean | DEFAULT false | Accessible without auth |
| `minZoom` | integer | DEFAULT 0 | Minimum zoom level (0-22) |
| `maxZoom` | integer | DEFAULT 22 | Maximum zoom level (0-22) |
| `teamId` | UUID | FK → teams | Owning team (null = global) |
| `sortOrder` | integer | DEFAULT 0 | Display order |
| `createdAt` | timestamp | DEFAULT now() | Creation time |
| `updatedAt` | timestamp | DEFAULT now() | Last modification |

**Indexes:**
- Primary key on `id`
- Unique index on `name`
- Index on `teamId` for filtering

---

#### features

Vector features (points, lines, polygons) in layers.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PRIMARY KEY, DEFAULT random | Feature identifier |
| `layerId` | UUID | NOT NULL, FK → layers | Parent layer |
| `properties` | jsonb | NOT NULL, DEFAULT {} | Feature attributes |
| `status` | varchar(20) | DEFAULT 'published' | published, pending, rejected |
| `reviewNote` | text | | Admin review comment |
| `createdAt` | timestamp | DEFAULT now() | Creation time |
| `updatedAt` | timestamp | DEFAULT now() | Last modification |

**Relationships:**
- `layerId` references `geo.layers.id` (ON DELETE CASCADE)

**Note:** GeoJSON geometry added via migration (separate `geom` geometry column)

---

#### fireDetections

Fire detection data from NASA FIRMS.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PRIMARY KEY, DEFAULT random | Detection ID |
| `detectedAt` | timestamp | NOT NULL | Detection time (UTC) |
| `confidence` | varchar(20) | | "high", "medium", "low" |
| `brightness` | real | | Radiative temperature (Kelvin) |
| `frp` | real | | Fire Radiative Power (MW) |
| `satellite` | varchar(50) | | MODIS, VIIRS, etc |
| `createdAt` | timestamp | DEFAULT now() | Data import time |
| `geom` | geometry(POINT, 4326) | | Location (PostGIS) |

**Indexes:**
- GIST index on `geom` for spatial queries
- Index on `detectedAt` for time-range queries

**Sample query:**
```sql
SELECT * FROM geo.fireDetections
WHERE ST_DWithin(
  geom::geography,
  ST_Point(-122.4, 37.8)::geography,
  50000  -- 50km
)
  AND detectedAt > NOW() - INTERVAL '1 day'
ORDER BY detectedAt DESC;
```

---

### TRACKING SCHEMA

#### assets

Tracked assets (vehicles, equipment, personnel).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PRIMARY KEY, DEFAULT random | Asset identifier |
| `name` | varchar(100) | NOT NULL | Display name |
| `type` | varchar(50) | DEFAULT 'vehicle' | vehicle, personnel, equipment, drone |
| `status` | varchar(20) | DEFAULT 'offline' | online, offline, inactive |
| `metadata` | jsonb | DEFAULT '{}' | Custom fields (license plate, etc) |
| `createdAt` | timestamp | DEFAULT now() | Registration time |

**Sample query:**
```sql
SELECT * FROM tracking.assets WHERE status = 'online';
```

---

#### positions

Asset position history (TimescaleDB hypertable).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `time` | timestamp | NOT NULL, PRIMARY | Timestamp (with timezone) |
| `assetId` | UUID | NOT NULL | Asset being tracked |
| `heading` | double precision | | Direction (0-360°) |
| `speed` | double precision | | Velocity (km/h) |
| `altitude` | double precision | | Height above sea level (meters) |
| `metadata` | jsonb | DEFAULT '{}' | Custom fields |
| `geom` | geometry(POINT, 4326) | | Location (PostGIS) |

**Key Features:**
- TimescaleDB hypertable: automatic time-based partitioning
- Immutable append-only log
- Automatic compression of older data

**Indexes:**
- Time index (automatic from hypertable)
- GIST index on `geom`
- Index on `assetId` for per-asset queries

**Sample query:**
```sql
SELECT time, assetId, speed FROM tracking.positions
WHERE assetId = 'asset-uuid'
  AND time > NOW() - INTERVAL '24 hours'
ORDER BY time DESC;
```

**Data retention:** Default 90 days (compressed after 30 days)

---

#### geofences

Geographic boundaries for alerts.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PRIMARY KEY, DEFAULT random | Geofence ID |
| `name` | varchar(100) | NOT NULL | Geofence name |
| `geometry` | jsonb | NOT NULL, DEFAULT {} | GeoJSON Polygon or MultiPolygon |
| `alertOnEnter` | boolean | DEFAULT true | Trigger alert on entry |
| `alertOnExit` | boolean | DEFAULT true | Trigger alert on exit |
| `createdAt` | timestamp | DEFAULT now() | Creation time |

**Note:** Stored as JSONB (not PostGIS geometry) for flexibility

**Sample query:**
```sql
SELECT * FROM tracking.geofences
WHERE name = 'Fire Zone A';
```

---

#### alerts

Alert records (fire proximity, geofence, etc).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PRIMARY KEY, DEFAULT random | Alert ID |
| `assetId` | UUID | FK → assets | Asset in alert (optional) |
| `geofenceId` | UUID | FK → geofences | Triggered geofence (optional) |
| `type` | varchar(50) | NOT NULL | enter, exit, proximity, etc |
| `message` | text | NOT NULL | Alert description |
| `acknowledged` | boolean | DEFAULT false | User confirmation |
| `metadata` | jsonb | DEFAULT '{}' | Extra data (distance, etc) |
| `createdAt` | timestamp | DEFAULT now() | Alert time |

**Relationships:**
- `assetId` references `tracking.assets.id` (optional)
- `geofenceId` references `tracking.geofences.id` (optional)

**Sample query:**
```sql
SELECT * FROM tracking.alerts
WHERE assetId = 'asset-uuid'
  AND type = 'enter'
  AND createdAt > NOW() - INTERVAL '7 days';
```

---

## PostGIS Reference

### Common PostGIS Functions

```sql
-- Distance between two points (geography type = meters)
ST_Distance(geom1::geography, geom2::geography)

-- Points within radius
ST_DWithin(geom1::geography, geom2::geography, distance_meters)

-- Intersection check
ST_Intersects(geom1, geom2)

-- Create point from coordinates
ST_Point(lon, lat)

-- Set spatial reference system
ST_SetSRID(geom, 4326)  -- EPSG:4326 = WGS84 (lat/lon)

-- Area calculation (square meters)
ST_Area(geom::geography)

-- Coordinate conversion
ST_Transform(geom, from_srid, to_srid)
```

### Index Types

```sql
-- GIST (Generalized Search Tree) - best for most cases
CREATE INDEX idx_geom ON table_name USING GIST(geom);

-- BRIN (Block Range Index) - compact, slower
CREATE INDEX idx_geom ON table_name USING BRIN(geom);
```

---

## Drizzle ORM Usage

### Schema Definition

```typescript
import { pgTable, uuid, text, timestamp, geometry } from "drizzle-orm/pg-core";

export const fireDetections = geoSchema.table("fire_detections", {
  id: uuid("id").defaultRandom().primaryKey(),
  detectedAt: timestamp("detected_at", { withTimezone: true }).notNull(),
  brightness: real("brightness"),
  geom: geometry("geom", { type: "point", srid: 4326 }),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
});
```

### Queries

```typescript
import { db } from "@/lib/server/db";
import { fireDetections } from "@/lib/server/db/schema";
import { eq, sql } from "drizzle-orm";

// Select all
const all = await db.select().from(fireDetections);

// With WHERE clause
const recent = await db
  .select()
  .from(fireDetections)
  .where(gt(fireDetections.detectedAt, sql`NOW() - INTERVAL '1 day'`));

// Raw SQL (for PostGIS)
const nearby = await db.execute(sql`
  SELECT * FROM geo.fire_detections
  WHERE ST_DWithin(
    geom::geography,
    ST_Point(${lon}, ${lat})::geography,
    50000
  )
`);
```

---

## Migrations

Migrations are auto-generated by Drizzle:

```bash
npm run db:generate
# Creates file: drizzle/migrations/{timestamp}_{name}.sql
```

Example migration:

```sql
CREATE TABLE IF NOT EXISTS "teams" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "name" text NOT NULL,
  "slug" varchar(100) UNIQUE,
  "created_at" timestamp with time zone DEFAULT now()
);

CREATE INDEX "teams_slug_idx" ON "teams"("slug");
```

---

## Performance Tips

1. **Use GIST indexes on geometry columns**
   ```sql
   CREATE INDEX idx_fire_geom ON geo.fire_detections USING GIST(geom);
   ```

2. **Use geography type for distance queries** (automatically handles Earth curvature)
   ```sql
   ST_Distance(geom::geography, point::geography) -- Result in meters
   ```

3. **Partition large tables** (TimescaleDB for time-series)
   ```sql
   SELECT create_hypertable('tracking.positions', 'time');
   ```

4. **Analyze query plans**
   ```sql
   EXPLAIN ANALYZE SELECT * FROM geo.fire_detections WHERE ST_Intersects(...);
   ```

5. **Vacuum tables periodically**
   ```sql
   VACUUM ANALYZE geo.fire_detections;
   ```

---

## Backup & Recovery

```bash
# Backup entire database
pg_dump postgresql://geo:geopass@localhost:5432/plantgeo > backup.sql

# Restore
psql postgresql://geo:geopass@localhost:5432/plantgeo < backup.sql

# Backup single table
pg_dump -t geo.fire_detections postgresql://... > fires.sql
```

---

## Monitoring

```sql
-- Check table sizes
SELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size
FROM pg_tables
WHERE schemaname IN ('public', 'geo', 'tracking')
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;

-- Check index sizes
SELECT indexname, pg_size_pretty(pg_relation_size(indexrelid))
FROM pg_indexes
WHERE schemaname IN ('public', 'geo', 'tracking');

-- Check connection count
SELECT datname, count(*) FROM pg_stat_activity GROUP BY datname;

-- Check slow queries (requires logging)
SELECT query, calls, mean_exec_time FROM pg_stat_statements ORDER BY mean_exec_time DESC;
```
