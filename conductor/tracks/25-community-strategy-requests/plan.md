# Track 25: Community Strategy Request System — Implementation Plan

## Phase 1: Database Schema
- [x] Add `strategyRequests` table to schema (id, lat, lon, strategyType, description, userId, sessionId, createdAt)
- [x] Add `requestVotes` table (requestId, userId/sessionId, value, createdAt) with unique constraint
- [x] Add `priorityZones` table (id, geom polygon, dominantStrategy, totalVotes, requestIds[], computedAt)
- [ ] Run `npm run db:generate && npm run db:migrate`

## Phase 2: tRPC Community Router
- [x] Create `src/lib/server/trpc/routers/community.ts`
- [x] Implement `submitRequest` mutation (lat, lon, strategyType, description, sessionId)
- [x] Implement `voteOnRequest` mutation (requestId, value +1/-1, upsert with unique constraint)
- [x] Implement `getRequests` query (bbox → requests with vote counts, sorted by votes desc)
- [x] Implement `getPriorityZones` query (bbox → zone polygons with metadata)
- [x] Register `communityRouter` in `src/lib/server/trpc/router.ts`

## Phase 3: Priority Zone Service
- [x] Create `src/lib/server/services/priority-zones.ts`
- [x] Implement DBSCAN clustering via PostGIS `ST_ClusterDBSCAN(geom, eps:=0.05, minpoints:=3)`
- [x] Compute convex hull per cluster: `ST_ConvexHull(ST_Collect(geom))`
- [x] Compute dominant strategy type and total vote count per cluster
- [x] Upsert results into `priorityZones` table

## Phase 4: BullMQ Cron Job
- [x] Create `src/lib/server/jobs/priority-zone-refresh.ts` — BullMQ repeatable job (daily at 2am)
- [x] Register job in Next.js instrumentation hook (`src/instrumentation.ts`)
- [x] Add `npm install bullmq` if not already installed (check water-scarcity track)

## Phase 5: Map Layers
- [x] Create `src/components/map/layers/StrategyRequestLayer.tsx` — circle markers with vote badge labels
- [x] Color-code by strategyType (keyline=blue, silvopasture=green, reforestation=teal, biochar=brown)
- [x] Create `src/components/map/layers/PriorityZoneLayer.tsx` — fill polygon with dominant strategy color + opacity by vote intensity
- [x] Add click handler → open CommunityPanel with selected request detail

## Phase 6: Submit Modal & Panel
- [x] Create `src/components/panels/RequestSubmitModal.tsx` — form with strategyType select + optional description
- [ ] Wire map right-click / long-press → open modal with lat/lon pre-filled
- [x] Create `src/components/panels/CommunityPanel.tsx` — request list (sorted by votes), filter bar, detail view
- [ ] Detail view: request info + "Why here?" environmental context (fire risk, water stress, soil SOC via tRPC queries)
- [ ] Link to relevant strategy cards from Track 26
