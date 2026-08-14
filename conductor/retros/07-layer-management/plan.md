# Track 07: Layer Management - Implementation Plan

## Phase 1: tRPC Setup & Layer CRUD
- [x] Set up tRPC v11 router with Next.js App Router
- [x] Create layer CRUD tRPC procedures
- [x] Add Zod validation for layer schemas
- [x] Test CRUD operations

## Phase 2: Layer Panel UI
- [x] Build LayerPanel sidebar component
- [x] Create LayerItem with toggle and opacity
- [x] Implement drag-to-reorder with dnd-kit
- [x] Add layer grouping (collapsible folders)

## Phase 3: Layer Styling
- [x] Build LayerStyler dialog component
- [x] Add color picker for fill/stroke
- [x] Create data-driven styling rule builder
- [x] Implement style presets (fire, water, etc.)

## Phase 4: Data Upload
- [x] Build LayerUpload component with drag-and-drop
- [x] Parse GeoJSON files client-side
- [x] Parse CSV with lat/lon columns
- [x] Convert KML/KMZ to GeoJSON
- [x] Store features to PostGIS via tRPC

## Phase 5: Filtering
- [x] Build LayerFilter component
- [x] Implement property filters (equals, range, contains)
- [x] Add spatial filter (within polygon)
- [x] Apply filters to MapLibre layer expressions

## Phase 6: Legend & Polish
- [x] Build auto-generated Legend component
- [x] Make legend interactive (click to filter)
- [x] Add mobile drawer/sheet for layer panel
- [x] Write tests for layer management
