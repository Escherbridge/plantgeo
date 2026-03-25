# Specification: Map Layer Data Visualization

## Overview

Get all five PlantGeo environmental map layers rendering with real or realistic demo data over Washington State. Each layer must display meaningful, visually correct data when toggled on via its panel sidebar, with proper color legends, working controls, and correct layer ordering relative to the Protomaps basemap.

## Background

The PlantGeo map currently loads with Protomaps API basemap tiles (dark/light/satellite styles). Five environmental data layers exist in code with panel UIs and layer components, but most do not visually render on the map. The Vegetation/NDVI layer partially works (renders on light style only). Water, Drought, Soil, and Fire layers have components wired to the `activeLayers` Zustand store via `LayerToggle` but produce no visual output on the map due to missing data flow, incorrect wiring, or absent source data.

**Current architecture:**
- `LayerManager.tsx` renders all layer components, gated by `activeLayers.includes("<layerId>")`
- `LayerToggle` UI component toggles layer IDs in the Zustand `map-store`
- Each layer component receives `map` (MapLibre instance) and `visible` prop
- Demo data exists in `src/lib/map/demo-data.ts` for Water gauges, Groundwater wells, Drought polygons, and Soil points
- Vegetation uses NASA GIBS WMS tile URLs built in `src/lib/vegetation.ts`
- Fire uses a deck.gl `ScatterplotLayer` factory but has no data source or integration into LayerManager

**Constraint:** No Martin tile server. Use Protomaps API for basemap, public APIs (NASA GIBS, NASA FIRMS) for live data, and demo data as fallback.

## Functional Requirements

### FR-1: Vegetation/NDVI Layer Fix
**Description:** Fix NDVI/NDWI/NBR raster tile rendering so it works on all map styles (dark, light, satellite), not just light.
**Acceptance Criteria:**
- NDVI tiles render correctly when toggled on with dark style active
- NDVI tiles render correctly when toggled on with satellite style active
- Year/month slider changes update the displayed tiles within 2 seconds
- Toggling between NDVI, NDWI, and NBR modes shows the correct raster overlay
- Opacity slider works across all modes
- Color legend in VegetationPanel matches the active raster mode
**Priority:** High

### FR-2: Water Layer Rendering
**Description:** Render USGS streamflow gauge markers and groundwater well markers on the map using demo data from `demo-data.ts`.
**Acceptance Criteria:**
- Toggling "Water Gauges" in WaterPanel shows circle markers at all 8 demo gauge locations
- Gauge circles are color-coded by condition (above_normal=blue, normal=teal, below_normal=yellow, low=orange)
- Toggling "Drought Monitor" in WaterPanel shows the drought polygons (covered by FR-3)
- Groundwater well markers (smaller circles) appear at all 5 demo well locations
- Well circles are color-coded by trend (rising=blue, stable=green, declining=orange)
- Clicking a gauge or well marker shows a popup with site name, flow/depth, condition/trend
- Panel sidebar shows the gauge list with condition badges matching map marker colors
- Panel uses demo data when tRPC queries fail (API unavailable)
**Priority:** High

### FR-3: Drought Layer Rendering
**Description:** Render USDM drought GeoJSON polygons with severity-based fill coloring.
**Acceptance Criteria:**
- Toggling "Drought Monitor" shows 4 demo drought polygons over eastern Washington
- Polygons are filled with USDM standard colors: D0=yellow, D1=tan, D2=orange, D3=red, D4=dark red
- Polygon outlines are visible with slightly darker stroke colors
- Drought Classification Legend in WaterPanel matches actual rendered colors
- Opacity adjustment works without rebuilding the layer
- Polygons render below labels but above the basemap
**Priority:** High

### FR-4: Soil Layer Redesign
**Description:** Upgrade soil visualization from scattered dots to a more informative display with property-specific coloring and a property selector that actually updates the map.
**Acceptance Criteria:**
- Toggling "Soil Properties" shows colored circles at all 8 demo soil sample locations
- Property selector buttons (pH, Organic Carbon, Nitrogen, Bulk Density, CEC) in SoilPanel change the circle colors on the map
- Each circle color matches the corresponding color ramp for the selected property
- Value labels appear below each circle showing the numeric value
- Color legend in SoilPanel updates to match the currently selected property
- When tRPC query fails, fallback to nearest demo data point without error
**Priority:** Medium

### FR-5: Fire Layer Implementation
**Description:** Add a functional fire detection/risk layer using NASA FIRMS data or static demo data, integrated into LayerManager and the FireDashboard panel.
**Acceptance Criteria:**
- Fire layer renders on the map when toggled via FireDashboard's "Fire Risk" toggle
- Fire detections appear as colored scatter points (yellow-to-red by risk score)
- Demo fire data includes at least 6 points across Washington State covering varied risk levels
- FireDashboard panel shows active fire count, and stat cards display data (using demo/fallback when APIs unavailable)
- Fire layer is registered in LayerManager with the `"landfire"` layer ID
- Fire legend is visible on the map or in the FireDashboard panel
**Priority:** Medium

### FR-6: Layer Ordering
**Description:** Ensure all data layers render in the correct visual stacking order.
**Acceptance Criteria:**
- All GeoJSON/circle layers insert before the first symbol layer (labels stay on top)
- Raster layers (NDVI, NDWI, NBR) insert before the first symbol layer
- When multiple layers are active simultaneously, they do not obscure each other completely
- Layer ordering: basemap -> raster overlays (NDVI/fire) -> polygon fills (drought) -> circles (water/soil) -> labels
**Priority:** High

