# PlantGeo System Architecture

This document describes the complete system architecture, data flows, and technical patterns used in PlantGeo.

## High-Level Architecture

PlantGeo follows a client-server architecture with clear separation of concerns:

```
┌──────────────────────────────────────────────────────────────┐
│                    CLIENT LAYER                              │
│ ┌────────────────────────────────────────────────────────┐   │
│ │ React 19 Components + TypeScript                       │   │
│ │ State Management: Zustand (global) + Jotai (per-layer)│   │
│ └────────────────────────────────────────────────────────┘   │
│                                                               │
│ ┌──────────────────┐  ┌──────────────────┐                   │
│ │ MapLibre GL JS   │  │  deck.gl v9      │                   │
│ │ • Globe          │  │ • 30+ layer types│                   │
│ │ • Terrain        │  │ • Interleaved    │                   │
│ │ • Raster overlay │  │   mode rendering │                   │
│ │ • Vector tiles   │  │ • Interactive    │                   │
│ └──────────────────┘  └──────────────────┘                   │
│                                                               │
│ ┌────────────────────────────────────────────────────────┐   │
│ │ Three.js Custom Layers (via CustomLayerInterface)     │   │
│ │ • 3D models (buildings, trees)                        │   │
│ │ • Particles and effects                               │   │
│ └────────────────────────────────────────────────────────┘   │
└──────────────────────┬───────────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        │ tRPC | REST  │ WebSocket    │ SSE Stream
        │              │              │
┌───────▼──────────────▼──────────────▼──────────────────────────┐
│                   SERVER LAYER (Next.js 15)                    │
│                                                                 │
│ ┌─────────────────────────────────────────────────────────┐   │
│ │ tRPC Procedures (init.ts)                               │   │
│ │ • publicProcedure (no auth required)                   │   │
│ │ • protectedProcedure (authenticated users)            │   │
│ │ • contributorProcedure (contributor+ role)            │   │
│ │ • expertProcedure (expert+ role)                       │   │
│ │ • adminProcedure (admin only)                          │   │
│ └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│ ┌─────────────────────────────────────────────────────────┐   │
│ │ tRPC Routers (src/lib/server/trpc/routers/)            │   │
│ │ • layers, routing, teams, contributions               │   │
│ │ • wildfire, analytics, tracking                        │   │
│ │ • environmental, strategy, alerts                      │   │
│ │ • places, visualization, community                     │   │
│ │ • regional-intelligence                                │   │
│ └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│ ┌─────────────────────────────────────────────────────────┐   │
│ │ Backend Services (src/lib/server/services/)            │   │
│ │ • Data fetching from external APIs                     │   │
│ │ • GIS computations and transformations                 │   │
│ │ • Caching layer (Redis)                                │   │
│ │ • Business logic and aggregations                      │   │
│ └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│ ┌─────────────────────────────────────────────────────────┐   │
│ │ Background Jobs (src/lib/server/jobs/)                 │   │
│ │ • Alert dispatcher                                      │   │
│ │ • Email digest                                          │   │
│ │ • Priority zone refresh                                 │   │
│ │ • Data cleanup                                          │   │
│ └─────────────────────────────────────────────────────────┘   │
└───────┬──────────────┬──────────────────┬─────────────────────┘
        │              │                  │
        │              │                  │
┌───────▼──────────┐  ┌▼─────────────┐  ┌▼──────────────────────┐
│   PostgreSQL     │  │   Redis      │  │  External APIs       │
│   16 + PostGIS   │  │   7          │  │ • NASA FIRMS         │
│   + TimescaleDB  │  │              │  │ • Nominatim/Photon  │
│                  │  │ • Cache      │  │ • Valhalla           │
│ • Users & Auth   │  │ • Pub/Sub    │  │ • NOAA Weather       │
│ • Layers & Data  │  │ • Sessions   │  │ • PlantCommerce API  │
│ • Fire, Water    │  │              │  │ • Mapillary          │
│ • Tracking       │  │              │  │ • USGS, etc          │
│ • Alerts         │  │              │  │ • Anthropic Claude   │
└──────────────────┘  └──────────────┘  └──────────────────────┘
```

