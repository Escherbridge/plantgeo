# Track 22: Water Scarcity Layer

## Goal
Add a comprehensive water scarcity data layer to PlantGeo showing real-time streamflow conditions, drought severity, watershed boundaries, and groundwater levels — enabling users to identify water stress zones, plan water-dependent interventions (silvopasture, aquaponics, keyline design), and connect drought-affected communities to solutions.

## Features

1. **USGS Streamflow Gauge Markers**
   - Live markers for all active USGS NWIS streamflow gauges in Western USA (~1,500 gauges)
   - Color-coded by flow condition: above normal (blue) / normal (teal) / below normal (yellow) / low (orange) / critically low (red)
   - Trend arrow: rising / stable / falling
   - Click gauge → panel showing flow rate (cfs), historical percentile, 30-day trend chart
   - 15-minute polling via USGS NWIS Instantaneous Values API

2. **US Drought Monitor Overlay**
   - Weekly drought classification choropleth: None / Abnormally Dry (D0) / Moderate (D1) / Severe (D2) / Extreme (D3) / Exceptional (D4)
   - Color scale from white to dark red
   - Date slider to view drought history (2000–present)
   - Source: USDA/NDMC GeoJSON weekly release

3. **Watershed Catchment Boundaries**
   - HydroSHEDS Level 4/6 watershed polygons as vector layer
   - Click watershed → shows area (km²), major waterways, gauge count, dominant drought class
   - Used for intervention suitability context ("this watershed is in D3 drought")

4. **Groundwater Level Indicators**
   - USGS groundwater monitoring wells as markers (where data available)
   - Color-coded by recent trend: rising / stable / declining / critical
   - Click well → 12-month water table depth chart

5. **Water Scarcity Risk Score**
   - Composite score per watershed: drought severity × streamflow percentile × groundwater trend
   - Displayed as color-coded watershed choropleth (separate from drought monitor)
   - Used to feed strategy card recommendations (keyline design, water harvesting applicable in high-scarcity zones)

## Files to Create/Modify
- `src/lib/server/services/usgs-water.ts` — NWIS API client (streamflow + groundwater polling)
- `src/lib/server/services/drought.ts` — US Drought Monitor GeoJSON fetcher + weekly cache
- `src/lib/server/services/hydrosheds.ts` — HydroSHEDS boundary loader + caching
- `src/lib/server/db/schema.ts` — Add `waterGauges`, `droughtData` tables for caching
- `src/lib/server/trpc/routers/environmental.ts` — Add `getStreamflow`, `getDroughtClassification`, `getWatersheds`, `getGroundwater`
- `src/components/map/layers/WaterLayer.tsx` — Streamflow gauge markers + watershed polygons
- `src/components/map/layers/DroughtLayer.tsx` — Drought monitor choropleth
- `src/components/panels/WaterPanel.tsx` — Gauge detail, drought stats, watershed info

## Acceptance Criteria
- [ ] USGS streamflow gauges appear as color-coded markers in Western USA
- [ ] Clicking a gauge shows flow rate, percentile, and 30-day trend
- [ ] US Drought Monitor choropleth loads with current week's data
- [ ] Drought date slider works back to at least 2010
- [ ] HydroSHEDS watershed boundaries load at zoom 6+
- [ ] Water scarcity score visible as a choropleth layer
- [ ] Groundwater wells visible with trend indicators where data available

## Dependencies
- Track 07 (Layer Management) — layer toggle system
- USGS NWIS API (free, no key required): `https://waterservices.usgs.gov/nwis/iv/`
- US Drought Monitor (free): `https://droughtmonitor.unl.edu/data/json/usdm_current.json`
- HydroSHEDS (free): `https://www.hydrosheds.org/products/hydrobasins`
