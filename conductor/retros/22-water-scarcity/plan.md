# Track 22: Water Scarcity Layer — Implementation Plan

## Phase 1: USGS Streamflow Integration
- [x] Create `src/lib/server/services/usgs-water.ts` — fetch instantaneous values from NWIS for bbox
- [x] Parse NWIS response (site number, flow rate cfs, historical percentile, trend)
- [x] Add `waterGauges` cache table to schema (id, siteNo, lat, lon, lastReading, percentile, trend, updatedAt)
- [x] Add `getStreamflow` tRPC query (bbox → gauge array)
- [x] Set up 15-minute cron job via BullMQ to refresh gauge data

## Phase 2: Drought Monitor
- [x] Create `src/lib/server/services/drought.ts` — fetch weekly USDM GeoJSON
- [x] Add `droughtData` cache table (week, geojson blob, fetchedAt)
- [x] Add `getDroughtClassification` tRPC query
- [x] Create `src/components/map/layers/DroughtLayer.tsx` — D0-D4 choropleth with date slider

## Phase 3: Watershed Boundaries
- [x] Create `src/lib/server/services/hydrosheds.ts` — load HydroSHEDS Level 4 GeoJSON for Western USA
- [x] Cache watersheds in PostGIS (static data, load once)
- [x] Add `getWatersheds` tRPC query (bbox → watershed polygons)
- [x] Add watershed polygon rendering to `WaterLayer.tsx`

## Phase 4: Groundwater
- [x] Extend `usgs-water.ts` to fetch groundwater well data from NWIS
- [x] Parse water table depth and 12-month trend
- [x] Add groundwater well markers to `WaterLayer.tsx` (separate from streamflow)

## Phase 5: Water Scarcity Score
- [x] Implement composite water scarcity score (drought class × streamflow percentile × groundwater trend)
- [x] Map score to watershed polygons as a choropleth
- [x] Add `WaterPanel.tsx` — gauge detail panel, watershed summary, drought history chart

## Phase 6: Cron Infrastructure
- [x] Install BullMQ (`npm install bullmq`)
- [x] Create `src/lib/server/jobs/water-refresh.ts` — BullMQ job for NWIS polling
- [x] Register job in app startup (Next.js instrumentation hook)
