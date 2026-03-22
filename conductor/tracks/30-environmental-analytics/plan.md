# Track 30: Environmental Analytics Dashboard — Implementation Plan

## Phase 1: TimescaleDB Continuous Aggregates
- [x] Add TimescaleDB extension to db init script (`CREATE EXTENSION IF NOT EXISTS timescaledb`)
- [x] Convert `waterGauges` and `droughtData` tables to hypertables (if not already)
- [x] Create continuous aggregate: `daily_fire_metrics` (burned acreage per watershed per day)
- [x] Create continuous aggregate: `daily_water_metrics` (avg streamflow percentile per watershed per day)
- [x] Create continuous aggregate: `monthly_ndvi_anomaly` (avg NDVI deviation per region per month)

## Phase 2: Analytics Queries
- [x] Create `src/lib/server/db/analytics.ts`
- [x] `getRegionalRiskSummary(bbox)` — aggregate fire/water/soil/vegetation risk for bbox
- [x] `getTrendData(bbox, domain, months)` — time-bucket query from TimescaleDB aggregates
- [x] `getPrioritySubregions(bbox)` — join watershed boundaries with composite risk scores, sort desc
- [x] `getStrategyDemandDensity(bbox, strategyType?)` — aggregate request counts to GeoJSON points for heatmap

## Phase 3: tRPC Analytics Router Extension
- [x] Extend `src/lib/server/trpc/routers/analytics.ts` with new procedures:
  - `getRegionalRiskSummary` (bbox → 4-domain risk object)
  - `getTrendData` (bbox, domain, months → time-series array)
  - `getPrioritySubregions` (bbox → top 5 subregion array)
  - `getStrategyDemandHeatmap` (bbox, strategyType? → GeoJSON FeatureCollection)
- [x] Add Redis caching for risk summary (TTL 5 min, keyed by geohash-4)

## Phase 4: Chart Components
- [x] Install `recharts` if not present (SVG fallback implemented)
- [x] Create `src/components/charts/TrendChart.tsx` — SVG line chart with responsive container, domain color theming
- [x] Create `src/components/charts/RiskSummaryWidget.tsx` — 4 metric cards with severity color badges (green/yellow/orange/red)
- [x] Create `src/components/charts/PriorityTable.tsx` — sortable table of top subregions with composite score bar

## Phase 5: Analytics Dashboard Panel
- [x] Create `src/components/panels/AnalyticsDashboard.tsx`
- [x] Tab 1 "Overview": RiskSummaryWidget (4 domains) + PriorityTable (top 5)
- [x] Tab 2 "Trends": domain selector + TrendChart + historical comparison toggle (1yr/3yr/5yr)
- [x] Tab 3 "Demand": strategy type filter + demand stats summary
- [x] Wire viewport change → debounced (500ms) refetch of regional data

## Phase 6: Demand Heatmap Layer
- [x] Create `src/components/map/layers/DemandHeatmapLayer.tsx`
- [x] MapLibre heatmap layer from `getStrategyDemandHeatmap` GeoJSON
- [x] Strategy type filter passed as query param → different heatmap weight properties
- [x] Activate when "Analytics" mode enabled in layer control

## Phase 7: Export & Reporting
- [x] Install `@react-pdf/renderer` (HTML + window.print() fallback implemented)
- [x] Create `src/lib/export/analytics-export.ts` — exportCSV + exportPDF with print-friendly HTML template
- [x] Add "Export PDF" and "Export CSV" buttons to AnalyticsDashboard header
