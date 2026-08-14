# Track 24: Soil Health & Carbon Potential Layer

## Goal
Add soil health data to PlantGeo using SoilGrids (global 250m) and USDA Web Soil Survey REST API — showing soil organic carbon, texture, drainage, erosion risk, and carbon sequestration potential. Soil data is foundational for intervention suitability (silvopasture, keyline, biochar) and future carbon accounting.

## Features

1. **SoilGrids Choropleth Layers**
   - REST API at `rest.isric.org` returns soil properties at 6 depth intervals (0-5, 5-15, 15-30, 30-60, 60-100, 100-200 cm)
   - Properties to display: soil organic carbon (g/kg), clay content (%), pH, bulk density (kg/m³)
   - Choropleth rendered as color-coded grid cells at 250m resolution for the visible map extent
   - Property selector dropdown (switch between SOC, clay, pH, bulk density)

2. **USDA Web Soil Survey Integration**
   - USDA WSS SOAP/REST API for US-specific high-resolution soil data
   - Returns dominant soil series, drainage class (well-drained / somewhat poorly / poorly), hydric status, NRCS land capability class
   - Rendered as vector polygon overlay matching SSURGO map unit boundaries

3. **Erosion Risk Layer**
   - Derived from SoilGrids (soil erodibility K-factor), slope (from DEM), and land cover (NLCD)
   - USLE/RUSLE-based erosion hazard rating: very low / low / moderate / high / very high
   - Displayed as choropleth overlay

4. **Carbon Sequestration Potential**
   - Composite score per 250m cell: SoilGrids SOC × land cover × precipitation × slope
   - Categories: Low / Moderate / High / Very High potential for carbon sequestration via soil management
   - Highlights best areas for biochar application, cover cropping, and silvopasture
   - Clickable cell → shows estimated t CO₂-eq/ha/yr potential range

5. **Soil-Based Intervention Suitability**
   - Per-cell suitability scores for: keyline design (requires adequate depth + infiltration), silvopasture (requires well-drained + moderate-high SOC), aquaponics (soil type less critical), biochar (SOC-deficient soils benefit most)
   - Used by Track 26 (Strategy Cards) to rank strategy recommendations

## Files to Create/Modify
- `src/lib/server/services/soilgrids.ts` — SoilGrids REST API client (bbox → property grid)
- `src/lib/server/services/usda-soil.ts` — USDA WSS REST client (bbox → SSURGO polygons)
- `src/lib/server/db/schema.ts` — Add `soilGridCache` table (tile key, property, data blob, fetchedAt)
- `src/lib/server/trpc/routers/environmental.ts` — Add `getSoilProperties`, `getSoilSurvey`, `getErosionRisk`, `getCarbonPotential`
- `src/components/map/layers/SoilLayer.tsx` — Choropleth layer with property selector
- `src/components/map/layers/ErosionLayer.tsx` — Erosion risk overlay
- `src/components/map/layers/CarbonPotentialLayer.tsx` — Carbon sequestration potential choropleth
- `src/components/panels/SoilPanel.tsx` — Soil profile display, intervention suitability scores

## Acceptance Criteria
- [ ] SoilGrids SOC values load for visible map extent at zoom 8+
- [ ] Property selector switches between SOC, clay, pH, bulk density
- [ ] USDA WSS polygons render with drainage class and soil series name
- [ ] Erosion risk overlay displays with 5-class rating
- [ ] Carbon sequestration potential choropleth shows t CO₂-eq/ha/yr estimate on click
- [ ] Suitability scores for 4 intervention types returned per map click point
- [ ] SoilGrids data cached in Redis (TTL 7 days — data updates annually)

## Dependencies
- Track 23 (Vegetation) — NLCD land cover input for erosion calculation
- SoilGrids REST API (free): `https://rest.isric.org/soilgrids/v2.0/properties/query`
- USDA WSS API (free): `https://sdmdataaccess.sc.egov.usda.gov/`

## Tech Stack Note
SoilGrids capped at ~2 req/sec — cache all responses aggressively in Redis. No raster pipeline needed — properties returned as JSON points, rendered as choropleth on client.
