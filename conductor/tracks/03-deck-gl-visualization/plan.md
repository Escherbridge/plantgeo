# Track 03: deck.gl Visualization - Implementation Plan

## Phase 1: deck.gl Foundation
- [ ] Install deck.gl packages (@deck.gl/core, layers, geo-layers, mapbox, react)
- [ ] Create `DeckOverlay.tsx` with MapboxOverlay in interleaved mode
- [ ] Wire into MapView component
- [ ] Verify WebGL2 context sharing works

## Phase 2: Core Visualization Layers
- [ ] Implement HeatmapLayer for fire risk
- [ ] Implement ScatterplotLayer for sensors
- [ ] Implement GeoJsonLayer for polygons
- [ ] Create reusable layer factory pattern

## Phase 3: Advanced 3D Layers
- [ ] Implement HexagonLayer with 3D elevation
- [ ] Implement ColumnLayer for metric visualization
- [ ] Implement ArcLayer for flow data
- [ ] Add TerrainExtension for 2D-on-3D draping

## Phase 4: Animation Layers
- [ ] Implement TripsLayer for animated paths
- [ ] Add time slider control for animation
- [ ] Implement PathLayer for route display

## Phase 5: Interactivity
- [ ] Build tooltip component for hover/click
- [ ] Add click handler for feature selection
- [ ] Implement brush selection modes
- [ ] Add cross-filtering between layers

## Phase 6: Data Integration
- [ ] Hook layers to tRPC data endpoints
- [ ] Implement chunked loading for large datasets
- [ ] Add SSE streaming for real-time layer updates
- [ ] Performance optimization (picking, LOD)
