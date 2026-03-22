# Deep Dive Spec: PlantGeo Sprint Completeness + AI Regional Intelligence

**Source:** deep-dive (trace → interview pipeline)
**Interview ID:** plantgeo-sprint-completeness
**Ambiguity:** ~12% (below 20% threshold)
**Date:** 2026-03-21

## Goal

Bring PlantGeo Sprints 8-10 (Tracks 21-30) to production readiness by fixing critical bugs, wiring disconnected UI components, and replacing mock data with real service calls. Then implement Track 31: AI Regional Intelligence — a map-click AI agent that RAGs on all regional environmental data and returns structured, actionable intelligence via Claude API streaming.

## Constraints

- **SDK:** `@anthropic-ai/sdk` (direct Claude API, not Vercel AI SDK)
- **Model:** `claude-haiku-4-5-20251001` for cost/speed balance (~$0.0004/query)
- **Output:** Structured JSON via Claude `tool_use` (not free-text parsing)
- **Auth:** Required — no anonymous AI queries; rate limit 20 req/hour per userId
- **Chat persistence:** Per-location threads in PostgreSQL (geohash key), 30-day retention with auto-cleanup
- **NDVI:** Required for v1 — add `getNDVIAtPoint()` via NASA GIBS WMS GetFeatureInfo
- **Streaming:** SSE via ReadableStream (matching existing `/api/stream/` pattern)
- **Caching:** Redis geohash-precision-5 keys, 15-min TTL for assembled context
- **No vector DB:** All data is structured and point-queryable; direct context injection (~1,400-2,500 tokens)

## Non-Goals

- No new environmental data sources beyond existing Sprints 1-10
- No PDF export of AI reports (deferred)
- No real-time PlantCommerce supplier queries (static boolean for v1)
- No anonymous access to AI endpoints

## Acceptance Criteria

### Sprint 8-10 Bug Fixes (completed)
- [x] XSS via unsanitized popup HTML in WaterLayer — `escapeHtml()` applied to all dynamic values
- [x] Unreachable groundwater "critical" trend branch — threshold order reversed
- [x] Duplicate Redis singletons in 4 service files — consolidated to shared `getRedis()`
- [x] NDVI anomaly/absolute modes return identical URLs — anomaly now uses `MODIS_Combined_NDVI`
- [x] Non-null session assertion in `contributorProcedure` — explicit guard added
- [x] Vote count inflation race condition — `.returning()` check before increment
- [x] `"use client"` not first statement in AnalyticsDashboard — moved to line 1
- [x] Unvalidated bbox inputs — `bboxSchema` regex added to environmental + community routers

### Sprint 8-10 Integration Fixes (completed)
- [x] All 8 new panels wired into UI via PanelManager.tsx with icon toolbar
- [x] AlertBell mounted in MapView.tsx top-right controls
- [x] All new map layers mounted via LayerManager.tsx with dynamic imports
- [x] All 5 BullMQ jobs registered in instrumentation.ts
- [x] `bullmq` added to package.json dependencies
- [x] `strategy-scoring.ts` waterStress now calls real drought + USGS services

### Track 31: AI Regional Intelligence
- [ ] Map click opens RegionalIntelligencePanel within 200ms
- [ ] 5 data sources fetched in parallel via `Promise.allSettled`; failures tolerated
- [ ] Assembled context cached in Redis (geohash-5 key, 15-min TTL)
- [ ] `/api/ai/regional-intelligence` streams SSE with events: context, delta, done, error
- [ ] Claude `tool_use` returns valid `RegionalIntelligenceResponse` with 4 structured sections
- [ ] `riskSummary.level` correctly reflects fire risk >= 60 or drought >= D2
- [ ] `interventionRecommendations` maps to the 6 strategy types from strategy-scoring.ts
- [ ] Follow-up chat with max 5 turns per session, full history sent to Claude
- [ ] Conversations persisted as per-location threads (userId + geohash)
- [ ] Revisiting same ~5km area reopens previous conversation thread
- [ ] Profile page at `/dashboard/conversations` lists past threads with mini-map
- [ ] Conversation detail page shows full history with structured response cards
- [ ] 30-day auto-cleanup via BullMQ cron job
- [ ] Rate limit: 20/hour per userId, HTTP 429 with Retry-After header
- [ ] `getNDVIAtPoint()` returns numeric NDVI value via NASA GIBS WMS
- [ ] `@anthropic-ai/sdk` installed, `ANTHROPIC_API_KEY` in `.env.example`
- [ ] Zero `any` types in new files; TypeScript clean

