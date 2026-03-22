# PlantGeo API Reference

Complete documentation of all tRPC routers and REST API endpoints in PlantGeo.

## tRPC Overview

tRPC endpoints are available at `/api/trpc/[procedure]` and can be called via the client:

```typescript
import { trpc } from "@/lib/trpc/client";

// Query (read)
const data = await trpc.layers.list.query();

// Mutation (write)
const result = await trpc.layers.create.mutate({ name: "My Layer" });
```

All tRPC procedures use superjson for serialization, supporting Dates, Maps, Sets, etc.

## Authorization Levels

| Level | Required Role | Example |
|-------|---------------|---------|
| `publicProcedure` | None | View public layers |
| `protectedProcedure` | Any authenticated user | List my teams |
| `contributorProcedure` | contributor, expert, admin | Create a layer |
| `expertProcedure` | expert, admin | Review contributions |
| `adminProcedure` | admin | Manage users |

## tRPC Routers

### Layers Router

Manage map layers and their styles.

**Route**: `trpc.layers.*`

#### list

List all public layers or team-specific layers.

```typescript
// Query
await trpc.layers.list.query({
  teamId?: string;  // Optional: filter by team
});

// Response
[
  {
    id: string;
    name: string;
    type: "vector" | "raster" | "geojson";
    description?: string;
    style: Record<string, unknown>;
    isPublic: boolean;
    minZoom: number;
    maxZoom: number;
    teamId?: string;
    sortOrder: number;
    createdAt: Date;
    updatedAt: Date;
  }
]
```

**Auth**: `publicProcedure`

#### getById

Get a single layer by ID.

```typescript
await trpc.layers.getById.query({
  id: string;  // UUID
});

// Response: Layer object (see list) or null
```

**Auth**: `publicProcedure`

#### create

Create a new layer. Requires contributor role.

```typescript
await trpc.layers.create.mutate({
  name: string;                // Required, max 100 chars
  type?: "vector" | "raster" | "geojson";
  description?: string;
  style?: Record<string, unknown>;
  isPublic?: boolean;
  minZoom?: number;            // 0-22, default 0
  maxZoom?: number;            // 0-22, default 22
  teamId?: string;             // UUID, optional
  sortOrder?: number;          // Default 0
});

// Response: Created layer object
```

**Auth**: `contributorProcedure`

#### update

Update an existing layer.

```typescript
await trpc.layers.update.mutate({
  id: string;                  // UUID (required)
  name?: string;
  type?: string;
  description?: string;
  style?: Record<string, unknown>;
  isPublic?: boolean;
  minZoom?: number;
  maxZoom?: number;
  teamId?: string;
  sortOrder?: number;
});

// Response: Updated layer object
```

**Auth**: `contributorProcedure`

#### delete

Delete a layer.

```typescript
await trpc.layers.delete.mutate({
  id: string;  // UUID
});

// Response: { success: true }
```

**Auth**: `contributorProcedure`

#### reorder

Reorder layers by setting sort order.

```typescript
await trpc.layers.reorder.mutate({
  ids: string[];  // Array of layer UUIDs in desired order
});

// Response: { success: true }
```

**Auth**: `contributorProcedure`

---

### Routing Router

Multi-modal routing, isochrones, and distance matrices.

**Route**: `trpc.routing.*`

#### route

Calculate a route between points.

```typescript
await trpc.routing.route.mutate({
  locations: [
    { lat: 40.7, lon: -74.0 },
    { lat: 40.8, lon: -73.9 }
  ],
  costing: "auto" | "bicycle" | "pedestrian" | "truck",
  directions_options?: {
    units: "miles" | "kilometers";
  },
  alternates?: number;  // 0-3 alternate routes
});

// Response
{
  routes: [
    {
      geometry: GeoJSON.LineString;
      maneuvers: [
        {
          type: number;
          instruction: string;
          distance: number;      // meters
          time: number;          // seconds
          beginShapeIndex: number;
          endShapeIndex: number;
        }
      ];
      summary: {
        length: number;         // meters
        time: number;           // seconds
        hasHighway: boolean;
        hasToll: boolean;
      };
    }
  ];
  rawResponse: unknown;
}
```

