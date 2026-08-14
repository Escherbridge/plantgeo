# Track 01: Core Map Engine - Implementation Plan

## Phase 1: Map Foundation
- [ ] Enhance `MapView.tsx` with proper style management and lifecycle
- [ ] Create `src/lib/map/styles.ts` with dark, light, satellite style specs
- [ ] Implement PMTiles basemap from Protomaps (free planet tiles)
- [ ] Add proper error handling for WebGL context loss

## Phase 2: 3D Terrain
- [ ] Configure raster-dem source with AWS Terrain Tiles
- [ ] Add terrain exaggeration to map style
- [ ] Create `TerrainControl.tsx` with exaggeration slider
- [ ] Add hillshade layer with proper blend mode

## Phase 3: Globe View
- [ ] Implement globe projection toggle
- [ ] Add atmosphere-blend zoom expression
- [ ] Create `GlobeToggle.tsx` control component
- [ ] Handle projection change animation

## Phase 4: 3D Buildings
- [ ] Add OpenMapTiles vector source for buildings
- [ ] Create fill-extrusion layer with height expressions
- [ ] Implement zoom-based opacity transition (z15-z16)
- [ ] Color buildings by height gradient

## Phase 5: Sky & Controls
- [ ] Implement setSky with day/night themes
- [ ] Create `StyleSwitcher.tsx` with preview thumbnails
- [ ] Build `MapControls.tsx` unified control panel
- [ ] Add keyboard shortcuts (R=reset, T=terrain, G=globe)

## Phase 6: Polish
- [ ] Mobile touch gesture support
- [ ] Performance profiling and optimization
- [ ] Loading states and error boundaries
- [ ] Unit tests for map state management