## Assumptions Exposed

1. NASA GIBS WMS supports GetFeatureInfo for MODIS_Terra_NDVI_M — needs verification via probe call
2. `claude-haiku-4-5-20251001` handles `tool_use` with the RegionalIntelligenceResponse schema reliably
3. BullMQ is available in the deployment environment (Railway) — confirmed via package.json addition
4. The 4 remaining mock factors in strategy-scoring.ts (fireRisk, vegetationDegradation, communityDemand) will be wired to real services in future sprints; waterStress is now real

## Technical Context

### Existing Infrastructure
- **14 tRPC routers** registered including environmental (11 procedures), community, strategy, alerts
- **5 BullMQ jobs** registered in instrumentation.ts
- **SSE streaming** at `/api/stream/[layerId]/route.ts` — proven pattern with ReadableStream + Redis pub/sub
- **Location-context API** at `/api/v1/location-context/route.ts` — already aggregates fire+soil+drought+NDVI+zones with `Promise.allSettled`
- **Redis caching** throughout services with consistent TTL patterns

### Architecture: Direct Context Injection
```
Map Click (lat, lon)
  → assembleRegionalContext() [parallel fetch 6 services]
  → Redis cache check (geohash-5 key)
  → buildSystemPrompt() + buildUserMessage(context)
  → Claude API stream (tool_use: regional_intelligence_report)
  → SSE events → RegionalIntelligencePanel
  → Persist to ai_conversations + ai_messages
```

Token budget per query: ~1,400-2,500 tokens input, ~800 tokens output
Cost: ~$0.0004/query at Haiku pricing (~$0.40/day at 1000 queries)

### New DB Tables
- `ai_conversations`: id, userId, geohash, lat, lon, title, messageCount, createdAt, updatedAt
- `ai_messages`: id, conversationId, role, content, structuredResponse (jsonb), tokenCount, createdAt

## Trace Findings

### Lane 1: Implementation Completeness
- **Confirmed:** Multiple services use hardcoded/placeholder values (percentile=null, rainfall=600, 4/5 strategy factors are coordinate-based mocks)
- **Fixed:** waterStress now uses real drought + USGS data; other 3 factors remain TODO for future tracks
- **Risk:** `getReforestationZones` returns mock rectangles — documented as known limitation

### Lane 2: Integration Wiring
- **Confirmed:** All 8 new panels and 9 new map layers were built but never mounted in the render tree
- **Fixed:** PanelManager.tsx, LayerManager.tsx, AlertBell mounted in MapView.tsx
- **Confirmed:** tRPC routers were correctly registered (the one layer that was properly wired)

### Lane 3: AI RAG Architecture
- **Confirmed:** Direct context injection is correct — ~1,400 tokens per location, well within context window
- **Confirmed:** No vector DB needed — all data is structured and point-queryable
- **Gap identified:** NDVI has no point-value query → user chose to require it for v1
- **Gap identified:** No AI SDK installed → user chose @anthropic-ai/sdk

## Interview Transcript

| Round | Question | Answer |
|-------|----------|--------|
| 1 | AI SDK choice | @anthropic-ai/sdk (direct Claude API) |
| 1 | Output format | Structured JSON sections via tool_use |
| 1 | Auth gate | Required (authenticated only) |
| 2 | NDVI in v1 | Required — add NASA GIBS point query |
| 2 | Claude model | claude-haiku-4-5-20251001 |
| 2 | Follow-up chat | Yes + persistent history + profile page |
| 3 | Chat storage | Per-location threads (geohash key) |
| 3 | Retention | 30 days with auto-cleanup |
