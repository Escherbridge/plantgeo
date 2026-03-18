# Track 07: Layer Management - Implementation Plan

## Phase 1: tRPC Setup & Layer CRUD
- [ ] Set up tRPC v11 router with Next.js App Router
- [ ] Create layer CRUD tRPC procedures
- [ ] Add Zod validation for layer schemas
- [ ] Test CRUD operations

## Phase 2: Layer Panel UI
- [ ] Build LayerPanel sidebar component
- [ ] Create LayerItem with toggle and opacity
- [ ] Implement drag-to-reorder with dnd-kit
- [ ] Add layer grouping (collapsible folders)

## Phase 3: Layer Styling
- [ ] Build LayerStyler dialog component
- [ ] Add color picker for fill/stroke
- [ ] Create data-driven styling rule builder
- [ ] Implement style presets (fire, water, etc.)

## Phase 4: Data Upload
- [ ] Build LayerUpload component with drag-and-drop
- [ ] Parse GeoJSON files client-side
- [ ] Parse CSV with lat/lon columns
- [ ] Convert KML/KMZ to GeoJSON
- [ ] Store features to PostGIS via tRPC

## Phase 5: Filtering
- [ ] Build LayerFilter component
- [ ] Implement property filters (equals, range, contains)
- [ ] Add spatial filter (within polygon)
- [ ] Apply filters to MapLibre layer expressions

## Phase 6: Legend & Polish
- [ ] Build auto-generated Legend component
- [ ] Make legend interactive (click to filter)
- [ ] Add mobile drawer/sheet for layer panel
- [ ] Write tests for layer management
