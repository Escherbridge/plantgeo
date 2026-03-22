# Track 23: Vegetation & Land Cover — Implementation Plan

## Phase 1: NDVI Tile Overlay
- [x] Create `src/lib/server/services/vegetation.ts` — configure Copernicus NDVI XYZ tile endpoint URLs by month/year
- [x] Add NDVI tile source to `getSources()` in `sources.ts` (dynamic URL based on selected month)
- [x] Create `src/components/map/layers/VegetationLayer.tsx` — raster tile overlay with month state
- [x] Add month slider UI component to VegetationPanel
- [x] Implement NDVI anomaly mode (deviation from 10-year average tile URL)

## Phase 2: Burn Recovery (NBR)
- [x] Add NBR tile source from USGS to `sources.ts`
- [x] Extend `VegetationLayer.tsx` to support NBR mode toggle
- [x] Integrate NBR color ramp (burned → recovering → healthy)

## Phase 3: NLCD Land Cover
- [x] Create `src/lib/server/services/nlcd.ts` — NLCD WMS source configuration
- [x] Add NLCD WMS source to `getSources()` in `sources.ts`
- [x] Create `src/components/map/layers/LandCoverLayer.tsx` — classification layer with class filter
- [x] Build class filter UI (checkboxes for forest / shrubland / grassland / cropland / wetland)

## Phase 4: Land Use Change Detection
- [x] Add NLCD change layer WMS source (NLCD Change 2019-2021)
- [x] Color-code by change type (deforestation=red, recovery=green, urbanization=gray)
- [x] Add year range selector for change layer

## Phase 5: Reforestation Opportunity Zones
- [x] Create `src/lib/server/trpc/routers/environmental.ts` — add `getReforestationZones` procedure
- [x] Implement zone calculation: degraded NLCD class + ≥400mm precipitation + soil organic carbon > threshold
- [x] Cache computed zones in PostGIS as polygon features
- [x] Create `src/components/map/layers/ReforestationLayer.tsx` — priority polygon rendering

## Phase 6: NDWI & Panel
- [x] Add NDWI tile source (Copernicus or USGS pre-computed)
- [x] Add NDWI toggle to `VegetationLayer.tsx`
- [x] Create `src/components/panels/VegetationPanel.tsx` — NDVI stats, land cover chart, opportunity zone detail
