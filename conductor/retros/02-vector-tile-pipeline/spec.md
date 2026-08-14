# Track 02: Vector Tile Pipeline

## Goal
Set up the complete vector tile infrastructure: Martin tile server connected to PostGIS, PMTiles basemap from Cloudflare R2, and the data import pipeline for OpenStreetMap data.

## Features
1. **Martin Tile Server Configuration**
   - Auto-discover PostGIS tables with geometry columns
   - Function sources for dynamic tile generation
   - Sprite and font serving
   - Health check endpoint
   - Connection pooling (20 connections)

2. **PMTiles Basemap Setup**
   - Download regional Protomaps basemap PMTiles
   - Configure Cloudflare R2 bucket for hosting
   - Client-side PMTiles protocol for direct R2 access
   - Fallback to Martin-served tiles

3. **PostGIS Data Import**
   - osm2pgsql flex configuration for custom schema
   - Import script for regional OSM PBF extracts
   - Generalized tables for low-zoom tile performance
   - Spatial index creation and optimization

4. **Custom Tile Functions**
   - `fire_risk_tiles(z, x, y)` - Dynamic fire risk heatmap
   - `sensor_tiles(z, x, y, params)` - Filtered sensor data
   - `intervention_tiles(z, x, y)` - Ecosystem actions

5. **Tile Caching**
   - Nginx proxy cache for Martin tiles
   - Cache-Control headers by zoom level
   - Redis cache for frequently accessed tiles
   - Cache invalidation on data updates

## Files to Create/Modify
- `infra/martin/martin.yaml` - Martin configuration (enhance)
- `infra/db/init/02-tile-functions.sql` - PostGIS tile functions
- `infra/db/import/osm-flex-config.lua` - osm2pgsql flex config
- `scripts/import-osm.sh` - OSM data import automation
- `scripts/setup-pmtiles.sh` - PMTiles download and R2 upload
- `src/lib/map/sources.ts` - Tile source definitions
- `src/lib/map/layers.ts` - Layer definitions for all tile sources

## Acceptance Criteria
- [ ] Martin serves tiles from PostGIS tables at /table/{z}/{x}/{y}
- [ ] PMTiles basemap loads from R2 with pmtiles:// protocol
- [ ] OSM data imports successfully with spatial indexes
- [ ] Custom tile functions return valid MVT
- [ ] Nginx caches tiles with proper TTLs
- [ ] Tile load time < 200ms at zoom 10