## Data Flow Patterns

### 1. Query Data Flow (Read-Heavy)

```
User Action (e.g., "Get fire detections in bounding box")
  │
  ├→ Frontend: Call tRPC procedure (client.wildfire.getFireDetections)
  │
  ├→ Server: Procedure router matches and executes
  │
  ├→ Service Layer:
  │   └→ Check Redis cache first
  │      └→ Cache hit: return cached data
  │      └→ Cache miss: fetch from external API or database
  │         └→ Transform/normalize response
  │         └→ Store in Redis (with TTL)
  │
  ├→ Return to client (via tRPC)
  │
  └→ Frontend: Update Zustand store → re-render React
```

### 2. Mutation Data Flow (Write)

```
User Action (e.g., "Create a custom layer")
  │
  ├→ Frontend: Call tRPC mutation (client.layers.create)
  │
  ├→ Server: Authentication check (protectedProcedure)
  │
  ├→ Validation: Zod schema validation
  │
  ├→ Database: Insert into PostgreSQL
  │   └→ Drizzle ORM handles SQL generation
  │   └→ Return inserted row
  │
  ├→ Cache invalidation: Clear Redis keys for layers
  │
  ├→ Pub/Sub broadcast (if relevant): Notify connected clients
  │
  ├→ Return to client (new layer object)
  │
  └→ Frontend: Update Zustand store
```

### 3. Real-Time Data Flow (SSE - Server-Sent Events)

Used for one-way server → client broadcasts (e.g., fire alerts):

```
Background Process / Webhook
  │
  ├→ Trigger: New fire detected, user's alert threshold triggered
  │
  ├→ Service: Alert engine creates alert record in database
  │
  ├→ Redis Pub/Sub: Publish alert message to channel
  │
  ├→ Server-Sent Events endpoint: Subscribed clients receive update
  │   /api/stream/alerts
  │
  └→ Frontend: SSE handler updates store → toast notification
     const eventSource = new EventSource('/api/stream/alerts');
     eventSource.onmessage = (e) => useAlertsStore.addAlert(JSON.parse(e.data));
```

### 4. Bidirectional Real-Time Flow (WebSocket)

Used for interactive, request-response scenarios (e.g., tracking):

```
Client connects to WebSocket
  wss://example.com/api/ws?token=...
  │
  ├→ Server: Authenticate via query token
  │
  ├→ Client sends: { type: "track", assetId: "..." }
  │
  ├→ Server processes: Queries tracking database
  │
  ├→ Server responds: { type: "positions", data: [...] }
  │
  ├→ Subscribe to Redis channel for asset updates
  │
  ├→ On each position update: Broadcast to subscribed clients
  │
  └→ Frontend: WebSocket handler updates tracking store
```

## Map Rendering Pipeline

MapLibre GL JS and deck.gl are used in "interleaved mode" to share the same map view:

```
1. MapLibre GL JS initialization
   ├→ Load basemap style (PMTiles via Martin)
   ├→ Add terrain layer
   ├→ Add raster overlay tiles (satellite imagery)
   └→ Initialize camera state

2. deck.gl layer initialization
   ├→ Mount DeckOverlay component on MapLibre
   ├→ Register all active layers (from layer store)
   ├→ Each layer gets:
   │   ├→ Data source (GeoJSON, Mapbox tiles, or WebGL data)
   │   ├→ Rendering function (e.g., ScatterLayer, HeatmapLayer)
   │   └→ Style properties (colors, sizes, opacities from layer store)
   └→ Interleaved rendering with MapLibre

3. User interaction
   ├→ Mouse move: Tooltip via deck.gl pickObjects()
   ├→ Click: Identify feature, open panel
   ├→ Zoom/Pan: Update map center/zoom (shared between both libraries)
   └→ Layer visibility toggle: Update layer store → deck.gl re-renders

4. Three.js custom layers
   ├→ Via CustomLayerInterface (MapLibre)
   ├→ Render at specific depth/z-order
   └→ Example: 3D building extrusions, particle effects
```

