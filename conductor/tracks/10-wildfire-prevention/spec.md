# Track 10: Wildfire Prevention Module

## Goal
Integrate the existing plantcommerce wildfire prevention knowledge base into PlantGeo as a first-class module, connecting the Python geospatial system (data_pipeline, strategy_mapper, ecosystem_monitor) to the 3D map interface.

## Features
1. **Fire Risk Dashboard**
   - Real-time fire risk heatmap from NASA FIRMS
   - Risk score overlays (5-factor composite index)
   - Fuel moisture modeling (Nelson Model)
   - Active fire markers with VIIRS/MODIS data

2. **Intervention Strategy Mapper**
   - 6 intervention strategy visualization on map
   - Suitability scoring per zone (0-100)
   - Cost/timeline estimates per intervention
   - Strategy comparison view
   - Zone click → recommended strategies panel

3. **Ecosystem Action Tracker**
   - Log interventions with geometry (polygon/point)
   - 8 action types: grazing, planting, biochar, fuel treatment, etc.
   - Impact metrics: fuel reduction, carbon seq, water infiltration
   - Before/after satellite imagery comparison
   - Time-series impact charts

4. **Weather Integration**
   - Open-Meteo weather data overlay
   - Wind speed/direction arrows
   - Temperature + humidity contours
   - Fire weather index (FWI) calculation
   - Forecast visualization (24h/48h/72h)

5. **Historical Analysis**
   - Historical fire perimeter timeline
   - Burn scar satellite imagery
   - Recovery tracking over time
   - Statistical analysis per region

## Files to Create/Modify
- `src/components/panels/FireDashboard.tsx` - Risk dashboard
- `src/components/panels/StrategyPanel.tsx` - Intervention strategies
- `src/components/panels/EcosystemTracker.tsx` - Action logging
- `src/components/map/layers/FireRiskLayer.tsx` - Risk heatmap
- `src/components/map/layers/WeatherLayer.tsx` - Weather overlay
- `src/components/map/layers/InterventionLayer.tsx` - Strategy zones
- `src/lib/server/services/fire-risk.ts` - Risk calculation
- `src/lib/server/services/weather.ts` - Open-Meteo integration
- `src/lib/server/services/nasa-firms.ts` - NASA FIRMS feed
- `src/lib/server/trpc/routers/wildfire.ts` - Wildfire tRPC router
- `src/app/api/ingest/firms/route.ts` - NASA FIRMS webhook

## Acceptance Criteria
- [ ] Fire risk heatmap renders with real NASA FIRMS data
- [ ] Risk score calculation matches Python pipeline logic
- [ ] Strategy mapper shows 6 intervention types per zone
- [ ] Ecosystem actions log to PostGIS with geometry
- [ ] Weather overlay shows wind, temp, humidity
- [ ] Time-series charts show impact metrics
- [ ] Historical fire perimeters load from NIFC data
