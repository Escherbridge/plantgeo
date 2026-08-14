# Track 26: Strategy Cards & Recommendations Engine

## Goal
When a user clicks a map location, generate ranked strategy recommendations based on local environmental conditions (fire risk, water stress, soil health, vegetation, land cover). Each strategy card shows rationale, suitability score, and embedded PlantCommerce supplier links for equipment/services needed to implement the strategy.

## Features

1. **Location-Based Strategy Scoring**
   - On map click: fetch environmental context for clicked lat/lon from Tracks 21-24 outputs
   - Score each strategy type (1–10) based on local conditions:
     - **Keyline design**: soil depth + infiltration rate + slope gradient
     - **Silvopasture**: drainage class + moderate-high SOC + adequate precipitation
     - **Reforestation**: degraded NLCD class + rainfall + fire risk proximity
     - **Biochar application**: SOC-deficient soils + cropland/degraded land cover
     - **Water harvesting / swales**: slope + soil permeability + precipitation deficit
     - **Cover cropping**: soil texture + erosion risk + cropland NLCD class
   - Score formula: weighted composite of normalized sub-indicators from Tracks 21-24

2. **Strategy Cards UI**
   - Ranked list of strategy cards (highest score first) in sidebar panel
   - Each card: strategy name, suitability score (badge), 2-sentence rationale, key conditions met/not met
   - "Learn more" expands card with full implementation guide (static markdown content per strategy)
   - Condition chips: green (favorable) / red (unfavorable) / gray (no data)

3. **PlantCommerce Supplier Integration**
   - Each strategy card has a "Get Supplies" section
   - Calls `/api/v1/strategy-suppliers?strategy={type}&lat={lat}&lon={lon}` (PlantCommerce API)
   - Displays: supplier name, product/service name, distance (if local), price range, link
   - Falls back gracefully if PlantCommerce API is unavailable (hides "Get Supplies" section)

4. **Priority Zone Context**
   - If clicked location is inside a Community Priority Zone (Track 25): show "High community demand" badge on relevant strategy cards
   - Priority Zone vote count and dominant requested strategy surfaced in card header

5. **Strategy Comparison View**
   - Side-by-side comparison of up to 3 strategies for a given location
   - Radar chart showing sub-indicator scores (soil, water, fire risk, land cover) per strategy
   - Export comparison as PDF or share link

## Files to Create/Modify
- `src/lib/server/services/strategy-scoring.ts` — Scoring engine: fetches sub-indicator values, applies weights, returns ranked strategies
- `src/lib/server/trpc/routers/strategy.ts` — `getStrategyRecommendations(lat, lon)` → ranked strategy array with scores and rationale
- `src/lib/server/services/plantcommerce-api.ts` — Client for `/api/v1/strategy-suppliers` with timeout + graceful fallback
- `src/components/panels/StrategyPanel.tsx` — Ranked cards, expand/collapse, comparison view
- `src/components/panels/StrategyCard.tsx` — Individual card: score badge, conditions, supplier section
- `content/strategies/` — Markdown files per strategy type (keyline.md, silvopasture.md, etc.)

## Acceptance Criteria
- [ ] Map click triggers strategy recommendations within 2 seconds
- [ ] All 6 strategy types scored and ranked for any clicked location in Western USA
- [ ] Each card shows ≥2 favorable/unfavorable condition chips with data source
- [ ] PlantCommerce supplier links appear for at least keyline and silvopasture strategies
- [ ] Community Priority Zone badge shown when location is inside a zone
- [ ] Strategy comparison view shows radar chart for ≤3 selected strategies
- [ ] Graceful fallback when PlantCommerce API is unreachable

## Dependencies
- Track 21 (Wildfire) — fire risk score + fuel model data
- Track 22 (Water Scarcity) — streamflow percentile + drought class + groundwater trend
- Track 23 (Vegetation) — NLCD land cover class + NDVI value
- Track 24 (Soil Health) — SOC, drainage class, erosion risk, suitability scores from `getInterventionSuitability`
- Track 25 (Community Requests) — Priority Zone overlap check
- PlantCommerce API — `/api/v1/strategy-suppliers` endpoint (external)

## Tech Stack Note
Scoring engine is pure server-side TypeScript — no ML model needed for v1. Weights are hardcoded per strategy type and can be tuned via config. Radar chart uses Recharts (already in dependency candidates) or a lightweight SVG implementation.