## State Management

### Zustand Stores

Global application state stored in `/src/stores/`:

| Store | Purpose |
|-------|---------|
| `layer-store.ts` | Active layers, visibility, styles, order |
| `map-store.ts` | Map center, zoom, bearing, pitch |
| `routing-store.ts` | Routing parameters, selected route, isochrone |
| `tracking-store.ts` | Active asset tracking, vehicle positions |
| `alerts-store.ts` | Fire alerts, environmental alerts, dismissals |
| `imagery-store.ts` | Street view imagery, Mapillary sequences |
| `drawing-store.ts` | Drawing mode, drawn features, measurement |
| `realtime-store.ts` | WebSocket connection status, live data |
| `search-store.ts` | Geocode search results, selected place |
| `auth-store.ts` | Current user, authentication state |
| `regional-intelligence-store.ts` | AI context, analysis results |

Each store uses Zustand's pattern:

```typescript
export const useLayerStore = create<LayerStore>((set) => ({
  layers: [],
  activeLayerId: null,
  addLayer: (layer) => set((state) => ({
    layers: [...state.layers, layer],
  })),
  // ...
}));

// In components
const { layers, addLayer } = useLayerStore();
```

### Per-Layer State (Jotai)

For complex per-layer state (data, filters, cache), Jotai atoms are used:

```typescript
const layerDataAtom = atomWithQuery(async () => {
  // Load data for specific layer
  return fetchLayerData(layerId);
});
```

## Authentication & Authorization

### NextAuth.js Integration

- **Location**: `src/lib/server/auth.ts`
- **Providers**: OAuth (Google, GitHub) + email/password
- **Session**: Database sessions stored in PostgreSQL

### Authorization Levels

Implemented via tRPC procedure middleware:

```
publicProcedure
  └→ No authentication required (e.g., view public layers)

protectedProcedure
  └→ Authenticated user required
  └→ Available to all roles (contributor, expert, admin)

contributorProcedure
  └→ User role must be "contributor", "expert", or "admin"
  └→ Can upload layers, create features

expertProcedure
  └→ User role must be "expert" or "admin"
  └→ Can review contributions, set strategy

adminProcedure
  └→ User role must be "admin" only
  └→ Can manage users, teams, system settings
```

## Caching Strategy

### Redis Cache

Used for expensive computations and external API responses:

**TTLs by data type:**

| Data | TTL | Key Pattern |
|------|-----|-------------|
| Fire detections (NASA FIRMS) | 30 min | `fire:detections:{bbox}` |
| Weather data | 1 hour | `weather:{lat},{lon}` |
| Geocoding results | 24 hours | `geocode:{query}` |
| Routing responses | 24 hours | `route:{hash(params)}` |
| User sessions | 30 days | NextAuth default |

**Invalidation:**

- Manual: On write mutations (e.g., delete layer → clear `layers:*`)
- TTL: Automatic expiry
- Pub/Sub: Broadcast cache clear to other server instances

## Geospatial Queries

### PostGIS

All geospatial queries use PostGIS functions in SQL:

```typescript
// Example: Find fires within 50km radius
const fires = await db.execute(sql`
  SELECT * FROM geo.fire_detections
  WHERE ST_DWithin(
    geom::geography,
    ST_Point(${lon}, ${lat})::geography,
    50000  -- 50km in meters
  )
  ORDER BY ST_Distance(geom::geography, ST_Point(${lon}, ${lat})::geography)
`);
```

### PostGIS Columns

All tables with geospatial data include a `geom` column:

```sql
geom geometry(POINT, 4326)    -- Point (lat/lon, EPSG:4326)
geom geometry(POLYGON, 4326)  -- Polygon (geofences, zones)
geom geometry(LINESTRING, 4326)  -- Linestring (routes, paths)
```

