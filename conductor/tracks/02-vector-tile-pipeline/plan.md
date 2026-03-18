# Track 02: Vector Tile Pipeline - Implementation Plan

## Phase 1: Martin Configuration
- [ ] Finalize martin.yaml with PostGIS auto-publish settings
- [ ] Configure sprite and font paths
- [ ] Add PMTiles source paths
- [ ] Test Martin startup and health endpoint

## Phase 2: PostGIS Tile Functions
- [ ] Create `fire_risk_tiles()` function returning MVT
- [ ] Create `sensor_tiles()` with parameter filtering
- [ ] Create `intervention_tiles()` function
- [ ] Create generalized view for low-zoom performance

## Phase 3: OSM Data Import
- [ ] Write osm2pgsql flex Lua config for roads, buildings, POIs
- [ ] Create import script that downloads PBF + runs osm2pgsql
- [ ] Run osm2pgsql-gen for low-zoom generalization
- [ ] Verify spatial indexes with EXPLAIN ANALYZE

## Phase 4: PMTiles Basemap
- [ ] Download regional Protomaps PMTiles
- [ ] Set up Cloudflare R2 bucket
- [ ] Upload PMTiles to R2
- [ ] Configure client PMTiles protocol with R2 URL

## Phase 5: Layer Definitions
- [ ] Create `src/lib/map/sources.ts` with all tile sources
- [ ] Create `src/lib/map/layers.ts` with styled layers
- [ ] Wire sources and layers into MapView
- [ ] Test layer toggling and source switching

## Phase 6: Caching
- [ ] Configure nginx tile proxy cache
- [ ] Set zoom-level-aware Cache-Control headers
- [ ] Add Redis tile caching for hot tiles
- [ ] Test cache hit rates
