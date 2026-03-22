# PlantGeo Documentation

Welcome to PlantGeo, an enterprise-grade open-source 3D geospatial mapping platform. This documentation covers all aspects of the system architecture, deployment, and development.

## Quick Start

### Clone and Install

```bash
git clone https://github.com/plantgeo/plantgeo.git
cd plantgeo
npm install
```

### Configure Environment

```bash
cp .env.example .env.local
# Edit .env.local with your local values
```

### Start Infrastructure

```bash
npm run docker:up
npm run db:migrate
```

### Run Development Server

```bash
npm run dev
```

The application will be available at `http://localhost:3000`.

## Project Overview

PlantGeo is an open-source alternative to Google Maps Platform, providing:

- **3D Mapping**: Globe rendering, terrain, and fill-extrusion using MapLibre GL JS v5
- **Data Visualization**: 30+ visualization layers via deck.gl v9 (scatter, heatmaps, trips, paths)
- **Routing**: Multi-modal routing, isochrones, and distance matrices via Valhalla
- **Geocoding**: Address autocomplete and reverse geocoding via Photon
- **Real-Time Data**: SSE for alerts, WebSocket for tracking and live data
- **Geospatial Queries**: PostGIS 3.4 with TimescaleDB for time-series data
- **AI Intelligence**: Regional context and environmental analysis via Claude API
- **Team Collaboration**: Multi-tenant with role-based access control

## Documentation Index

### Core Documentation

