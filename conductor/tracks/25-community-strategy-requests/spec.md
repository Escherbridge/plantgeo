# Track 25: Community Strategy Request System

## Goal
Allow users to submit, browse, and vote on strategy requests pinned to map locations — identifying where communities most want interventions (keyline, silvopasture, reforestation, biochar, etc.). High-vote clusters auto-generate Priority Zone polygons that surface in recommendation layers and alert utility companies, agencies, and affected industries.

## Features

1. **Map-Pinned Strategy Requests**
   - Click any map location → "Submit Strategy Request" form
   - Fields: strategy type (keyline / silvopasture / reforestation / biochar / water harvesting / other), description (optional), contact email (optional)
   - Pin appears on map immediately; stored with lat/lon, strategy type, user/anonymous ID
   - Request visible to all users — public by default

2. **Voting System**
   - Upvote/downvote any request (one vote per user/session)
   - Vote count displayed on pin and in detail panel
   - Sort/filter requests by: vote count, recency, strategy type, proximity to current view

3. **Priority Zone Auto-Generation**
   - Cluster algorithm (DBSCAN) groups requests within 5km radius with ≥5 votes total
   - Cluster centroid + convex hull → Priority Zone polygon stored in PostGIS
   - Priority Zone has: dominant strategy type, total vote count, top requests list
   - Zones displayed as semi-transparent colored polygons (by strategy type)
   - Zones re-computed nightly via BullMQ cron job

4. **Community Request Panel**
   - Sidebar panel: list of nearby requests (sorted by vote count)
   - Click request → zoom to pin, show detail card (description, vote count, strategy context from Track 26)
   - Filter panel: strategy type checkboxes, min vote threshold slider, date range

5. **Request Detail & Context**
   - Detail card shows: request description, vote count, location name (reverse geocoded), nearby environmental context (fire risk, water stress, soil SOC from existing layers)
   - "Why here?" section: pulls relevant risk scores from Tracks 21-24 for the pinned location
   - Link to relevant strategy cards (Track 26)

## Files to Create/Modify
- `src/lib/server/db/schema.ts` — Add `strategyRequests` table (id, lat, lon, strategyType, description, votes, userId, createdAt), `requestVotes` table (requestId, userId/sessionId, value)
- `src/lib/server/db/schema.ts` — Add `priorityZones` table (id, geom polygon, dominantStrategy, totalVotes, requestIds[], computedAt)
- `src/lib/server/services/priority-zones.ts` — DBSCAN clustering + convex hull generation
- `src/lib/server/jobs/priority-zone-refresh.ts` — BullMQ nightly job to recompute zones
- `src/lib/server/trpc/routers/community.ts` — `submitRequest`, `voteOnRequest`, `getRequests` (bbox), `getPriorityZones` (bbox)
- `src/components/map/layers/StrategyRequestLayer.tsx` — Pin layer with vote count badges
- `src/components/map/layers/PriorityZoneLayer.tsx` — Priority zone polygon choropleth
- `src/components/panels/CommunityPanel.tsx` — Request list, filter controls, detail view
- `src/components/map/RequestSubmitModal.tsx` — Map-click modal for submitting requests

## Acceptance Criteria
- [ ] Clicking map opens request submission form with lat/lon pre-filled
- [ ] Submitted request appears as pin on map within 1 second
- [ ] Voting updates pin badge count in real time (optimistic update)
- [ ] Priority Zones visible as polygons when ≥5 clustered votes exist
- [ ] Community panel lists requests sorted by vote count for current viewport
- [ ] Detail card shows environmental context from existing risk layers
- [ ] Priority zones recomputed nightly (BullMQ cron)
- [ ] Anonymous users can vote (session ID) — one vote per session per request

## Dependencies
- Track 21-24 — Environmental context scores for "Why here?" section
- Track 26 — Strategy card links in detail view
- BullMQ — Nightly zone recomputation job
- PostGIS — DBSCAN clustering via `ST_ClusterDBSCAN`, convex hull via `ST_ConvexHull`

## Tech Stack Note
PostGIS `ST_ClusterDBSCAN` handles clustering server-side — no external ML library needed. Convex hulls computed with `ST_ConvexHull(ST_Collect(geom))` per cluster. Real-time vote updates use optimistic Zustand state + tRPC mutation.
