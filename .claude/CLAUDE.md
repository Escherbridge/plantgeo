# PlantGeo - Open-Source 3D Geospatial Mapping Platform

## Project Overview
PlantGeo is an enterprise-grade open-source 3D mapping platform built to provide feature parity with Google Maps Platform using entirely open-source technologies.

## Tech Stack
- **Frontend**: Next.js 15 (App Router) + React 19 + TypeScript
- **3D Map Rendering**: MapLibre GL JS v5 (globe, terrain, fill-extrusion)
- **Data Visualization**: deck.gl v9 (interleaved mode with MapLibre)
- **Custom 3D**: Three.js via CustomLayerInterface
- **Tile Serving**: Martin v1.4 (Rust, PostGIS + PMTiles + MBTiles)
- **Tile Format**: PMTiles v3 on Cloudflare R2, MVT for dynamic
- **Routing**: Valhalla (multi-modal, isochrones, turn-by-turn)
- **Geocoding**: Photon (autocomplete) backed by Nominatim
- **Database**: PostgreSQL 16 + PostGIS 3.4 + TimescaleDB
- **ORM**: Drizzle ORM
- **API**: tRPC v11
- **State**: Zustand (global) + Jotai (per-layer atoms)
- **Caching**: Redis 7 (cache + pub/sub)
- **Styling**: Tailwind CSS v4
- **Deployment**: Railway Pro (multi-service) + Cloudflare R2 (tiles)

## Architecture
- `src/app/` - Next.js App Router pages
- `src/components/map/` - MapLibre GL JS components
- `src/components/ui/` - Shared UI components
- `src/components/panels/` - Sidebar panels (layers, routing, search)
- `src/stores/` - Zustand stores
- `src/lib/server/` - Server-side logic (db, services, tRPC)
- `src/lib/server/db/` - Drizzle ORM schema and queries
- `src/lib/server/services/` - Backend services (routing, geocoding, realtime)
- `infra/` - Docker configs (martin, nginx, db init scripts)
- `data/` - Local tile data, sprites, fonts

## Conventions
- Use `"use client"` only when needed (map components, interactive UI)
- Dynamic import MapLibre components with `ssr: false`
- All geospatial queries go through PostGIS, never client-side for large datasets
- Use PMTiles for basemap tiles, Martin for dynamic overlay tiles
- Redis for caching GeoJSON responses and pub/sub for real-time updates
- SSE for broadcast updates (fire alerts), WebSocket for bidirectional (tracking)

## Commands
- `npm run dev` - Start Next.js dev server
- `npm run docker:up` - Start all infrastructure services
- `npm run docker:down` - Stop infrastructure
- `npm run db:generate` - Generate Drizzle migrations
- `npm run db:migrate` - Run migrations
