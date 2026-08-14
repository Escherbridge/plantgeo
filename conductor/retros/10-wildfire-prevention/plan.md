# Track 10: Wildfire Prevention - Implementation Plan

## Phase 1: Fire Risk Engine
- [ ] Port Python fire risk index calculation to TypeScript
- [ ] Create NASA FIRMS API integration
- [ ] Build fire risk heatmap layer (deck.gl HeatmapLayer)
- [ ] Store fire data in PostGIS with time tracking

## Phase 2: Strategy Mapper
- [ ] Port strategy scoring algorithm to TypeScript
- [ ] Create intervention zone visualization layer
- [ ] Build StrategyPanel with comparison view
- [ ] Add cost/timeline estimates display

## Phase 3: Weather Integration
- [ ] Create Open-Meteo service client
- [ ] Build weather overlay layer (wind arrows, contours)
- [ ] Calculate Fire Weather Index (FWI)
- [ ] Add forecast time slider

## Phase 4: Ecosystem Tracker
- [ ] Create action logging form with geometry drawing
- [ ] Build impact metrics calculation
- [ ] Create time-series charts for impact tracking
- [ ] Add before/after comparison view

## Phase 5: Data Ingest
- [ ] Create NASA FIRMS webhook endpoint
- [ ] Set up periodic weather data fetch (cron)
- [ ] Integrate historical NIFC fire perimeters
- [ ] Add data freshness indicators

## Phase 6: Dashboard
- [ ] Build FireDashboard with summary metrics
- [ ] Add fire risk timeline chart
- [ ] Create area-of-interest saved views
- [ ] Add alert notifications for risk threshold breaches
