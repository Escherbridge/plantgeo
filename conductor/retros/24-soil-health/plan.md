# Track 24: Soil Health & Carbon Potential — Implementation Plan

## Phase 1: SoilGrids Integration
- [x] Create `src/lib/server/services/soilgrids.ts` — query SoilGrids v2 REST API for bbox + property
- [x] Parse response (lat/lon grid cells with property values at surface depth 0-5cm)
- [x] Add `soilGridCache` table to schema (cellKey, property, value, fetchedAt)
- [x] Add `getSoilProperties` tRPC query — returns grid cells as GeoJSON points for current viewport
- [x] Cache in Redis (TTL 7 days)

## Phase 2: USDA Web Soil Survey
- [x] Create `src/lib/server/services/usda-soil.ts` — USDA SDM REST client for SSURGO polygon query
- [x] Parse dominant soil component (drainage class, soil series, NRCS LCC)
- [x] Add `getSoilSurvey` tRPC query — returns SSURGO polygon GeoJSON for bbox

## Phase 3: Soil Map Layers
- [x] Create `src/components/map/layers/SoilLayer.tsx` — choropleth from SoilGrids points (circle markers colored by value range)
- [x] Add property selector dropdown (SOC, clay, pH, bulk density)
- [x] Add SSURGO polygon overlay toggle

## Phase 4: Derived Layers
- [x] Implement USLE K-factor lookup from SoilGrids texture classes
- [x] Calculate erosion risk (K × slope from DEM × NLCD C-factor)
- [x] Add `getErosionRisk` tRPC query
- [x] Create `src/components/map/layers/ErosionLayer.tsx` — 5-class erosion risk choropleth

## Phase 5: Carbon Potential
- [x] Implement carbon sequestration potential formula (SOC stock × land cover multiplier × precip factor)
- [x] Add `getCarbonPotential` tRPC query returning per-cell potential in t CO₂-eq/ha/yr
- [x] Create `src/components/map/layers/CarbonPotentialLayer.tsx` — 4-class potential choropleth

## Phase 6: Intervention Suitability & Panel
- [x] Implement per-point suitability scoring for keyline, silvopasture, aquaponics, biochar
- [x] Create `src/components/panels/SoilPanel.tsx` — soil profile, carbon estimate, suitability scores
- [x] Expose suitability scores via `getInterventionSuitability(lat, lon)` for use by Track 26
