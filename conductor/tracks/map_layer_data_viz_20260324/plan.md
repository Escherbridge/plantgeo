# Implementation Plan: Map Layer Data Visualization

## Overview

This plan brings all five environmental data layers (Vegetation, Water, Drought, Soil, Fire) to a visually functional state over Washington State. Work is organized into 5 phases, each focused on one layer, plus a final integration phase. Phases 1-3 are highest priority (layers with existing code that just needs fixing). Phases 4-5 require more rework. Phase 6 ties everything together.

**Estimated total effort:** 12-18 hours across 6 phases.

---

## Phase 1: Vegetation/NDVI Layer Fix

Goal: Make NDVI/NDWI/NBR raster tiles render correctly on all map styles (dark, light, satellite), with working year/month/mode controls.

Tasks:
- [x] Task 1.1: Investigate and fix style-dependent rendering bug -- extracted `getFirstSymbolLayer` to shared `layer-utils.ts` with null-safe fallback when style has no layers
- [x] Task 1.2: Add style change resilience to VegetationLayer -- rewrote to use `map.on("style.load", ...)` for persistent re-addition across style switches; consolidated all three raster layers (NDVI/NDWI/NBR) into a single `addAllLayers` callback
- [x] Task 1.3: Verify year/month slider updates trigger tile URL refresh -- confirmed: dedicated update effect with deps `[map, year, month, ndviMode, mode, showNDWI, opacity, visible]` calls `setTiles` on source changes
- [x] Task 1.4: Verify NDVI/NDWI/NBR mode switching sets correct opacity values -- confirmed: update effect sets opacity to 0 for inactive modes and to `opacity` prop for active mode
- [ ] Verification: Toggle vegetation layer on dark style, satellite style, and light style. Adjust year slider and month slider. Toggle NDWI checkbox. Confirm all three raster modes render tiles. [checkpoint marker]

---

## Phase 2: Water Layer Rendering

Goal: Render USGS gauge circles and groundwater well circles on the map using demo data, with click popups and proper color coding.

Tasks:
- [x] Task 2.1: Verify WaterLayer renders with demo data when toggled on -- refactored with `buildGaugeGeoJSON`/`buildWellGeoJSON` helpers; confirmed demo data flows through LayerManager
- [x] Task 2.2: Wire WaterPanel to fall back to demo data when tRPC queries fail -- WaterPanel now imports `DEMO_WATER_GAUGES` and `DEMO_DROUGHT_GEOJSON`, uses them when `isError=true`
- [x] Task 2.3: Add demo data display in WaterPanel streamflow tab -- error message updated to amber "showing demo data" instead of red error; demo gauges render in list with condition badges
- [x] Task 2.4: Verify gauge and well click popups work -- confirmed: click handlers create Popup with site name, flow/depth, condition badge, USGS site number
- [x] Task 2.5: Add style change resilience to WaterLayer -- rewrote with `map.on("style.load", ...)` pattern; consolidated gauge, well, and watershed layers into `addAllLayers` callback
- [ ] Verification: Open WaterPanel, toggle "Water Gauges" on. Confirm 8 colored circles appear on map. Click a gauge to see popup. Toggle off, confirm markers disappear. Switch map style with layer on, confirm markers return. [checkpoint marker]

---

## Phase 3: Drought Layer Rendering

Goal: Render USDM drought polygons with severity coloring from demo GeoJSON data.

Tasks:
- [x] Task 3.1: Verify DroughtLayer renders demo GeoJSON polygons -- confirmed: uses shared `getFirstSymbolLayer`, renders fill + outline layers with DM 0-4 color match expression
- [x] Task 3.2: Wire WaterPanel drought tab to show demo data on API failure -- WaterPanel falls back to `DEMO_DROUGHT_GEOJSON.features` when `droughtQuery.isError=true`; shows amber info message
- [x] Task 3.3: Verify drought polygon layer ordering -- confirmed: `beforeId` from `getFirstSymbolLayer` ensures polygons render below labels
- [x] Task 3.4: Add style change resilience to DroughtLayer -- rewrote with `map.on("style.load", ...)` pattern consistent with Vegetation and Water layers
- [ ] Verification: Open WaterPanel, switch to Drought tab, toggle "Drought Monitor" on. Confirm 4 colored polygons appear over eastern Washington. Verify colors match legend (yellow, tan, orange, red). Switch map style, confirm polygons persist. [checkpoint marker]

---

## Phase 4: Soil Layer Enhancement

Goal: Make soil layer property selector functional, with circles that re-color when the user switches between pH, Organic Carbon, Nitrogen, Bulk Density, and CEC.

