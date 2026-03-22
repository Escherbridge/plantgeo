# Track 04: Routing & Navigation - Implementation Plan

## Phase 1: Valhalla Integration
- [x] Enhance routing service with full Valhalla API coverage
- [x] Create API routes for /route, /isochrone, /matrix
- [x] Add request validation with Zod schemas
- [x] Handle Valhalla error responses gracefully

## Phase 2: Route Visualization
- [x] Create RouteLayer with animated line
- [x] Add turn markers with maneuver icons
- [x] Implement route fit-to-bounds
- [x] Add direction arrows along route line

## Phase 3: Routing Panel UI
- [x] Build WaypointInput with geocoding autocomplete
- [x] Create transport mode selector (car/bike/walk/truck)
- [x] Add waypoint drag-to-reorder
- [x] Build route options (avoid tolls/highways/ferries)
- [x] Create routing state store

## Phase 4: Directions & Profile
- [x] Build DirectionsList with step-by-step maneuvers
- [x] Create ElevationProfile chart component
- [x] Add route summary (distance, time, elevation gain)
- [x] Implement print-friendly directions view

## Phase 5: Isochrone Analysis
- [x] Build IsochronePanel with time/distance controls
- [x] Create IsochroneLayer with color-coded polygons
- [x] Support multiple origin points
- [x] Add costing model per isochrone

## Phase 6: Advanced Features
- [x] Implement drag-to-reroute on map
- [x] Add alternative route display
- [x] Build distance matrix UI
- [x] Add route sharing via URL parameters
