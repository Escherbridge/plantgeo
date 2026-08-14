# Track 13: Analytics & Dashboard

## Goal
Build a spatial analytics dashboard with charts, statistics, and insights derived from geospatial data, providing business intelligence on map data.

## Features
1. **Dashboard Layout**
   - Split view: map + dashboard panels
   - Configurable widget grid
   - Full-screen chart mode
   - Dashboard presets (fire, logistics, monitoring)

2. **Chart Types**
   - Time-series line charts (sensor readings over time)
   - Bar charts (feature counts by category)
   - Pie/donut charts (land use distribution)
   - Histogram (elevation distribution)
   - Scatter plots (correlation analysis)

3. **Spatial Statistics**
   - Feature count by area
   - Density analysis
   - Hotspot detection
   - Spatial clustering (DBSCAN)
   - Nearest neighbor analysis

4. **Real-Time Metrics**
   - Active fire count
   - Sensor online/offline status
   - Vehicle fleet status
   - Alert summary

5. **Export & Reporting**
   - PDF report generation
   - CSV data export
   - Screenshot map capture
   - Scheduled report delivery

## Files to Create/Modify
- `src/app/dashboard/page.tsx` - Dashboard page
- `src/components/dashboard/DashboardGrid.tsx` - Widget layout
- `src/components/dashboard/TimeSeriesChart.tsx` - Time series
- `src/components/dashboard/SpatialStats.tsx` - Spatial analysis
- `src/components/dashboard/MetricsBar.tsx` - Real-time metrics
- `src/lib/server/services/analytics.ts` - Analytics queries
- `src/lib/server/trpc/routers/analytics.ts` - Analytics router
- `src/lib/charts/chart-config.ts` - Chart.js/Recharts config

## Acceptance Criteria
- [ ] Dashboard renders with configurable widget grid
- [ ] Time-series charts show sensor data correctly
- [ ] Spatial statistics calculate density and clusters
- [ ] Real-time metrics update via SSE
- [ ] PDF and CSV exports work
- [ ] Dashboard presets switch between fire/logistics views