### Spatial Indexes

Automatically created for performance:

```sql
CREATE INDEX idx_fire_detections_geom
ON geo.fire_detections USING GIST(geom);
```

## Tile Serving

### PMTiles (Basemap)

- **Format**: PMTiles v3 (single-file archive)
- **Storage**: Cloudflare R2 bucket
- **CDN**: Cloudflare CDN for fast access
- **Client**: MapLibre GL JS loads via HTTP with byte-range requests
- **URL**: `NEXT_PUBLIC_PMTILES_URL` environment variable

### MVT (Dynamic Tiles)

- **Server**: Martin v1.4 (Rust)
- **Source**: PostgreSQL + PostGIS
- **Format**: Mapbox Vector Tiles (MVT)
- **Caching**: Redis (tile cache)
- **URL**: `NEXT_PUBLIC_MAP_STYLE_URL` (Martin style.json)

### Tile Rendering Pipeline

```
Client requests zoom level
  │
  ├→ Martin checks Redis cache
  │   └→ Hit: return cached tile
  │   └→ Miss: continue
  │
  ├→ PostGIS query (via Martin)
  │   └→ Use spatial indexes to find features in tile bounds
  │   └→ Convert to GeoJSON then MVT
  │
  ├→ Store in Redis with TTL
  │
  └→ Send to client as binary MVT tile
```

## AI Pipeline

### Regional Intelligence (Track 31)

```
User clicks on map location
  │
  ├→ Frontend: assemble context
  │   ├→ User's region
  │   ├→ Active layers' data
  │   ├→ Fire/water/environmental status
  │   └→ Historical trends
  │
  ├→ Server: POST /api/ai/regional-intelligence
  │
  ├→ Service: ai-prompt.ts
  │   └→ Assemble all context data from services:
  │       ├→ Recent fires (nasa-firms)
  │       ├→ Water levels (usgs-water)
  │       ├→ Vegetation health (vegetation.ts)
  │       ├→ Soil data (soilgrids.ts)
  │       └→ Carbon potential (carbon-potential.ts)
  │
  ├→ Call Anthropic Claude API
  │   └→ Streaming response (SSE to frontend)
  │
  ├→ Frontend: Display streamed text in panel
  │
  └→ Store analysis in regional-intelligence-store
```

**Prompt template:**

```
"Analyze this region for environmental health:
- Vegetation: {vegetation_data}
- Water: {water_data}
- Fire risk: {fire_risk}
- Soil: {soil_data}
- Carbon potential: {carbon_potential}

Provide actionable insights and recommendations."
```

## External Integrations

### Fire Detection (NASA FIRMS)

```
Daily job runs:
  ├→ Fetch active fires from NASA FIRMS API
  ├→ Filter by date range
  ├→ Convert to GeoJSON
  ├→ Insert/update in geo.fire_detections table
  └→ Trigger alert engine (if within user's watch zones)
```

### Water Data (USGS)

```
Hourly job:
  ├→ Query USGS water gauge stations
  ├→ Fetch latest measurements
  ├→ Compare to thresholds
  └→ Create alerts if exceeded
```

### Weather (NOAA)

```
On-demand (via wildfire router):
  ├→ Get current weather for location
  ├→ Used in fire risk calculations
  └→ Cached for 1 hour
```

### Routing (Valhalla)

```
POST http://localhost:8002/route
{
  "locations": [
    { "lat": 40.7, "lon": -74.0 },
    { "lat": 40.8, "lon": -73.9 }
  ],
  "costing": "auto",
  "directions_options": { "units": "kilometers" }
}

Response:
{
  "routes": [{
    "legs": [...],
    "geometry": "encoded polyline"
  }]
}
```

### Geocoding (Photon/Nominatim)

