# Track 21: Wildfire Risk Enhancement

## Goal
Replace hardcoded vegetation weights in `calculateFireRisk()` with real LANDFIRE fuel model data, add historical burn perimeter overlays from MTBS, and upgrade the fire risk engine to use actual fuel type classifications for accurate, data-driven risk scoring across all CONUS.

## Features

1. **LANDFIRE Fuel Model Integration**
   - Replace hardcoded vegetation weights (chaparral=0.95, grassland=0.8, etc.) with LANDFIRE EVT (Existing Vegetation Type) WMS tile overlay
   - Map LANDFIRE fuel model codes to fire behavior parameters (rate of spread, flame length, intensity)
   - Dynamic fuel moisture modeling based on current drought index + weather data
   - LANDFIRE 2020 (LF2020) as primary source — WMS tiles loaded directly into MapLibre

2. **Burn History Layer (MTBS)**
   - MTBS (Monitoring Trends in Burn Severity) fire perimeter polygons as a vector layer
   - Color-coded by burn severity (unburned, low, moderate, high, increased greenness)
   - Filterable by year range (1984–present)
   - Click perimeter → shows fire name, date, acres burned, severity statistics

3. **Post-Fire Recovery Monitoring**
   - Pre-computed NBR (Normalized Burn Ratio) tile overlay from USGS/Copernicus
   - Recovery index derived from delta-NBR (pre vs. post fire comparison)
   - Visual legend showing recovery stages (no recovery → full recovery)

4. **Enhanced Fire Weather Index**
   - Full Canadian FWI System: FFMC, DMC, DC, ISI, BUI, FWI components
   - Replace current simplified scoring with proper FWI calculation
   - 7-day FWI forecast from Open-Meteo forecast data

5. **Improved Risk Scoring API**
   - `getFireRiskForPoint(lat, lon)` — queries LANDFIRE EVT at point, fetches current weather, computes full FWI
   - Risk score now reflects actual fuel type, terrain, and real-time weather — not hardcoded parameters
   - Confidence interval displayed alongside score

## Files to Create/Modify
- `src/lib/server/services/landfire.ts` — LANDFIRE WMS/REST client, EVT code → fuel behavior mapping
- `src/lib/server/services/fire-weather-index.ts` — Full FWI System calculation (FFMC, DMC, DC, ISI, BUI, FWI)
- `src/lib/server/services/mtbs.ts` — MTBS perimeter data fetcher (GeoJSON from USGS)
- `src/lib/server/services/fire-risk.ts` — Refactor to use LANDFIRE + FWI (replace hardcoded weights)
- `src/lib/server/trpc/routers/wildfire.ts` — Add `getFireRiskForPoint`, `getMTBSPerimeters`, `getRecoveryIndex`
- `src/components/map/layers/LandFireLayer.tsx` — LANDFIRE EVT WMS tile overlay
- `src/components/map/layers/BurnHistoryLayer.tsx` — MTBS perimeter polygons
- `src/components/map/layers/RecoveryLayer.tsx` — NBR recovery tile overlay
- `src/components/panels/FireDashboard.tsx` — Add FWI components, LANDFIRE legend, recovery tracking

## Acceptance Criteria
- [ ] `calculateFireRisk()` uses LANDFIRE EVT fuel codes, not hardcoded vegetation strings
- [ ] LANDFIRE WMS tile layer renders in MapLibre at zoom 8+
- [ ] MTBS burn perimeters load for a given bbox, colored by severity
- [ ] FWI score calculated from real weather data (FFMC, DMC, DC components)
- [ ] Recovery NBR tiles overlay visible and toggleable
- [ ] Fire risk score for a known high-risk location (e.g., Paradise CA) returns >80

## Dependencies
- Track 10 (Wildfire Prevention) — must be complete
- LANDFIRE WMS endpoint: `https://www.landfire.gov/arcgis/rest/services/...`
- MTBS GeoJSON: `https://edcintl.cr.usgs.gov/downloads/sciweb1/shared/MTBS_Fire/data/composite_data/burned_area_extent_shapefile/`
- Open-Meteo forecast API (already integrated)