**Auth**: `publicProcedure`

**Backend**: Calls Valhalla routing engine

#### isochrone

Calculate reachability area from a point (isochrone/accessibility).

```typescript
await trpc.routing.isochrone.mutate({
  locations: [
    { lat: 40.7, lon: -74.0 }
  ],
  costing: "auto" | "bicycle" | "pedestrian",
  contours: [
    { time: 15, color: "ff0000" },
    { time: 30, color: "ffff00" }
  ],
  polygons: true;  // Return as polygons vs lines
});

// Response
{
  contours: [
    {
      coordinates: [[[lon, lat], ...]];
      color: string;
      time: number;
    }
  ];
}
```

**Auth**: `publicProcedure`

**Backend**: Calls Valhalla isochrone API

#### matrix

Calculate distance/time matrix between sources and targets.

```typescript
await trpc.routing.matrix.mutate({
  sources: [
    { lat: 40.7, lon: -74.0 },
    { lat: 40.8, lon: -73.9 }
  ],
  targets: [
    { lat: 40.9, lon: -73.8 }
  ],
  costing?: "auto";  // Default
});

// Response
[
  [  // distances/times from source 0
    { distance: 5000, time: 300 },  // to target 0
    { distance: 6000, time: 350 }   // to target 1
  ],
  [  // distances/times from source 1
    { distance: 4000, time: 240 }
  ]
]
```

**Auth**: `publicProcedure`

---

### Teams Router

Team management and membership.

**Route**: `trpc.teams.*`

#### listMyTeams

List all teams the authenticated user belongs to.

```typescript
await trpc.teams.listMyTeams.query();

// Response
[
  {
    team: {
      id: string;
      name: string;
      slug?: string;
      description?: string;
      orgType?: string;
      specialties?: string[];
      website?: string;
      serviceArea?: Record<string, unknown>;
      isVerified: boolean;
      verifiedAt?: Date;
      createdBy: string;
      createdAt: Date;
    };
    role: "owner" | "member" | "viewer";
  }
]
```

**Auth**: `protectedProcedure`

#### inviteMember

Invite a user to a team.

```typescript
await trpc.teams.inviteMember.mutate({
  teamId: string;
  userId: string;
  teamRole?: "owner" | "member" | "viewer";  // Default "member"
});

// Response: Team member object
```

**Auth**: `protectedProcedure` (requires owner/member role in team)

#### removeMember

Remove a user from a team.

```typescript
await trpc.teams.removeMember.mutate({
  teamId: string;
  userId: string;
});

// Response: { success: true }
```

**Auth**: `protectedProcedure` (requires owner role)

#### updateMemberRole

Change a team member's role.

```typescript
await trpc.teams.updateMemberRole.mutate({
  teamId: string;
  userId: string;
  teamRole: "owner" | "member" | "viewer";
});

// Response: Updated team member object
```

**Auth**: `protectedProcedure` (requires owner role)

---

### Wildfire Router

Fire detection and risk analysis.

**Route**: `trpc.wildfire.*`

#### getFireDetections

Get active fire detections from NASA FIRMS.

```typescript
await trpc.wildfire.getFireDetections.query({
  bbox?: string;      // "west,south,east,north"
  dayRange?: number;  // 1-10, default 1
});

// Response: GeoJSON FeatureCollection of fire detections
{
  type: "FeatureCollection";
  features: [
    {
      type: "Feature";
      geometry: { type: "Point"; coordinates: [lon, lat] };
      properties: {
        detectDate: string;
        confidence: string;
        brightness: number;
        frp: number;  // Fire Radiative Power
      };
    }
  ];
}
```

**Auth**: `publicProcedure`

#### getFireRiskForArea

Calculate fire risk score for an area with terrain + weather parameters.