- **[Architecture Diagrams](./diagrams.md)** — Mermaid diagrams of all data flows, ERDs, and system architecture
- **[Architecture](./architecture.md)** — System architecture, data flows, real-time systems, AI pipeline
- **[API Reference](./api-reference.md)** — Complete tRPC router and REST API documentation
- **[Services](./services.md)** — All backend services (fire, water, vegetation, soil, etc.)
- **[Database](./database.md)** — PostgreSQL schema, tables, relationships, PostGIS columns
- **[Database Schema (DBML)](./schema.dbml)** — DBML file for [dbdiagram.io](https://dbdiagram.io) visualization
- **[Components](./components.md)** — Frontend components (map, panels, layers), stores, hooks

### Deployment & Operations

- **[Deployment](./deployment.md)** — Railway deployment, multi-service setup, scaling, monitoring
- **[Environment Variables](./env-vars.md)** — Complete reference with defaults and how to obtain keys

### Tech Stack Summary

| Layer | Technology | Version |
|-------|-----------|---------|
| Frontend | Next.js (App Router) | 15 |
| Rendering | React + MapLibre GL JS | 19 / 5.x |
| 3D Visualization | Three.js + deck.gl | - / 9.x |
| Routing | Valhalla | 1.x |
| Geocoding | Photon (Nominatim) | - |
| Tile Serving | Martin (Rust + PostGIS) | 1.4 |
| Tile Format | PMTiles | v3 |
| Database | PostgreSQL + PostGIS + TimescaleDB | 16 / 3.4 / - |
| ORM | Drizzle ORM | - |
| API | tRPC | 11 |
| State Management | Zustand (global) + Jotai (per-layer) | - / - |
| Caching | Redis | 7 |
| Styling | Tailwind CSS | 4 |
| Deployment | Railway Pro | - |
| Object Storage | Cloudflare R2 | - |
| AI | Anthropic Claude API | - |

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     CLIENT (Browser)                         │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │ React 19 + MapLibre GL JS v5 (globe, terrain)           │ │
│ │ deck.gl v9 (30+ layers: scatter, heatmap, trips, etc)  │ │
│ │ Three.js (custom 3D objects)                            │ │
│ │ Zustand stores (layer, routing, tracking, etc)         │ │
│ └──────────────────────────────────────────────────────────┘ │
└────────┬────────────────────────────────────────────┬────────┘
         │ tRPC / REST API / WebSocket / SSE          │
         │                                             │
┌────────▼─────────────────────────────────────────────▼──────┐
│                   Next.js 15 (Server)                        │
│ ┌───────────────────────────────────────────────────────┐   │
│ │ tRPC Routers                                          │   │
│ │ • layers, routing, teams, contributions              │   │
│ │ • wildfire, analytics, tracking                      │   │
│ │ • environmental, strategy, alerts                    │   │
│ │ • places, visualization, community                   │   │
│ │ • regional-intelligence                             │   │
│ └───────────────────────────────────────────────────────┘   │
│ ┌───────────────────────────────────────────────────────┐   │
│ │ REST API Routes (/api/v1, /api/stream, etc)          │   │
│ │ • Geocoding, reverse geocoding                       │   │
│ │ • SSE streams for alerts                             │   │
│ │ • Ingest endpoints (FIRMS, sensors, weather)        │   │
│ │ • Data export endpoints                              │   │
│ └───────────────────────────────────────────────────────┘   │
│ ┌───────────────────────────────────────────────────────┐   │
│ │ Backend Services (30+)                               │   │
│ │ • Fire: nasa-firms, landfire, mtbs, fire-weather    │   │
│ │ • Water: usgs-water, hydrosheds, drought            │   │
│ │ • Vegetation: vegetation, nlcd, biomass             │   │
│ │ • Soil: soilgrids, usda-soil, usle                  │   │
│ │ • Analytics: carbon-potential, strategy-scoring     │   │
│ │ • Alerts: alert-engine, geofence, email             │   │
│ │ • Real-time: tracking, realtime, ai-prompt          │   │
│ └───────────────────────────────────────────────────────┘   │
└────────┬────────────────────────────┬──────────────────┬─────┘
         │                            │                  │
    ┌────▼──────────┐  ┌─────────────▼─────────────┐  ┌─▼────────────────┐
    │  PostgreSQL   │  │   External APIs           │  │  Cloudflare R2   │
    │  16 + PostGIS │  │ • NASA FIRMS              │  │  (PMTiles)       │
    │  + TimescaleDB│  │ • Nominatim/Photon       │  │                  │
    │               │  │ • Valhalla               │  │  Martin (tiles)  │
    │  Martin v1.4  │  │ • NOAA Weather           │  │  Redis Cache     │
    │  (tile server)│  │ • PlantCommerce API      │  │  Cloudflare CDN  │
    └───────────────┘  │ • Anthropic Claude       │  └──────────────────┘
                       │ • Mapillary              │
                       │ • USGS, NOAA, etc       │
                       └──────────────────────────┘
```

## Key Features by Track

| Feature | Status | Docs |
|---------|--------|------|
| Vector Tile Pipeline | Active | Track 02 |
| Routing & Navigation | Active | Track 04 |
| Real-Time Data Streaming | Active | Track 06 |
| Layer Management | Active | Track 07 |
| 3D Objects (Three.js) | Active | Track 08 |
| Drawing & Measurement | Active | Track 09 |
| Offline & PWA | Active | Track 11 |
| Authentication & Multi-Tenancy | Active | Track 12 |
| Analytics Dashboard | Active | Track 13 |
| Fleet Tracking | Active | Track 14 |
| Street View Imagery | Active | Track 16 |
| Railway Deployment | Active | Track 18 |
| Testing & Quality | Active | Track 19 |
| Embed API | Active | Track 20 |
| Wildfire Enhancement | In Progress | Track 21 |
| Water Scarcity | In Progress | Track 22 |
| Vegetation & Land Cover | In Progress | Track 23 |
| Soil Health | In Progress | Track 24 |
| Community Strategy Requests | In Progress | Track 25 |
| Strategy Cards | In Progress | Track 26 |
| Team Organization Pages | In Progress | Track 27 |
| PlantCommerce Integration | In Progress | Track 28 |
| Environmental Alerts | In Progress | Track 29 |
| Environmental Analytics | In Progress | Track 30 |
| AI Regional Intelligence | In Progress | Track 31 |

## Getting Help

### Repository Structure

```
plantgeo/
├── docs/                          # This documentation
├── src/
│   ├── app/                       # Next.js App Router
│   │   ├── api/                   # REST API routes
│   │   ├── dashboard/             # Analytics dashboard
│   │   ├── docs/                  # Documentation UI
│   │   ├── embed/                 # Embed API
│   │   └── ...pages
│   ├── components/
│   │   ├── map/                   # Map components & layers
│   │   ├── panels/                # UI panels (layer, routing, etc)
│   │   └── ui/                    # Shared UI components
│   ├── hooks/                     # Custom React hooks
│   ├── stores/                    # Zustand state stores
│   ├── lib/
│   │   ├── map/                   # Map utilities (drawing, measurement)
│   │   ├── server/
│   │   │   ├── trpc/              # tRPC routers & init
│   │   │   ├── services/          # 30+ backend services
│   │   │   ├── db/                # Drizzle schema & migrations
│   │   │   ├── jobs/              # Background jobs
│   │   │   ├── middleware/        # Auth & API key middleware
│   │   │   └── auth.ts            # NextAuth.js config
│   │   ├── offline/               # PWA & offline sync
│   │   └── export/                # Data export formats
│   ├── styles/                    # Global styles
│   └── middleware.ts              # Next.js middleware
├── infra/
│   └── railway/                   # Railway deployment configs
├── conductor/                     # Task planning & tracking
├── Dockerfile                     # Multi-stage build
├── railway.json                   # Railway.app config
├── package.json                   # Dependencies
└── .env.example                   # Environment template
```

### Common Commands

```bash
# Development
npm run dev              # Start dev server
npm run build            # Build for production
npm run start            # Start production server

# Database
npm run db:generate     # Generate Drizzle migrations
npm run db:migrate      # Run pending migrations
npm run db:push         # Push schema changes (sync mode)

# Docker
npm run docker:up       # Start all services (PostgreSQL, Redis, Martin, Valhalla)
npm run docker:down     # Stop all services

# Testing
npm run test            # Run tests
npm run test:watch      # Watch mode
npm run lint            # Lint code
```

## Monitoring & Observability

- **Health Check**: GET `/api/health` returns service status
- **Logs**: Docker logs available via `docker logs <container>`
- **Database**: Connect to PostgreSQL for query inspection
- **Redis**: Use `redis-cli` to inspect cache and pub/sub
- **API Metrics**: tRPC middleware logs all RPC calls with timing

## Contributing

See the project's CLAUDE.md for architecture conventions and guidelines.

## License

PlantGeo is open-source. See LICENSE for details.
