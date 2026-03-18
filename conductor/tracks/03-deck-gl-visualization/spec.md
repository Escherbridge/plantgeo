# Track 03: deck.gl Data Visualization

## Goal
Integrate deck.gl v9 with MapLibre GL JS in interleaved mode for advanced 3D data visualization including heatmaps, hexagonal bins, column charts, scatter plots, arc flows, and terrain-draped layers.

## Features
1. **Interleaved Rendering**
   - MapboxOverlay with `interleaved: true`
   - Depth-correct rendering between MapLibre layers
   - Shared WebGL2 context

2. **Visualization Layers**
   - `HeatmapLayer` - Fire risk density
   - `HexagonLayer` - Aggregated sensor readings
   - `ColumnLayer` - 3D bar charts on map (population, risk scores)
   - `ScatterplotLayer` - Point data with size/color encoding
   - `ArcLayer` - Origin-destination flows (logistics, evacuations)
   - `PathLayer` - Routes and trajectories
   - `GeoJsonLayer` - Arbitrary GeoJSON overlays
   - `IconLayer` - Custom marker icons
   - `TextLayer` - Dynamic labels
   - `TripsLayer` - Animated vehicle tracking paths

3. **Terrain Integration**
   - TerrainExtension for draping 2D layers on 3D terrain
   - Elevation-aware tooltips
   - Proper z-fighting prevention

4. **Interactivity**
   - Hover tooltips with feature properties
   - Click selection with highlight
   - Brush selection (rectangle, lasso)
   - Cross-filtering between layers

5. **Data Loading**
   - Chunked GeoJSON loading for large datasets
   - TileLayer for server-side tiled data
   - Streaming data updates via SSE

## Files to Create/Modify
- `src/components/map/DeckOverlay.tsx` - deck.gl overlay component
- `src/components/map/layers/HeatmapLayer.tsx` - Fire risk heatmap
- `src/components/map/layers/HexLayer.tsx` - Hexagonal aggregation
- `src/components/map/layers/ColumnLayer.tsx` - 3D column charts
- `src/components/map/layers/ArcLayer.tsx` - Flow visualization
- `src/components/map/layers/TripsLayer.tsx` - Animated tracking
- `src/components/map/Tooltip.tsx` - Hover/click tooltip
- `src/lib/map/deck-config.ts` - deck.gl configuration
- `src/hooks/useDeckLayers.ts` - Layer management hook

## Acceptance Criteria
- [ ] deck.gl renders interleaved with MapLibre layers
- [ ] Heatmap shows fire risk intensity correctly
- [ ] HexagonLayer aggregates sensor readings in 3D
- [ ] ArcLayer animates flow lines
- [ ] TripsLayer shows animated vehicle paths
- [ ] Tooltips show on hover/click for all layers
- [ ] Terrain draping works for 2D layers on 3D surface
- [ ] Performance: 60fps with 100K points