```typescript
await trpc.wildfire.getFireRiskForArea.query({
  vegetationType: string;      // e.g., "mixed_forest"
  slope: number;               // 0-90 degrees
  aspect: number;              // 0-360 degrees
  humidity: number;            // 0-100 percent
  windSpeed: number;           // >= 0
  lat?: number;                // Optional for weather fetching
  lon?: number;
});

// Response
{
  score: number;              // 0-100 risk score
  fwiComponents?: {
    ffmc: number;             // Fine Fuel Moisture Code
    dmc: number;              // Duff Moisture Code
    dc: number;               // Drought Code
    isi: number;              // Initial Spread Index
    bui: number;              // Buildup Index
    fwi: number;              // Fire Weather Index
  };
}
```

**Auth**: `publicProcedure`

#### getFireRiskForPoint

Get comprehensive fire risk analysis for a single point.

```typescript
await trpc.wildfire.getFireRiskForPoint.query({
  lat: number;
  lon: number;
});

// Response
{
  score: number;              // 0-100
  compositeScore: number;     // Weighted combination
  fwiComponents: { ... };
  vegetationType: string;
  fuelParams: { ... };
}
```

**Auth**: `publicProcedure`

---

### Alerts Router

Environmental and fire alert management.

**Route**: `trpc.alerts.*`

#### list

List alerts for the authenticated user.

```typescript
await trpc.alerts.list.query({
  status?: "active" | "dismissed";
  limit?: number;
  offset?: number;
});

// Response: Array of alert objects
```

**Auth**: `protectedProcedure`

#### dismiss

Mark an alert as dismissed.

```typescript
await trpc.alerts.dismiss.mutate({
  alertId: string;
});

// Response: { success: true }
```

**Auth**: `protectedProcedure`

---

### Tracking Router

Asset tracking and positioning.

**Route**: `trpc.tracking.*`

#### getPositions

Get recent positions for an asset.

```typescript
await trpc.tracking.getPositions.query({
  assetId: string;
  limit?: number;       // Default 100
  timeRange?: "1h" | "24h" | "7d";
});

// Response
[
  {
    time: Date;
    assetId: string;
    lat: number;
    lon: number;
    heading?: number;
    speed?: number;
    altitude?: number;
    metadata?: Record<string, unknown>;
  }
]
```

**Auth**: `protectedProcedure`

---

### Analytics Router

User behavior and engagement analytics.

**Route**: `trpc.analytics.*`

#### track

Track a user action.

```typescript
await trpc.analytics.track.mutate({
  event: string;           // e.g., "layer_toggled"
  properties?: Record<string, unknown>;
  timestamp?: Date;
});

// Response: { success: true }
```

**Auth**: `protectedProcedure`

#### getMetrics

Get aggregated analytics metrics.

```typescript
await trpc.analytics.getMetrics.query({
  startDate: Date;
  endDate: Date;
  groupBy?: "day" | "week" | "month";
});

// Response: Aggregated metrics
```

**Auth**: `protectedProcedure`

---

### Environmental Router

Environmental data queries (water, vegetation, soil, etc).

**Route**: `trpc.environmental.*`

#### getWaterData

Get water-related metrics for a region.

```typescript
await trpc.environmental.getWaterData.query({
  bbox: string;           // "west,south,east,north"
  dataTypes?: string[];   // e.g., ["drought", "water_level"]
});

// Response: Environmental data
```

**Auth**: `publicProcedure`

#### getVegetationData

Get vegetation and land cover data.

```typescript
await trpc.environmental.getVegetationData.query({
  lat: number;
  lon: number;
  radius?: number;        // meters
});

// Response: Vegetation metrics
```

**Auth**: `publicProcedure`

---

### Strategy Router

Strategy and intervention planning.

**Route**: `trpc.strategy.*`

#### listStrategies

List all strategies or filter by criteria.

```typescript
await trpc.strategy.listStrategies.query({
  status?: "active" | "completed" | "archived";
  teamId?: string;
});

// Response: Array of strategy objects
```