```
GET /api/v1/geocode?q=san+francisco&limit=5

Response:
{
  "features": [
    {
      "geometry": { "type": "Point", "coordinates": [-122.42, 37.77] },
      "properties": { "name": "San Francisco", ... }
    }
  ]
}
```

## Deployment Architecture (Railway)

### Services

| Service | Role | Port |
|---------|------|------|
| Next.js App | API server + SSR | 3000 |
| PostgreSQL | Primary database | 5432 |
| Redis | Cache + pub/sub | 6379 |
| Martin | Tile server | 3100 |
| Valhalla | Routing engine | 8002 |

### Multi-Service Setup

```
Railway Project
  ├→ Service: next-app
  │   ├→ Build: Dockerfile
  │   ├→ Environment: All .env variables
  │   ├→ Port: 3000
  │   └→ Health check: /api/health
  │
  ├→ Service: postgres
  │   ├→ Image: postgres:16
  │   ├→ Volume: persistent storage
  │   └→ DATABASE_URL exposed to app
  │
  ├→ Service: redis
  │   ├→ Image: redis:7
  │   └→ REDIS_URL exposed to app
  │
  ├→ Service: martin
  │   ├→ Build: Custom Dockerfile
  │   ├→ DATABASE_URL: shared with app
  │   └→ MARTIN_URL exposed to app
  │
  └→ Service: valhalla
      ├→ Image: valhalla/valhalla:latest
      └→ VALHALLA_URL exposed to app
```

### Environment Propagation

```
Railway Variables → Dockerfile ENV → app /env.local → process.env
```

## Error Handling

### tRPC Errors

Thrown from procedures are caught and formatted:

```typescript
// In procedure
throw new TRPCError({
  code: 'UNAUTHORIZED',   // or FORBIDDEN, NOT_FOUND, BAD_REQUEST, INTERNAL_SERVER_ERROR
  message: 'User not authenticated',
});

// Client receives
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "User not authenticated",
    "data": { ... }
  }
}
```

### REST API Errors

```typescript
// Example from /api/v1/geocode
return NextResponse.json(
  { error: "Missing required parameter: q" },
  { status: 400 }
);
```

### Client Error Handling

```typescript
// tRPC query with error handling
const { data, error, isLoading } = useQuery(
  () => trpc.wildfire.getFireDetections.query({ bbox: "..." }),
  {
    onError: (error) => {
      showErrorToast(error.message);
    },
  }
);
```

## Performance Considerations

### Frontend Performance

- **Code splitting**: Dynamic imports for map components (ssr: false)
- **Lazy loading**: Panels and layers loaded on-demand
- **Memoization**: React.memo on expensive layer components
- **WebGL rendering**: deck.gl handles large datasets efficiently

### Backend Performance

- **Database indexes**: PostGIS GIST indexes on geometry columns
- **Connection pooling**: Redis and PostgreSQL use connection pools
- **Query optimization**: Use PostGIS index-aware queries
- **Caching**: Redis cache for external API responses and computed results

### Network Performance

- **Tile compression**: MVT tiles are gzipped
- **HTTP/2**: Cloudflare R2 supports multiplexing
- **CDN**: R2 and Cloudflare edge locations for global coverage
- **Batch requests**: Combine multiple tRPC calls where possible

## Monitoring & Debugging

### Health Checks

```bash
# Application health
curl http://localhost:3000/api/health

# Database
psql postgresql://geo:geopass@localhost:5432/plantgeo -c "SELECT 1"

# Redis
redis-cli ping

# Martin
curl http://localhost:3100/health

# Valhalla
curl http://localhost:8002/status
```

### Logs

- **Next.js**: Console output (stdout)
- **Database**: PostgreSQL log files or `pg_log` directory
- **Redis**: Redis CLI `MONITOR` or `SLOWLOG`
- **Docker**: `docker logs <container>`

### Metrics to Track

- API response times (tRPC middleware timing)
- Database query performance (slow query log)
- Cache hit rates (Redis INFO stats)
- External API failures and latencies
- User engagement (analytics store)
