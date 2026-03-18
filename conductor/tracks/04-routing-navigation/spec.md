# Track 04: Routing & Navigation

## Goal
Build a full routing and navigation system using Valhalla, providing turn-by-turn directions, isochrone analysis, distance matrices, and multi-modal route planning.

## Features
1. **Turn-by-Turn Routing**
   - Route calculation between 2+ waypoints
   - Drag-to-reroute on map
   - Alternative route suggestions
   - Step-by-step direction maneuvers
   - Route summary (distance, time, elevation)

2. **Multi-Modal Support**
   - Auto (car) routing
   - Bicycle routing (road, mountain, electric)
   - Pedestrian routing
   - Truck routing (with height/weight restrictions)
   - Transit (if GTFS data available)

3. **Isochrone Analysis**
   - Time-based reachability polygons (5, 10, 15, 30, 60 min)
   - Distance-based reachability
   - Color-coded concentric zones
   - Multiple origin points
   - Costing model selection per isochrone

4. **Distance Matrix**
   - Many-to-many time/distance calculations
   - Batch processing for logistics optimization
   - Visual matrix display

5. **Route Visualization**
   - Animated route line on map
   - Turn-by-turn markers with icons
   - Elevation profile chart
   - Traffic-aware coloring (if data available)

6. **Route Panel UI**
   - Origin/destination input with geocoding autocomplete
   - Add intermediate waypoints
   - Drag to reorder waypoints
   - Transport mode selector
   - Route options (avoid tolls, highways, ferries)
   - Print directions

## Files to Create/Modify
- `src/components/panels/RoutingPanel.tsx` - Route planning UI
- `src/components/panels/IsochonePanel.tsx` - Isochrone controls
- `src/components/map/layers/RouteLayer.tsx` - Route line on map
- `src/components/map/layers/IsochroneLayer.tsx` - Isochrone polygons
- `src/components/routing/DirectionsList.tsx` - Step-by-step directions
- `src/components/routing/ElevationProfile.tsx` - Elevation chart
- `src/components/routing/WaypointInput.tsx` - Geocoding input
- `src/lib/server/services/routing.ts` - Valhalla service (enhance)
- `src/app/api/route/route.ts` - Route API endpoint
- `src/app/api/isochrone/route.ts` - Isochrone API endpoint
- `src/app/api/matrix/route.ts` - Matrix API endpoint
- `src/stores/routing-store.ts` - Routing state

## Acceptance Criteria
- [ ] Route calculates between 2+ points with turn-by-turn directions
- [ ] Drag-to-reroute works on map
- [ ] All transport modes work (car, bike, walk, truck)
- [ ] Isochrone polygons render with color-coded time bands
- [ ] Distance matrix returns correct N×M results
- [ ] Route line animates on map with direction arrows
- [ ] Elevation profile shows altitude changes along route
- [ ] Waypoint geocoding autocomplete works