**Auth**: `publicProcedure`

#### createStrategy

Create a new strategy.

```typescript
await trpc.strategy.createStrategy.mutate({
  title: string;
  description: string;
  location: { lat: number; lon: number };
  priority?: "low" | "medium" | "high";
  teamId?: string;
});

// Response: Created strategy object
```

**Auth**: `contributorProcedure`

---

### Visualization Router

Custom data visualization and styling.

**Route**: `trpc.visualization.*`

#### getStyles

Get predefined visualization styles.

```typescript
await trpc.visualization.getStyles.query({
  layerType?: string;  // e.g., "scatter", "heatmap"
});

// Response: Array of style presets
```

**Auth**: `publicProcedure`

---

### Places Router

Place search and geocoding.

**Route**: `trpc.places.*`

#### search

Search for places.

```typescript
await trpc.places.search.query({
  query: string;
  limit?: number;
  bbox?: string;  // Bias results to bounding box
});

// Response: GeoJSON FeatureCollection
```

**Auth**: `publicProcedure`

---

### Community Router

Community contributions and feedback.

**Route**: `trpc.community.*`

#### listContributions

List community contributions.

```typescript
await trpc.community.listContributions.query({
  status?: "pending" | "approved" | "rejected";
  limit?: number;
});

// Response: Array of contribution objects
```

**Auth**: `publicProcedure`

---

### Regional Intelligence Router

AI-powered regional analysis.

**Route**: `trpc.regionalIntelligence.*`

#### analyzeRegion

Get AI analysis of a region using Claude.

```typescript
await trpc.regionalIntelligence.analyzeRegion.mutate({
  lat: number;
  lon: number;
  radius?: number;  // km
});

// Response: Streaming text (via SSE or regular response)
{
  analysis: string;
  insights: string[];
  recommendations: string[];
}
```

**Auth**: `publicProcedure`

**Backend**: Calls Anthropic Claude API with regional context

---

## REST API Endpoints

### GET /api/health

System health check.

```bash
curl http://localhost:3000/api/health

# Response
{ "status": "ok", "services": { "db": "ok", "redis": "ok" } }
```

---

### GET /api/v1/geocode

Geocode an address to coordinates.

**Query Parameters:**
- `q` (required): Search query
- `limit` (optional, default 5, max 50): Number of results
- `x-api-key` (header): API key for rate limiting

```bash
curl -H "x-api-key: YOUR_API_KEY" \
  "http://localhost:3000/api/v1/geocode?q=san+francisco&limit=5"

# Response
{
  "features": [
    {
      "type": "Feature",
      "geometry": {
        "type": "Point",
        "coordinates": [-122.42, 37.77]
      },
      "properties": {
        "name": "San Francisco, California",
        "type": "city"
      }
    }
  ]
}
```

---

### GET /api/v1/geocode/reverse

Reverse geocode coordinates to address.

**Query Parameters:**
- `lat` (required): Latitude
- `lon` (required): Longitude

```bash
curl "http://localhost:3000/api/v1/geocode/reverse?lat=37.77&lon=-122.42"

# Response
{
  "address": "San Francisco, California, USA",
  "lat": 37.77,
  "lon": -122.42
}
```

---

### GET /api/stream/alerts

Server-Sent Events stream for fire and environmental alerts.

```javascript
const eventSource = new EventSource('/api/stream/alerts');

eventSource.onmessage = (event) => {
  const alert = JSON.parse(event.data);
  console.log('New alert:', alert);
  // { id, type, title, body, severity, metadata }
};

eventSource.onerror = () => {
  console.error('Stream disconnected');
  eventSource.close();
};
```

---

### GET /api/imagery/sequence

Get Mapillary imagery sequences for a location.

**Query Parameters:**
- `lat` (required): Latitude
- `lon` (required): Longitude
- `radius` (optional, default 500): Search radius in meters