### FR-7: Style Change Resilience
**Description:** Layers survive map style changes without disappearing permanently.
**Acceptance Criteria:**
- Switching from dark to light style with layers active re-adds layers after style load
- Switching from light to satellite with layers active re-adds layers after style load
- Layer toggle state persists through style changes (no phantom "on" states)
**Priority:** High

## Non-Functional Requirements

### NFR-1: Performance
- Layer toggle response time under 500ms for GeoJSON layers
- Raster tile load should not block map interaction
- No excessive re-renders when moving the map with layers active

### NFR-2: Error Handling
- All tRPC queries gracefully fall back to demo data when APIs are unavailable
- Console errors from failed API calls should not break the map
- Missing or malformed GeoJSON should be handled without throwing

### NFR-3: Accessibility
- Layer toggle switches have proper `role="switch"` and `aria-checked`
- Color legends include text labels (not color-only information)

## User Stories

### US-1: Environmental Analyst Views Water Data
**As** an environmental monitoring analyst,
**I want** to toggle on the Water layer and see streamflow gauge markers color-coded by condition,
**So that** I can quickly identify which rivers in Washington are experiencing low flow.

**Given** the map is loaded centered on Washington State
**When** I open the Water panel and toggle "Water Gauges" on
**Then** I see 8 colored circles on the map at river gauge locations
**And** clicking a gauge shows a popup with flow rate, condition, and trend

### US-2: Drought Monitor Viewer
**As** a drought response coordinator,
**I want** to see drought severity polygons overlaid on the map,
**So that** I can identify areas under extreme or exceptional drought.

**Given** the map is loaded centered on Washington State
**When** I open the Water panel and toggle "Drought Monitor" on
**Then** I see colored polygons over eastern Washington with USDM severity coloring
**And** the legend in the panel matches the polygon colors

### US-3: Soil Property Explorer
**As** an agricultural scientist,
**I want** to switch between soil properties and see the map update to show the selected property's values,
**So that** I can compare pH vs organic carbon distribution across the state.

**Given** the Soil panel is open and the soil layer is toggled on
**When** I click the "pH" property selector button
**Then** the soil sample circles on the map change color to reflect pH values
**And** the legend updates to show the pH color ramp

### US-4: Fire Risk Assessment
**As** a wildfire prevention team member,
**I want** to see fire detection points on the map with risk-based coloring,
**So that** I can prioritize areas needing immediate attention.

**Given** the map is loaded centered on Washington State
**When** I open the Fire Dashboard and toggle "Fire Risk" on
**Then** I see scatter points across Washington colored yellow-to-red by risk level

### US-5: Multi-Layer Analysis
**As** a geospatial analyst,
**I want** to enable multiple layers simultaneously (e.g., drought + water gauges + NDVI),
**So that** I can correlate environmental conditions across data types.

**Given** the map is loaded
**When** I toggle on Vegetation, Water, and Drought layers simultaneously
**Then** all three layers are visible without completely obscuring each other
**And** labels remain readable above all data layers

## Technical Considerations

1. **Style change handling:** MapLibre removes all sources/layers on style change. Each layer component must re-add its sources/layers on the `styledata` event. The current pattern of `map.once("styledata", addLayer)` is correct but may race with cleanup effects. Consider using `map.on("style.load", ...)` for more reliable re-addition.

2. **LandFireLayer source dependency:** The current `LandFireLayer` checks for `map.getSource(sourceId)` before adding the layer, but nobody adds the source. This is the root cause of fire layer not rendering. The component needs to add its own source or switch to a different data approach.

3. **WaterLayer demo data wiring:** `LayerManager` already passes `DEMO_WATER_GAUGES` and `DEMO_GROUNDWATER_WELLS` to `WaterLayer`. The component code looks correct. The issue may be that the layer is toggled off by default (`activeLayers: ["vegetation"]` in store). Need to verify the toggle actually adds `"water"` to `activeLayers`.

4. **DroughtLayer data wiring:** `LayerManager` passes `DEMO_DROUGHT_GEOJSON` to `DroughtLayer`. Component code looks correct. Same toggle issue as water.

5. **SoilLayer tRPC dependency:** SoilLayer uses `trpc.environmental.getSoilProperties.useQuery` which may fail if the server is not running. The component has fallback logic for demo data on error, but the initial render uses `buildDemoPoints` which should work independently.

6. **Fire layer architecture mismatch:** `FireRiskLayer.tsx` exports a deck.gl layer factory (`createFireRiskLayer`) but `LayerManager` renders `LandFireLayer` (a MapLibre raster layer expecting a pre-existing source). These are two different approaches. Need to decide: use deck.gl ScatterplotLayer or MapLibre native circles for fire points.

## Out of Scope

- Live WebSocket/SSE streaming of real-time sensor data
- Martin tile server integration
- PostGIS spatial queries for data
- Production-grade error boundaries
- Mobile-specific layout adaptations
- Authentication or multi-tenancy
- Time-series animation of layer data
- Custom 3D objects via Three.js for any layer

## Open Questions

1. **Fire data source:** Should the fire layer use NASA FIRMS API for real detections, or purely demo data? The FIRMS API requires an API key. For POC, demo data with realistic WA coordinates is likely sufficient.

2. **Vegetation style issue root cause:** Is the NDVI WMS tile URL failing on dark style due to CORS, tile URL format, or a layer ordering issue? Need to investigate whether `getFirstSymbolLayer` returns `undefined` on dark style (Protomaps dark style may not have symbol layers in the expected position).

3. **SoilLayer as circles vs choropleth:** The spec says "redesign from random dots to meaningful visualization (choropleth)." However, with only 8 sample points, a choropleth requires Voronoi tessellation or a grid interpolation. For POC, improved circles with property-specific coloring and value labels may be the pragmatic choice. True choropleth could be a follow-up.
