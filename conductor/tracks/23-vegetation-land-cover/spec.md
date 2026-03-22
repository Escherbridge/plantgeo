# Track 23: Vegetation & Land Cover Layer

## Goal
Add vegetation health monitoring and land cover change detection to PlantGeo using pre-computed satellite indices served as XYZ tile overlays — no raw satellite ingestion required. Show NDVI trends, land use change, deforestation, and reforestation opportunity zones to help users identify where vegetation interventions are most needed.

## Features

1. **NDVI Monthly Composite Tile Overlay**
   - NDVI (Normalized Difference Vegetation Index) monthly composites as a raster XYZ tile overlay
   - Source: Copernicus Land Service (free, pre-computed, global)
   - Color ramp: red (dead/sparse) → yellow (moderate) → dark green (dense/healthy)
   - Month selector slider to browse historical NDVI (2000–present)
   - Shows vegetation health relative to long-term average (anomaly mode)

2. **Burn Recovery Index (NBR)**
   - Post-fire NBR (Normalized Burn Ratio) overlay from USGS/Copernicus
   - Shows burn severity and recovery progress for areas burned in past 5 years
   - Integrates with Track 21 burn history layer

3. **National Land Cover Database (NLCD)**
   - NLCD 2021 land cover classification as a reference layer (21 classes)
   - Filterable by land type (forest, shrubland, grassland, cropland, developed, wetland)
   - Used as base context for intervention suitability

4. **Land Use Change Detection**
   - Annual NLCD change comparison layer (deforestation, urbanization, agricultural conversion, recovery)
   - Color-coded change type with year filter
   - Highlight reforestation success areas and deforestation threat zones

5. **Reforestation Opportunity Zones**
   - Derived composite layer: degraded land (NLCD) + adequate rainfall (WorldClim) + low development pressure + soil suitability (Track 24)
   - Shown as priority polygon overlays — "High", "Medium", "Low" suitability for tree planting
   - Feeds directly into strategy card recommendations

6. **NDWI Water Stress Overlay**
   - NDWI (Normalized Difference Water Index) for vegetation water content monitoring
   - Identifies water-stressed vegetation before it becomes a fire risk
   - Seasonal composite: spring / summer / fall comparisons

## Files to Create/Modify
- `src/lib/server/services/vegetation.ts` — Copernicus tile endpoint configuration, NDVI metadata
- `src/lib/server/services/nlcd.ts` — NLCD WMS/tile source configuration + change detection layer
- `src/lib/server/trpc/routers/environmental.ts` — Add `getVegetationSources`, `getNDVIMetadata`, `getReforestationZones`
- `src/lib/map/sources.ts` — Add Copernicus NDVI, NLCD, NBR tile sources
- `src/components/map/layers/VegetationLayer.tsx` — NDVI overlay with month slider, anomaly mode
- `src/components/map/layers/LandCoverLayer.tsx` — NLCD classification layer with class filters
- `src/components/map/layers/ReforestationLayer.tsx` — Opportunity zone polygons
- `src/components/panels/VegetationPanel.tsx` — NDVI stats, land cover breakdown, opportunity zone detail

## Acceptance Criteria
- [ ] NDVI tile overlay loads from Copernicus for current month
- [ ] Month slider navigates NDVI history at least 5 years back
- [ ] NDVI anomaly mode shows deviation from 10-year average
- [ ] NLCD 2021 layer renders with class filter controls
- [ ] Land use change layer shows deforestation and recovery for a given area
- [ ] Reforestation opportunity zones visible as color-coded polygons
- [ ] NDWI water stress overlay toggleable

## Dependencies
- Track 21 (Wildfire Enhancement) — NBR integration
- Track 24 (Soil Health) — soil suitability inputs for reforestation zones
- Copernicus NDVI tiles: `https://land.copernicus.eu/global/products/ndvi`
- NLCD WMS: `https://www.mrlc.gov/geoserver/mrlc_display/NLCD_2021_Land_Cover_L48/wms`

## Tech Stack Note
No raw satellite ingestion. All layers served as pre-computed XYZ tile URLs or WMS endpoints — compatible with current MapLibre + Martin architecture.