```bash
curl "http://localhost:3000/api/imagery/sequence?lat=37.77&lon=-122.42&radius=1000"

# Response
{
  "sequences": [
    {
      "id": "mapillary_sequence_id",
      "coordinates": [[lon, lat], ...],
      "captured": "2024-03-15",
      "views": 150
    }
  ]
}
```

---

### GET /api/imagery/image

Get a specific Mapillary image.

**Query Parameters:**
- `imageId` (required): Mapillary image ID
- `size` (optional): "small", "medium", "large"

```bash
curl "http://localhost:3000/api/imagery/image?imageId=IMAGE_ID&size=large"

# Response: Image binary data (redirect to Mapillary CDN)
```

---

### POST /api/ingest/firms

Ingest NASA FIRMS fire detection data (internal webhook).

**Body:**
```json
{
  "fires": [
    {
      "lat": 37.77,
      "lon": -122.42,
      "confidence": "high",
      "brightness": 350,
      "detectedAt": "2024-03-15T10:30:00Z"
    }
  ]
}
```

**Auth**: Requires admin API token

---

### POST /api/ingest/sensors

Ingest IoT sensor data.

**Body:**
```json
{
  "assetId": "asset-uuid",
  "readings": [
    {
      "type": "temperature",
      "value": 25.5,
      "unit": "celsius",
      "timestamp": "2024-03-15T10:30:00Z"
    }
  ]
}
```

---

### WebSocket /api/ws

Bidirectional WebSocket for real-time tracking and live data.

**Connection:**
```javascript
const ws = new WebSocket('wss://example.com/api/ws?token=YOUR_SESSION_TOKEN');

ws.onopen = () => {
  // Subscribe to asset tracking
  ws.send(JSON.stringify({
    type: 'subscribe',
    channel: 'asset:asset-uuid'
  }));
};

ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  // { type, data, channel }
};
```

**Message Types:**

Subscribe to channel:
```json
{
  "type": "subscribe",
  "channel": "asset:uuid"
}
```

Receive position update:
```json
{
  "type": "position",
  "channel": "asset:uuid",
  "data": {
    "lat": 37.77,
    "lon": -122.42,
    "heading": 45,
    "speed": 20.5
  }
}
```

---

### POST /api/v1/admin/keys

Create an API key (admin only).

**Body:**
```json
{
  "name": "Mobile App Integration",
  "permissions": ["geocode", "routing", "analytics"],
  "rateLimit": 10000
}
```

**Response:**
```json
{
  "id": "key-uuid",
  "keyHash": "hashed-for-storage",
  "createdAt": "2024-03-15T10:30:00Z",
  "publicKey": "pk_live_..."  // Only shown once
}
```

**Auth**: `adminProcedure`

---

### GET /api/v1/admin/keys

List all API keys.

**Auth**: `adminProcedure`

---

## Error Responses

### tRPC Errors

```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "User not authenticated",
    "data": {
      "code": "UNAUTHORIZED",
      "path": "teams.listMyTeams"
    }
  }
}
```

**Error Codes:**
- `UNAUTHORIZED` (401): No valid session
- `FORBIDDEN` (403): Insufficient permissions
- `NOT_FOUND` (404): Resource not found
- `BAD_REQUEST` (400): Invalid input
- `INTERNAL_SERVER_ERROR` (500): Server error

### REST API Errors

```json
{
  "error": "Invalid or missing API key"
}
```

Status codes: 400, 401, 403, 404, 500

---

## Rate Limiting

API keys have configurable rate limits (default 1000 requests/hour):

```
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 999
X-RateLimit-Reset: 1710487200
```

Exceeding limit returns 429 Too Many Requests.

---

## Pagination

Large result sets support cursor-based pagination:

```typescript
// Query
await trpc.alerts.list.query({
  limit: 20,
  cursor: "eyJpZCI6IDEyM30="  // Base64-encoded bookmark
});

// Response
{
  items: [...],
  nextCursor: "eyJpZCI6IDE0M30=",
  hasMore: true
}
```
