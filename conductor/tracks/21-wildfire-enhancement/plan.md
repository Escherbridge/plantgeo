# Track 21: Wildfire Risk Enhancement — Implementation Plan

## Phase 1: LANDFIRE Integration
- [x] Create `src/lib/server/services/landfire.ts` — fetch EVT code for a lat/lon via LANDFIRE REST API
- [x] Build EVT code → fuel behavior parameter mapping table (rate of spread, flame length, heat per unit area)
- [x] Refactor `calculateFireRisk()` in `fire-risk.ts` to accept LANDFIRE fuel parameters instead of hardcoded strings
- [x] Add LANDFIRE WMS source to `getSources()` in `sources.ts`
- [x] Create `src/components/map/layers/LandFireLayer.tsx` — toggleable WMS tile overlay

## Phase 2: FWI System
- [x] Create `src/lib/server/services/fire-weather-index.ts` — implement FFMC, DMC, DC, ISI, BUI, FWI calculations
- [x] Wire Open-Meteo weather data into FWI calculator
- [x] Update `getFireRiskForArea` tRPC procedure to return full FWI components
- [ ] Display FWI breakdown in FireDashboard (FFMC, DMC, DC, FWI gauge)

## Phase 3: Burn History (MTBS)
- [x] Create `src/lib/server/services/mtbs.ts` — fetch MTBS GeoJSON for bbox + year range
- [x] Cache MTBS data in Redis (TTL 24h — data is static/annual)
- [x] Add `getMTBSPerimeters` tRPC query procedure
- [x] Create `src/components/map/layers/BurnHistoryLayer.tsx` — severity-colored polygon layer with year filter slider

## Phase 4: Recovery Monitoring
- [x] Add NBR tile overlay source (Copernicus pre-computed XYZ tiles)
- [x] Create `src/components/map/layers/RecoveryLayer.tsx` — NBR raster overlay with recovery legend
- [ ] Add recovery stage classification (no recovery → full recovery) based on delta-NBR thresholds

## Phase 5: Dashboard Enhancement
- [ ] Update `FireDashboard.tsx` — add FWI gauge widget, LANDFIRE fuel type indicator
- [ ] Add burn history timeline chart (fires per year, acres, severity breakdown)
- [ ] Add risk score confidence interval display
- [ ] Wire all new layers into layer toggle panel
