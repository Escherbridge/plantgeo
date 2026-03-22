# Track 30: Environmental Analytics Dashboard

## Goal
Provide a regional analytics dashboard showing aggregated trends, risk summaries, and priority metrics across all environmental data layers — giving users, teams, agencies, and utility companies an at-a-glance intelligence view of what's happening and where action is most needed.

## Features

1. **Regional Risk Summary**
   - Summary panel: current risk levels for visible map region across all 4 domains
   - Fire: active fires count, total acreage, % area at high/extreme risk
   - Water: % of gauges at critical low / normal / flood risk; current drought class distribution
   - Soil: avg SOC for region, % area high erosion risk
   - Vegetation: avg NDVI deviation from 10-year mean, % area with land cover change in past 5 years
   - Updates dynamically as map viewport changes

2. **Trend Charts**
   - Time-series charts per domain for the current region:
     - Fire: monthly burned acreage (12-month rolling)
     - Water: streamflow percentile trend (6-month)
     - Drought: drought class coverage over time (12-month)
     - NDVI: vegetation health anomaly trend (24-month)
   - Charts use TimescaleDB continuous aggregates for fast historical queries

3. **Priority Metrics**
   - Top 5 highest-risk subregions (watershed / county level) for current viewport
   - Ranked by composite risk score (fire + water + soil + vegetation)
   - Each row: subregion name, dominant risk type, community vote count (from Track 25), team coverage (from Track 27)
   - "View" button → zoom map to subregion + open strategy panel

4. **Strategy Demand Heatmap**
   - Aggregated community request density as a heatmap layer
   - Filter by strategy type (keyline, silvopasture, reforestation, etc.)
   - Overlaid on base map when "Analytics" mode is active

5. **Export & Reporting**
   - Export current region's analytics as PDF report: risk summary table + trend charts + priority subregions list
   - CSV export of raw metric values for current region
   - Shareable link: encode viewport + selected metrics in URL params

6. **Historical Comparison**
   - Compare current risk metrics against same period in prior years (1yr, 3yr, 5yr)
   - Highlight areas with worsening trends (fire risk increasing, SOC declining, NDVI anomaly deepening)

## Files to Create/Modify
- `src/lib/server/trpc/routers/analytics.ts` — Extend existing with `getRegionalRiskSummary`, `getTrendData`, `getPrioritySubregions`, `getStrategyDemandHeatmap`
- `src/lib/server/db/queries/analytics.ts` — TimescaleDB time-bucket queries for trend data
- `src/components/panels/AnalyticsDashboard.tsx` — Main analytics panel with tabs: Overview / Trends / Priorities
- `src/components/charts/TrendChart.tsx` — Recharts LineChart wrapper for domain trend data
- `src/components/charts/RiskSummaryWidget.tsx` — 4-domain risk summary with color-coded severity badges
- `src/components/map/layers/DemandHeatmapLayer.tsx` — Community request density heatmap
- `src/lib/server/services/report-generator.ts` — PDF/CSV export using `@react-pdf/renderer` or `puppeteer`

## Acceptance Criteria
- [ ] Regional risk summary updates within 2 seconds of map viewport change
- [ ] Trend charts load 12-month data for fire and water domains
- [ ] Priority subregions table shows top 5 areas sorted by composite risk score
- [ ] Strategy demand heatmap renders for current viewport, filterable by strategy type
- [ ] PDF export generates report with summary + at least 2 trend charts
- [ ] Historical comparison highlights areas with worsening 3-year trend
- [ ] All charts use real data from Tracks 21-25, not synthetic values

## Dependencies
- Track 21 (Wildfire) — fire risk scores, burned area history
- Track 22 (Water Scarcity) — streamflow percentiles, drought classification data
- Track 23 (Vegetation) — NDVI anomaly data, land cover change
- Track 24 (Soil Health) — SOC, erosion risk
- Track 25 (Community Requests) — strategy demand density, priority zone vote counts
- Track 27 (Teams) — team service area coverage for priority subregions
- TimescaleDB — continuous aggregates for time-series trend queries

## Tech Stack Note
TimescaleDB continuous aggregates (`CREATE MATERIALIZED VIEW ... WITH (timescaledb.continuous)`) pre-aggregate hourly/daily metrics for fast trend chart queries. PDF export uses `@react-pdf/renderer` for server-side React-based PDF generation — no headless browser needed. Heatmap uses MapLibre's built-in heatmap layer type with community request point data.
