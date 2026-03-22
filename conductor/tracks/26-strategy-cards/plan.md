# Track 26: Strategy Cards & Recommendations Engine — Implementation Plan

## Phase 1: Scoring Engine
- [x] Create `src/lib/server/services/strategy-scoring.ts`
- [x] Define scoring weights per strategy (keyline, silvopasture, reforestation, biochar, water harvesting, cover cropping)
- [x] Implement `scoreStrategy(strategyType, indicators)` — normalize sub-indicators 0-10, apply weights, return composite score + rationale array
- [x] Implement `getStrategyRecommendations(lat, lon)` — fetch all sub-indicators from Tracks 21-24 in parallel, score all strategies, return ranked array

## Phase 2: PlantCommerce API Client
- [x] Create `src/lib/server/services/plantcommerce-api.ts`
- [x] Implement `getStrategySuppliers(strategyType, lat, lon)` — GET `/api/v1/strategy-suppliers` with 3s timeout
- [x] Add graceful fallback: return empty array on network error / non-200 response
- [x] Cache responses in Redis (TTL 1 hour — supplier availability changes slowly)

## Phase 3: tRPC Strategy Router
- [x] Create `src/lib/server/trpc/routers/strategy.ts`
- [x] Implement `getStrategyRecommendations` query (lat, lon → ranked strategies with scores, rationale, conditions)
- [x] Implement `getStrategySuppliers` query (strategyType, lat, lon → supplier array)
- [x] Register `strategyRouter` in `src/lib/server/trpc/router.ts`

## Phase 4: Strategy Content
- [x] Create `content/strategies/` directory
- [x] Write markdown implementation guides: `keyline.md`, `silvopasture.md`, `reforestation.md`, `biochar.md`, `water-harvesting.md`, `cover-cropping.md`
- [x] Each guide: overview, site requirements, implementation steps, expected outcomes, resources

## Phase 5: Strategy Panel UI
- [x] Create `src/components/panels/StrategyCard.tsx` — score badge, conditions chips (green/red/gray), expandable guide section, supplier list
- [x] Create `src/components/panels/StrategyPanel.tsx` — ranked card list, comparison mode toggle (select up to 3)
- [x] Implement comparison view with SVG radar chart showing sub-indicator scores per strategy
- [x] Wire map click → dispatch lat/lon to Zustand → StrategyPanel fetches recommendations

## Phase 6: Priority Zone Integration
- [x] Query `community.getPriorityZones` and aggregate totalVotes per strategyType
- [x] StrategyCard renders "High Community Demand" badge when communityDemand votes present
