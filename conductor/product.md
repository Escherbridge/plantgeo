---
type: product-context
---

# PlantGeo product context

## Vision

PlantGeo is an open-source geospatial action-network platform for exploring and
coordinating evidence-backed environmental work. Google Maps feature parity is a
long-term product direction, not a claim of present completeness or a release
commitment.

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
- PostgreSQL 16 + PostGIS
- Drizzle ORM + tRPC v11
- Zustand + Jotai
- Redis 7 (cache + pub/sub)
- Tailwind CSS v4
- Railway Pro (deployment)

## Deployment direction

Railway Pro and Cloudflare R2 are intended serving-plane components, subject to
the release gates in [`release-governance.md`](./release-governance.md). The
current product is in hardening and data-foundation work; it does not currently
promise a deployed forecast, automated intervention recommendation, or strategy
efficacy claim.

Historical capacity assumptions (not a current procurement or deployment plan):
- Next.js app: 2 GB RAM
- Martin: 2 GB RAM
- PostGIS: 8 GB RAM, 250 GB volume (until 2026-08-25 TimescaleDB held one empty hypertable; dropped as non-functional)
- Valhalla: 8 GB RAM, 50 GB volume
- Redis: 1 GB RAM
- Photon: 4 GB RAM, 50 GB volume
- PMTiles basemap: Cloudflare R2 (CDN, ~$11/10M requests)
