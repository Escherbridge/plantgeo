# Track 07: Layer Management System

## Goal
Build a comprehensive layer management system allowing users to add, configure, toggle, style, and organize geospatial data layers with full CRUD operations.

## Features
1. **Layer Panel**
   - Collapsible sidebar with layer tree
   - Drag-to-reorder layer stacking
   - Toggle visibility per layer
   - Opacity slider per layer
   - Layer grouping (folders)

2. **Layer Types**
   - Vector tile layers (from Martin/PostGIS)
   - GeoJSON upload layers
   - Raster tile layers (satellite, terrain)
   - Heatmap layers
   - deck.gl visualization layers

3. **Layer Styling**
   - Color picker for fill/stroke
   - Line width controls
   - Symbol/icon selector
   - Data-driven styling rules (color by property)
   - Style presets (fire, water, vegetation, infrastructure)

4. **Layer CRUD**
   - Create new layers with metadata
   - Upload GeoJSON/CSV/KML data
   - Edit layer properties
   - Delete layers with confirmation
   - Duplicate layers

5. **Layer Filtering**
   - Property-based filters (equals, contains, range)
   - Spatial filters (within polygon, near point)
   - Time range filters
   - Combined filter expressions

6. **Legend**
   - Auto-generated legend from style
   - Color gradient for data-driven layers
   - Symbol legend for icon layers
   - Interactive legend (click to filter)

## Files to Create/Modify
- `src/components/panels/LayerPanel.tsx` - Layer management sidebar
- `src/components/panels/LayerItem.tsx` - Individual layer row
- `src/components/panels/LayerStyler.tsx` - Style editing dialog
- `src/components/panels/LayerFilter.tsx` - Filter builder
- `src/components/panels/LayerUpload.tsx` - File upload dialog
- `src/components/map/Legend.tsx` - Map legend overlay
- `src/lib/map/layer-styles.ts` - Style presets and generators
- `src/lib/server/services/layers.ts` - Layer CRUD service
- `src/app/api/trpc/[trpc]/route.ts` - tRPC router
- `src/lib/server/trpc/routers/layers.ts` - Layer tRPC router
- `src/stores/layer-store.ts` - Layer state management

## Acceptance Criteria
- [ ] Layer panel shows all layers with toggle/opacity controls
- [ ] Drag-to-reorder changes layer stacking on map
- [ ] GeoJSON/CSV/KML upload creates new layer
- [ ] Style editor changes layer appearance in real-time
- [ ] Property filters correctly filter displayed features
- [ ] Legend auto-generates from layer styles
- [ ] Layer CRUD operations persist to database
- [ ] Mobile-friendly layer panel (drawer/sheet)
