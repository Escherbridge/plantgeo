# PlantGeo - Product Requirements Document

## Vision
An enterprise-grade open-source 3D geospatial mapping platform that provides full feature parity with Google Maps Platform, built on open-source technologies (MapLibre, PostGIS, Valhalla, Pelias/Photon), deployed on Railway Pro with Cloudflare R2 for tile distribution.

## Target Users
- Wildfire prevention teams needing real-time geospatial intelligence
- Environmental monitoring organizations tracking ecosystem interventions
- Logistics and fleet management operations needing routing + tracking
- Developers building location-based applications without Google Maps lock-in

## Core Value Propositions
1. **3D Immersive Maps** - Globe view, terrain, 3D buildings, custom 3D objects
2. **Full Routing Suite** - Turn-by-turn, isochrones, distance matrices, multi-modal
3. **Real-Time Intelligence** - Live data streaming, sensor feeds, fire perimeter updates
4. **Enterprise Search** - Geocoding, autocomplete, POI search, reverse geocoding
5. **Data Platform** - Multi-layer management, time-series tracking, analytics
6. **Zero Lock-In** - 100% open-source stack, self-hostable, Railway-deployable

## Tech Stack
- Next.js 15 + React 19 + TypeScript
- MapLibre GL JS v5 + deck.gl v9 + Three.js
- Martin v1.4 (Rust tile server)
- PMTiles v3 + Cloudflare R2
- Valhalla (routing) + Photon (geocoding)
- PostgreSQL 16 + PostGIS + TimescaleDB
- Drizzle ORM + tRPC v11
- Zustand + Jotai
- Redis 7 (cache + pub/sub)
- Tailwind CSS v4
- Railway Pro (deployment)

## Deployment Target
Railway Pro plan (~$240-400/month):
- Next.js app: 2 GB RAM
- Martin: 2 GB RAM
- PostGIS + TimescaleDB: 8 GB RAM, 250 GB volume
- Valhalla: 8 GB RAM, 50 GB volume
- Redis: 1 GB RAM
- Photon: 4 GB RAM, 50 GB volume
- PMTiles basemap: Cloudflare R2 (CDN, ~$11/10M requests)