Tasks:
- [ ] Task 4.1: Wire SoilPanel property selector to SoilLayer (TDD: Write test that clicking a property button in SoilPanel calls `onPropertyChange` callback; write test that SoilLayer rebuilds GeoJSON features with new colors when `property` prop changes)
- [ ] Task 4.2: Connect SoilPanel `onPropertyChange` through to LayerManager (TDD: Write test that LayerManager passes `property` and `onPropertyChange` props to SoilLayer; implement state management — either lift state to a Zustand soil store or use LayerManager local state)
- [ ] Task 4.3: Improve soil circle visualization with value labels (TDD: Write test that soil layer creates both a circle layer and a symbol layer with text-field showing rounded values; verify existing code already does this — confirm labels render on dark style)
- [ ] Task 4.4: Add style change resilience to SoilLayer (TDD: Write test verifying soil source/layers are re-added after style change; apply same pattern)
- [ ] Task 4.5: Verify SoilLayer tRPC fallback to demo data (TDD: Write test that when `soilQuery.isError=true`, the nearest demo point data is provided via `onPopupData` callback; verify existing fallback logic works)
- [ ] Verification: Open SoilPanel, toggle "Soil Properties" on. Confirm 8 colored circles with value labels appear. Click each property button (pH, OC, N, BD, CEC) and confirm circles change color and legend updates. Switch map style, confirm circles return. [checkpoint marker]

---

## Phase 5: Fire Layer Implementation

Goal: Create a functional fire detection layer with demo data, integrated into LayerManager and FireDashboard.

Tasks:
- [ ] Task 5.1: Create demo fire detection data (TDD: Write test that `DEMO_FIRE_POINTS` contains at least 6 points within WA state bounds with valid risk scores 0-100; create data in `demo-data.ts`)
- [ ] Task 5.2: Rewrite fire layer as MapLibre native circles instead of deck.gl (TDD: Write test that new `FireLayer` component adds a GeoJSON source with fire points and a circle layer with risk-to-color mapping; implement using same pattern as WaterLayer — this avoids deck.gl dependency and keeps consistency with other layers)
- [ ] Task 5.3: Integrate FireLayer into LayerManager (TDD: Write test that LayerManager renders FireLayer when `activeLayers.includes("fire")` and passes demo fire data; update LayerManager to import and render FireLayer with the `"fire"` layer ID instead of the broken LandFireLayer)
- [ ] Task 5.4: Update FireDashboard to use "fire" layer ID and show demo stats (TDD: Write test that FireDashboard's LayerToggle uses `layerId="fire"`; update panel to display demo fire point count and risk distribution when tRPC queries fail)
- [ ] Task 5.5: Add fire color legend to FireDashboard panel (TDD: Write test that FireDashboard renders a color legend with risk level labels; implement legend component showing yellow=low risk through red=high risk)
- [ ] Task 5.6: Add style change resilience and click popups to FireLayer (TDD: Write test verifying fire source/layers are re-added after style change; write test that click handler shows popup with risk score and location)
- [ ] Verification: Open FireDashboard, toggle "Fire Risk" on. Confirm colored scatter points appear across Washington. Verify legend shows risk color ramp. Click a fire point to see popup. Switch map style, confirm points return. Check stat cards show demo values. [checkpoint marker]

---

## Phase 6: Integration and Layer Ordering

Goal: Ensure all layers work together, with correct stacking order, style change resilience across all layers simultaneously, and no conflicts.

Tasks:
- [ ] Task 6.1: Implement consistent layer ordering strategy (TDD: Write test that defines expected layer order — basemap -> rasters -> fills -> circles -> labels — and verifies each layer component inserts at the correct position; implement a shared `getInsertionPoint` utility that all layers use)
- [ ] Task 6.2: Test all five layers active simultaneously (TDD: Write test that activating all five layer IDs produces no console errors and all source/layer IDs are unique; verify no source ID collisions)
- [ ] Task 6.3: Test style switching with all layers active (TDD: Write test that switching styles with all layers active results in all layers being re-added within 2 seconds; implement a shared style change handler if individual components have inconsistent behavior)
- [ ] Task 6.4: Verify all panel legends match rendered colors (TDD: Write integration test comparing panel legend color values to layer paint property color values for each layer type)
- [ ] Task 6.5: Clean up unused imports and dead code (Refactor: Remove `LandFireLayer` if replaced by `FireLayer`; remove any unused `FireRiskLayer` deck.gl code if not used; clean up duplicate `getFirstSymbolLayer` utility across layer files into a shared module)
- [ ] Verification: Enable all five layers simultaneously. Pan and zoom around Washington State. Switch between dark, light, and satellite styles. Verify no visual glitches, no console errors, all legends match rendered colors, and all popups work. [checkpoint marker]
