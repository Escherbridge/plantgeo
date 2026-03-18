# Track 04: Routing & Navigation - Implementation Plan

## Phase 1: Valhalla Integration
- [ ] Enhance routing service with full Valhalla API coverage
- [ ] Create API routes for /route, /isochrone, /matrix
- [ ] Add request validation with Zod schemas
- [ ] Handle Valhalla error responses gracefully

## Phase 2: Route Visualization
- [ ] Create RouteLayer with animated line
- [ ] Add turn markers with maneuver icons
- [ ] Implement route fit-to-bounds
- [ ] Add direction arrows along route line

## Phase 3: Routing Panel UI
- [ ] Build WaypointInput with geocoding autocomplete
- [ ] Create transport mode selector (car/bike/walk/truck)
- [ ] Add waypoint drag-to-reorder
- [ ] Build route options (avoid tolls/highways/ferries)
- [ ] Create routing state store

## Phase 4: Directions & Profile
- [ ] Build DirectionsList with step-by-step maneuvers
- [ ] Create ElevationProfile chart component
- [ ] Add route summary (distance, time, elevation gain)
- [ ] Implement print-friendly directions view

## Phase 5: Isochrone Analysis
- [ ] Build IsochronePanel with time/distance controls
- [ ] Create IsochroneLayer with color-coded polygons
- [ ] Support multiple origin points
- [ ] Add costing model per isochrone

## Phase 6: Advanced Features
- [ ] Implement drag-to-reroute on map
- [ ] Add alternative route display
- [ ] Build distance matrix UI
- [ ] Add route sharing via URL parameters
