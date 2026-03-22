# PlantGeo Architecture Diagrams

Visual guide to PlantGeo's data flows and system architecture using Mermaid diagrams. Each diagram is kept small and focused — scroll down for higher-level rollup views.

---

## Table of Contents

- [1. High-Level System Overview](#1-high-level-system-overview)
- [2. Client Architecture](#2-client-architecture)
  - [2a. Map Rendering Pipeline](#2a-map-rendering-pipeline)
  - [2b. State Management Flow](#2b-state-management-flow)
  - [2c. Panel System](#2c-panel-system)
- [3. Server Architecture](#3-server-architecture)
  - [3a. Request Routing](#3a-request-routing)
  - [3b. tRPC Router Map](#3b-trpc-router-map)
  - [3c. Authentication Flow](#3c-authentication-flow)
- [4. Data Flows](#4-data-flows)
  - [4a. Map Tile Loading](#4a-map-tile-loading)
  - [4b. Environmental Data Query](#4b-environmental-data-query)
  - [4c. AI Regional Intelligence](#4c-ai-regional-intelligence)
  - [4d. Real-Time Streaming (SSE)](#4d-real-time-streaming-sse)
  - [4e. Fleet Tracking (WebSocket)](#4e-fleet-tracking-websocket)
  - [4f. Routing Query](#4f-routing-query)
  - [4g. Geocoding Search](#4g-geocoding-search)
- [5. Background Jobs](#5-background-jobs)
- [6. Database Schema (ERD)](#6-database-schema-erd)
  - [6a. Auth & Teams](#6a-auth--teams)
  - [6b. Geospatial Data](#6b-geospatial-data)
  - [6c. Environmental & Community](#6c-environmental--community)
  - [6d. AI Conversations](#6d-ai-conversations)
- [7. Deployment Architecture](#7-deployment-architecture)
- [8. Caching Strategy](#8-caching-strategy)
- [9. Rollup: Full Request Lifecycle](#9-rollup-full-request-lifecycle)

---

## 1. High-Level System Overview

```mermaid
graph TB
    subgraph Client["Browser"]
        UI[React 19 + Next.js 15]
        Map[MapLibre GL JS v5]
        Deck[deck.gl v9]
        Three[Three.js]
    end

    subgraph Server["Next.js Server"]
        TRPC[tRPC v11]
        REST[REST API /api/v1]
        SSE[SSE Streams]
        WS[WebSocket]
        AI[AI Endpoint]
    end

    subgraph Services["Backend Services"]
        Fire[Fire Risk]
        Water[Water Scarcity]
        Soil[Soil Health]
        Veg[Vegetation]
        Strat[Strategy Scoring]
        Alert[Alert Engine]
        Context[Regional Context]
    end

    subgraph Infra["Infrastructure"]
        PG[(PostgreSQL + PostGIS)]
        Redis[(Redis 7)]
        Martin[Martin Tile Server]
        Valhalla[Valhalla Router]
        Photon[Photon Geocoder]
        R2[Cloudflare R2]
    end

    subgraph External["External APIs"]
        NASA[NASA FIRMS/GIBS]
        USGS[USGS Water]
        USDM[US Drought Monitor]
        SoilG[SoilGrids]
        Claude[Claude API]
    end

    UI --> TRPC & REST
    UI --> SSE & WS
    UI --> AI
    Map --> Martin & R2
    TRPC --> Services
    Services --> PG & Redis
    Services --> External
    AI --> Context --> Services
    Context --> Claude
```

---

## 2. Client Architecture

### 2a. Map Rendering Pipeline

```mermaid
graph LR
    subgraph Sources["Tile Sources"]
        PMT[PMTiles on R2]
        MVT[Martin MVT]
        Raster[NASA GIBS Raster]
        Terrain[AWS Terrain]
    end

    subgraph MapLibre["MapLibre GL JS"]
        Style[Style JSON]
        Base[Base Map Layer]
        Fill[Fill-Extrusion 3D]
        Globe[Globe Projection]
        Terr[Terrain Mesh]
    end

    subgraph DeckGL["deck.gl Interleaved"]
        Scatter[ScatterplotLayer]
        Heat[HeatmapLayer]
        Path[PathLayer]
        Trips[TripsLayer]
        GeoJ[GeoJsonLayer]
    end

    subgraph ThreeJS["Three.js Custom"]
        Models[3D Models]
        Particles[Particle Effects]
    end

    PMT --> Style --> Base & Fill & Globe
    Terrain --> Terr
    MVT --> GeoJ
    Raster --> MapLibre

    Base --> Canvas[WebGL Canvas]
    DeckGL --> Canvas
    ThreeJS --> Canvas
```

### 2b. State Management Flow

```mermaid
graph TD
    subgraph Stores["Zustand Stores"]
        MapStore[map-store<br/>viewport, zoom, bearing]
        LayerStore[layer-store<br/>visibility, opacity, filters]
        RoutingStore[routing-store<br/>origin, dest, waypoints]
        AlertsStore[alerts-store<br/>unread count, list]
        AIStore[regional-intelligence-store<br/>messages, location, streaming]
        TrackingStore[tracking-store<br/>assets, positions]
        DrawingStore[drawing-store<br/>shapes, mode, vertices]
    end

    subgraph Hooks["Custom Hooks"]
        useRI[useRegionalIntelligence]
        useSSE[useSSE]
        useWS[useWebSocket]
        useDeck[useDeckLayers]
        useDraw[useDrawing]
    end

    subgraph Components["UI Components"]
        MapView[MapView]
        Panels[PanelManager]
        Layers[LayerManager]
        AlertBell[AlertBell]
        RIPanel[AI Panel]
    end

    Stores --> Hooks --> Components
    Components -->|user actions| Stores
```

### 2c. Panel System

```mermaid
graph LR
    PM[PanelManager<br/>Toolbar] -->|click| Panels

    subgraph Panels["Side Panels"]
        FD[FireDashboard]
        WP[WaterPanel]
        VP[VegetationPanel]
        SP[SoilPanel]
        CP[CommunityPanel]
        StP[StrategyPanel]
        TD[TeamDashboard]
        AD[AnalyticsDashboard]
        RI[AI Intelligence Panel]
    end

    Panels -->|tRPC queries| API[tRPC Routers]
    RI -->|SSE stream| AIRoute[/api/ai/regional-intelligence]
```

---

## 3. Server Architecture

### 3a. Request Routing

```mermaid
graph TD
    Req[Incoming Request] --> MW[Next.js Middleware]

    MW -->|/api/trpc/*| TRPC[tRPC Handler]
    MW -->|/api/v1/*| REST[REST Routes]
    MW -->|/api/ai/*| AI[AI Endpoint]
    MW -->|/api/stream/*| SSE[SSE Handler]
    MW -->|/api/ws| WS[WebSocket]
    MW -->|/api/auth/*| Auth[NextAuth]
    MW -->|/api/health| Health[Health Check]
    MW -->|/*| Pages[Next.js Pages]

    TRPC --> Routers[14 tRPC Routers]
    REST --> V1[7 REST Endpoints]
    AI --> Claude[Claude Streaming]
```

### 3b. tRPC Router Map

```mermaid
graph TD
    AppRouter[appRouter] --> layers & routing & teams & contributions
    AppRouter --> analytics & tracking & wildfire & visualization
    AppRouter --> places & environmental & community & strategy
    AppRouter --> alerts & regionalIntelligence

    subgraph "Public"
        layers[layers<br/>5 procedures]
        routing[routing<br/>4 procedures]
        places[places<br/>3 procedures]
        environmental[environmental<br/>11 procedures]
        visualization[visualization]
    end

    subgraph "Protected"
        teams[teams<br/>6 procedures]
        contributions[contributions]
        analytics[analytics<br/>5 procedures]
        tracking[tracking<br/>4 procedures]
        alerts[alerts<br/>5 procedures]
        regionalIntelligence[regionalIntel<br/>3 procedures]
    end

    subgraph "Contributor+"
        wildfire[wildfire<br/>3 procedures]
        community[community<br/>4 procedures]
        strategy[strategy<br/>4 procedures]
    end
```

### 3c. Authentication Flow

```mermaid
sequenceDiagram
    participant U as User
    participant App as Next.js
    participant NA as NextAuth
    participant DB as PostgreSQL

    U->>App: Login (Google/GitHub/Email)
    App->>NA: Authenticate
    NA->>DB: Lookup/Create User
    DB-->>NA: User Record
    NA-->>App: Session Token (JWT)
    App-->>U: Set Cookie

    Note over U,DB: Subsequent Requests
    U->>App: Request + Cookie
    App->>NA: getServerSession()
    NA-->>App: { user: { id, role } }
    App->>App: Check procedure auth level
    App-->>U: Response
```

---

## 4. Data Flows

### 4a. Map Tile Loading

```mermaid
sequenceDiagram
    participant Map as MapLibre
    participant R2 as Cloudflare R2
    participant Martin as Martin Server
    participant PG as PostGIS

    Note over Map: Base map tiles
    Map->>R2: GET basemap.pmtiles (range request)
    R2-->>Map: MVT tile bytes

    Note over Map: Dynamic overlay tiles
    Map->>Martin: GET /tiles/{layer}/{z}/{x}/{y}.mvt
    Martin->>PG: ST_AsMVT query
    PG-->>Martin: MVT binary
    Martin-->>Map: MVT tile

    Note over Map: Raster tiles
    Map->>Map: NASA GIBS / Terrain URLs
```

### 4b. Environmental Data Query

```mermaid
sequenceDiagram
    participant UI as Panel Component
    participant tRPC as tRPC Router
    participant Svc as Service Layer
    participant Redis as Redis Cache
    participant API as External API

    UI->>tRPC: environmental.getSoilProperties(lat, lon)
    tRPC->>Svc: getSoilProperties()
    Svc->>Redis: GET soil:lat:lon
    alt Cache Hit
        Redis-->>Svc: Cached data
    else Cache Miss
        Svc->>API: SoilGrids REST API
        API-->>Svc: JSON response
        Svc->>Redis: SET soil:lat:lon (TTL 7d)
    end
    Svc-->>tRPC: Soil properties
    tRPC-->>UI: { ph, organicCarbon, nitrogen, ... }
```

### 4c. AI Regional Intelligence

```mermaid
sequenceDiagram
    participant U as User
    participant Map as MapView
    participant Store as Zustand Store
    participant API as /api/ai/regional-intelligence
    participant Ctx as RegionalContext
    participant Redis as Redis
    participant Svcs as 6 Data Services
    participant Claude as Claude API

    U->>Map: Click map point
    Map->>Store: openPanel(lat, lon)
    Store->>API: POST { lat, lon }

    Note over API: Auth + Rate Limit Check
    API->>Ctx: assembleRegionalContext(lat, lon)
    Ctx->>Redis: Check geohash cache

    alt Cache Miss
        Ctx->>Svcs: Promise.allSettled(6 services)
        Note over Svcs: fire risk, strategy,<br/>soil, drought, MTBS, carbon
        Svcs-->>Ctx: Partial results
        Ctx->>Redis: Cache context (15min TTL)
    end

    API->>Claude: stream({ tools, messages })

    loop Streaming
        Claude-->>API: tool_use delta chunks
        API-->>Store: SSE event: delta
        Store-->>Map: Update panel text
    end

    Claude-->>API: Complete tool_use JSON
    API-->>Store: SSE event: done
    Store-->>Map: Render structured cards
```

### 4d. Real-Time Streaming (SSE)

```mermaid
sequenceDiagram
    participant UI as Map Component
    participant SSE as /api/stream/[layerId]
    participant Redis as Redis Pub/Sub
    participant Ingest as Ingest API

    UI->>SSE: GET (EventSource)
    SSE->>Redis: SUBSCRIBE layer:{id}

    loop External Data
        Ingest->>Redis: PUBLISH layer:{id} (GeoJSON)
        Redis-->>SSE: Message
        SSE-->>UI: event: feature-update
        UI->>UI: Update map layer
    end

    Note over SSE,UI: Heartbeat every 30s
    SSE-->>UI: event: heartbeat
```

### 4e. Fleet Tracking (WebSocket)

```mermaid
sequenceDiagram
    participant Device as GPS Device
    participant WS as /api/ws
    participant Redis as Redis Pub/Sub
    participant TS as TimescaleDB
    participant UI as Fleet Panel

    Device->>WS: position { assetId, lat, lon, speed }
    WS->>TS: INSERT tracking.positions
    WS->>Redis: PUBLISH tracking:{assetId}

    Redis-->>UI: Position update (via SSE)
    UI->>UI: Animate marker on map

    Note over WS,TS: Geofence check
    WS->>WS: ST_Contains(geofence, point)
    opt Geofence Triggered
        WS->>TS: INSERT tracking.alerts
        WS->>Redis: PUBLISH alert
    end
```

### 4f. Routing Query

```mermaid
sequenceDiagram
    participant UI as RoutingPanel
    participant tRPC as routing router
    participant V as Valhalla

    UI->>tRPC: getRoute(origin, destination, mode)
    tRPC->>V: POST /route { costing: "auto" }
    V-->>tRPC: Route geometry + maneuvers
    tRPC-->>UI: { geometry, distance, duration, steps[] }
    UI->>UI: Draw route on map + show turn-by-turn
```

### 4g. Geocoding Search

```mermaid
sequenceDiagram
    participant UI as SearchBar
    participant tRPC as places router
    participant P as Photon

    UI->>tRPC: search(query) [debounced 300ms]
    tRPC->>P: GET /api?q=query&limit=5
    P-->>tRPC: Feature[] with coordinates
    tRPC-->>UI: Autocomplete results
    UI->>UI: Show dropdown

    UI->>tRPC: reverseGeocode(lat, lon)
    tRPC->>P: GET /reverse?lat=&lon=
    P-->>tRPC: Address components
    tRPC-->>UI: Place name + address
```

---

## 5. Background Jobs

```mermaid
graph TD
    subgraph Instrumentation["instrumentation.ts (startup)"]
        Start[Next.js Server Start]
    end

    Start --> PZR & WR & AD & ED & CC

    subgraph Jobs["BullMQ Workers"]
        PZR[priority-zone-refresh<br/>Cron: 2am UTC daily<br/>DBSCAN clustering]
        WR[water-refresh<br/>Cron: periodic<br/>USGS gauge upsert]
        AD[alert-dispatcher<br/>Cron: every 30min<br/>fire + drought + streamflow]
        ED[email-digest<br/>Cron: 8am UTC daily<br/>unread alert summary]
        CC[conversation-cleanup<br/>Cron: 3am UTC daily<br/>30-day retention]
    end

    PZR --> PG[(PostGIS)]
    WR --> USGS[USGS API] --> PG
    AD --> NASA[NASA FIRMS] & USDM[Drought Monitor]
    AD --> Email[Email Service]
    ED --> Email
    CC --> PG
```

---

## 6. Database Schema (ERD)

### 6a. Auth & Teams

```mermaid
erDiagram
    users ||--o{ sessions : has
    users ||--o{ accounts : has
    users ||--o{ team_members : joins
    teams ||--o{ team_members : has
    users ||--o{ api_keys : owns
    teams ||--o{ api_keys : owns

    users {
        uuid id PK
        text email UK
        text password_hash
        varchar platform_role
        boolean verified
    }

    teams {
        uuid id PK
        text name
        varchar slug UK
        varchar org_type
        jsonb specialties
        jsonb service_area
    }

    team_members {
        uuid team_id FK
        uuid user_id FK
        varchar team_role
    }

    api_keys {
        uuid id PK
        text key_hash
        uuid user_id FK
        uuid team_id FK
        integer rate_limit
    }
```

### 6b. Geospatial Data

```mermaid
erDiagram
    layers ||--o{ features : contains
    teams ||--o{ layers : owns

    layers {
        uuid id PK
        varchar name UK
        varchar type
        jsonb style
        uuid team_id FK
        boolean is_public
    }

    features {
        uuid id PK
        uuid layer_id FK
        jsonb properties
        geometry geom
        varchar status
    }

    fire_detections {
        uuid id PK
        timestamp detected_at
        real brightness
        real frp
        geometry geom
    }

    poi {
        uuid id PK
        text name
        varchar category
        geometry geom
    }

    assets ||--o{ positions : tracks
    assets {
        uuid id PK
        varchar name
        varchar type
        varchar status
    }

    positions {
        timestamp time
        uuid asset_id FK
        double speed
        geometry geom
    }
```

### 6c. Environmental & Community

```mermaid
erDiagram
    users ||--o{ strategy_requests : creates
    users ||--o{ request_votes : casts
    strategy_requests ||--o{ request_votes : receives
    users ||--o{ watched_locations : monitors
    users ||--o{ alert_subscriptions : configures
    users ||--o{ environmental_alerts : receives

    strategy_requests {
        uuid id PK
        varchar strategy_type
        text title
        double lat
        double lon
        integer vote_count
    }

    priority_zones {
        uuid id PK
        varchar strategy_type
        integer request_count
        jsonb geojson
    }

    watched_locations {
        uuid id PK
        uuid user_id FK
        double lat
        double lon
        integer radius_km
    }

    environmental_alerts {
        uuid id PK
        uuid user_id FK
        varchar alert_type
        varchar severity
        text title
        boolean is_read
    }

    water_gauges {
        uuid id PK
        varchar site_no UK
        double flow_cfs
        varchar condition
    }
```

### 6d. AI Conversations

```mermaid
erDiagram
    users ||--o{ ai_conversations : creates
    ai_conversations ||--o{ ai_messages : contains

    ai_conversations {
        uuid id PK
        uuid user_id FK
        varchar geohash
        double lat
        double lon
        varchar title
        integer message_count
        timestamp updated_at
    }

    ai_messages {
        uuid id PK
        uuid conversation_id FK
        varchar role
        text content
        jsonb structured_response
        integer token_count
    }
```

---

## 7. Deployment Architecture

```mermaid
graph TB
    subgraph Railway["Railway Pro"]
        subgraph Web["Web Service"]
            Next[Next.js 15<br/>Standalone Node.js]
        end
        subgraph DB["Database"]
            PG[(PostgreSQL 16<br/>+ PostGIS 3.4<br/>+ TimescaleDB)]
        end
        subgraph Cache["Cache"]
            Redis[(Redis 7<br/>Cache + Pub/Sub<br/>+ BullMQ)]
        end
        subgraph Tiles["Tile Server"]
            Martin[Martin v1.4<br/>PostGIS + PMTiles]
        end
        subgraph Router["Routing"]
            Val[Valhalla<br/>Multi-modal]
        end
        subgraph Geo["Geocoder"]
            Phot[Photon<br/>Nominatim]
        end
    end

    subgraph CDN["Cloudflare"]
        R2[R2 Object Storage<br/>PMTiles basemap]
    end

    subgraph Ext["External"]
        Claude[Claude API]
        FIRMS[NASA FIRMS]
        USGS[USGS NWIS]
    end

    Browser[Browser] --> Next
    Browser --> R2
    Next --> PG & Redis
    Next --> Martin & Val & Phot
    Next --> Claude & FIRMS & USGS
    Martin --> PG
```

---

## 8. Caching Strategy

```mermaid
graph LR
    subgraph "Cache Tiers"
        direction TB
        L1["Browser Cache<br/>PMTiles range requests<br/>Static assets (immutable)"]
        L2["CDN Cache<br/>Cloudflare R2<br/>Tile responses"]
        L3["Redis Cache<br/>API responses<br/>Session data<br/>Rate limits"]
    end

    subgraph "TTLs by Data Type"
        direction TB
        T1["15 min<br/>AI context, location-context"]
        T2["30 min<br/>Fire detections"]
        T3["6 hours<br/>Drought data, USGS gauges"]
        T4["24 hours<br/>MTBS perimeters, LandFire"]
        T5["7 days<br/>SoilGrids, NDVI monthly"]
    end

    L1 --> L2 --> L3
```

---

## 9. Rollup: Full Request Lifecycle

This diagram shows the complete lifecycle of a user interaction from map click to rendered AI response.

```mermaid
graph TD
    Click[User Clicks Map] --> Store[Zustand Store<br/>openPanel]
    Store --> Auth{Authenticated?}
    Auth -->|No| Login[Redirect to Login]
    Auth -->|Yes| Rate{Rate Limit OK?}
    Rate -->|No| Error429[Show 429 Error]
    Rate -->|Yes| Cache{Context Cached?}

    Cache -->|Hit| Prompt[Build Prompt]
    Cache -->|Miss| Fetch[Parallel Fetch]

    subgraph Fetch["Promise.allSettled"]
        F1[Fire Risk + FWI]
        F2[Strategy Scores]
        F3[Soil Properties]
        F4[Drought + Gauges]
        F5[MTBS Perimeters]
        F6[Carbon Potential]
    end

    Fetch --> CacheWrite[Write Redis<br/>15min TTL]
    CacheWrite --> Prompt

    Prompt --> Claude[Claude API<br/>tool_use stream]
    Claude --> SSE[SSE Events]

    subgraph SSE["Streaming Response"]
        E1[event: context]
        E2[event: delta ...]
        E3[event: done]
    end

    SSE --> Panel[RegionalIntelligencePanel]

    subgraph Panel["Rendered Output"]
        P1[Risk Summary Card]
        P2[Historical Events]
        P3[Actionable Items]
        P4[Intervention Cards]
        P5[Data Freshness]
    end

    Panel --> Persist[Save to DB<br/>ai_conversations]
    Panel --> FollowUp[Follow-up Chat Input]
    FollowUp --> Prompt
```

---

## Diagram Legend

| Symbol | Meaning |
|--------|---------|
| Rectangle | Process or component |
| Cylinder | Database or persistent store |
| Diamond | Decision point |
| Arrows | Data flow direction |
| Dashed box | Logical grouping |
| `PK` / `FK` / `UK` | Primary key / Foreign key / Unique key |
