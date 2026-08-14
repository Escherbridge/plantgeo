# Track 31 — AI Regional Intelligence

## Overview

When a user clicks any point on the map, a side panel opens containing an AI agent powered by Claude (Anthropic). The agent fetches all available regional environmental data for that coordinate in parallel, assembles it as structured context, and streams back a synthesized intelligence report covering risk assessment, historical events, actionable items, and intervention recommendations. Users can then ask follow-up questions in a persistent chat interface.

## Problem Statement

PlantGeo has built rich environmental data pipelines across 10 sprints — fire risk, soil health, water scarcity, MTBS burn history, vegetation, strategy scoring, carbon potential, and more. Today that data surfaces as disconnected map layers that require expert interpretation. Non-expert land managers, community planners, and restoration teams cannot easily answer "what should I do at this specific location and why?" The AI Regional Intelligence feature bridges that gap by synthesizing all available data into actionable, location-specific guidance delivered conversationally.

## Goals

- [ ] On map click, fetch all relevant environmental data for that lat/lon in parallel (fire risk + FWI, strategy recommendations, soil properties, water scarcity, MTBS burn perimeters summary)
- [ ] Assemble a structured context payload (~1,400–2,500 tokens) and send it to Claude via the Anthropic API
- [ ] Stream Claude's response back to the client using Server-Sent Events
- [ ] Return a structured response with four sections: risk summary, historical events, actionable items, intervention recommendations
- [ ] Allow follow-up questions in a persistent chat interface within the panel
- [ ] Cache assembled context in Redis (geohash precision-5 key, 15-minute TTL) to avoid redundant upstream fetches
- [ ] Rate-limit AI requests per authenticated user (20 requests/hour, sliding window)

## Non-Goals

- This track does not implement a vector database or semantic search over historical data
- This track does not add new environmental data sources beyond what exists in Sprints 1–10
- This track implements NDVI point-value queries via NASA GIBS WMS GetFeatureInfo in Phase 5
- This track does not support anonymous (unauthenticated) AI queries
- This track persists chat history as per-location threads in PostgreSQL with 30-day retention and auto-cleanup
- This track uses Claude tool_use for structured output (RegionalIntelligenceResponse schema) rather than free-text JSON parsing

## User Stories

- As a land manager, I want to click a point on the map and immediately see a risk summary with actionable steps, so that I can make informed decisions without needing GIS expertise
- As a restoration planner, I want to ask follow-up questions about a location's history and recommended interventions, so that I can refine my project plans
- As a community organizer, I want to understand what environmental conditions exist at a specific location, so that I can communicate urgency to stakeholders
- As a PlantGeo team member, I want AI-generated intervention recommendations linked to the existing strategy scoring engine, so that the AI output stays consistent with our data
- As a returning user, I want to see my past AI conversations organized by location on my profile page, so that I can reference prior analyses without re-querying
- As a land manager revisiting a location, I want the AI panel to show my previous conversation thread for that area, so that I can continue where I left off

## Acceptance Criteria

- [ ] Clicking any map coordinate opens the RegionalIntelligencePanel within 200ms (panel open is instant; data streams in)
- [ ] All five data sources are fetched in parallel via `Promise.allSettled`; individual service failures are tolerated and noted in `dataFreshness`
- [ ] The assembled context JSON is cached in Redis under a geohash-precision-5 key with a 15-minute TTL; a second click within the same ~5km cell returns the cached context
- [ ] The `/api/ai/regional-intelligence` endpoint streams `text/event-stream` responses using the same SSE pattern as `/api/stream/[layerId]/route.ts` (ReadableStream + TextEncoder)
- [ ] The streamed response, when complete, parses into a valid `RegionalIntelligenceResponse` object matching the schema below
- [ ] `riskSummary.level` is one of: `low`, `moderate`, `high`, `critical`
- [ ] `actionableItems` contains at least one item with `priority: "immediate"` when fire risk score >= 60 or drought classification >= D2
- [ ] `interventionRecommendations` maps directly to strategies from the existing `strategy-scoring.ts` service (keyline, silvopasture, reforestation, biochar, water_harvesting, cover_cropping)
- [ ] Follow-up messages are sent to the same SSE endpoint with the full prior conversation history appended as context; the panel renders streaming tokens in real time
- [ ] Requests beyond 20/hour per userId return HTTP 429 with a `Retry-After` header
- [ ] The `@anthropic-ai/sdk` package is installed and the `ANTHROPIC_API_KEY` environment variable is documented in `.env.example`
- [ ] All TypeScript types are explicit; no `any` usage in the new files
- [ ] The panel closes cleanly and aborts any in-flight SSE stream when the user dismisses it or clicks a different map location

## Structured Output Types

```typescript
interface RegionalIntelligenceResponse {
  riskSummary: {
    level: 'low' | 'moderate' | 'high' | 'critical'
    headline: string
    factors: string[]
  }
  historicalEvents: {
    date: string
    type: string
    description: string
    severity: string
  }[]
  actionableItems: {
    priority: 'immediate' | 'short_term' | 'long_term'
    action: string
    rationale: string
    strategy?: string
  }[]
  interventionRecommendations: {
    strategy: string
    score: number
    whyHere: string
    suppliersAvailable: boolean
  }[]
  dataFreshness: Record<string, string> // source -> ISO timestamp or "unavailable"
}
```

## Data Sources Assembled Per Click

| Source | Service | Data Points |
|--------|---------|-------------|
| Fire risk score + FWI | `fire-risk.ts`, `fire-weather-index.ts` | Composite score 0–100, FWI category |
| Strategy recommendations | `strategy-scoring.ts` | Top 3 strategies with scores and reasons |
| Soil properties | `soilgrids.ts` | Organic carbon, pH, clay %, bulk density |
| Water scarcity | `drought.ts`, `usgs-water.ts` | USDM drought class, nearest gauge condition |
| MTBS burn perimeters | `mtbs.ts` | Count, most recent fire name/year/severity within 0.5° bbox |
| Carbon potential | `carbon-potential.ts` | Potential tC/ha/yr, years to saturation |

## Context Token Budget

Estimated assembled context per location:

| Section | Estimated Tokens |
|---------|-----------------|
| System prompt (role, instructions, output schema) | ~400 |
| Location metadata (lat, lon, place name if available) | ~30 |
| Fire risk + FWI data | ~120 |
| Strategy scores (top 3) | ~200 |
| Soil properties | ~150 |
| Water scarcity | ~100 |
| MTBS summary (last 5 fires) | ~200 |
| Carbon potential | ~120 |
| Prior conversation turns (follow-ups) | ~100–800 |
| **Total initial query** | **~1,320–1,520 tokens** |
| **Total with follow-ups (up to 5 turns)** | **~2,500 tokens** |

This stays comfortably within claude-haiku-4-5-20251001 context limits and keeps per-query cost low (~$0.003–0.006 per initial query at Haiku pricing).

## Technical Considerations

### Claude Model Selection
Use `claude-haiku-4-5-20251001` for the initial implementation. The low token budget and structured output requirements make Haiku the appropriate cost/performance balance. The model ID should be configurable via `ANTHROPIC_MODEL` environment variable to allow future upgrades without code changes.

### Streaming Architecture
The `/api/ai/regional-intelligence` route uses Node.js `ReadableStream` + `TextEncoder` (identical pattern to `/api/stream/[layerId]/route.ts`). The Anthropic SDK's `stream()` method returns an async iterable; each chunk is forwarded as an SSE `data:` event. A final `event: done` carries the complete parsed `RegionalIntelligenceResponse` JSON for the client to store in the Zustand store.

### Redis Caching Strategy
- Context cache key: `ai-context:${geohash5}` (geohash precision 5 = ~5km cell)
- TTL: 900 seconds (15 minutes) — matches existing `location-context` route TTL
- Only the assembled context object is cached, not the Claude response (responses vary by conversation history)
- Rate limit key: `ai-ratelimit:${userId}` — sorted set with sliding window of request timestamps

### Authentication
The endpoint requires a valid session (via existing `src/lib/server/auth.ts`). The `userId` from the session is used for rate limiting. The endpoint is not exposed through the public API key system (that is for embed partners, not AI queries).

### NDVI Gap
`vegetation.ts` currently returns only tile URLs, not point values. For this track, the context payload will include the NDVI tile URL and a descriptive label ("NDVI tile available for current month") rather than a numeric value. A `getNDVIAtPoint()` function via NASA GIBS WMS `GetFeatureInfo` is planned for Phase 5 but is not a blocking requirement for the initial release.

### Error Handling
- Individual upstream service failures are caught via `Promise.allSettled`; the context notes which sources are unavailable
- If all data sources fail, the endpoint returns HTTP 503 rather than sending an uninformed AI response
- Anthropic API errors (rate limits, service unavailable) are forwarded as SSE `event: error` events so the UI can display a user-friendly message

### Chat Persistence Schema
New database tables for per-location conversation threads:

```typescript
// In src/lib/server/db/schema.ts
export const aiConversations = pgTable('ai_conversations', {
  id: uuid('id').primaryKey().defaultRandom(),
  userId: uuid('user_id').notNull().references(() => users.id, { onDelete: 'cascade' }),
  geohash: varchar('geohash', { length: 12 }).notNull(),
  lat: doublePrecision('lat').notNull(),
  lon: doublePrecision('lon').notNull(),
  title: varchar('title', { length: 255 }).notNull(), // auto-generated from first response headline
  messageCount: integer('message_count').default(0).notNull(),
  createdAt: timestamp('created_at').defaultNow().notNull(),
  updatedAt: timestamp('updated_at').defaultNow().notNull(),
});

export const aiMessages = pgTable('ai_messages', {
  id: uuid('id').primaryKey().defaultRandom(),
  conversationId: uuid('conversation_id').notNull().references(() => aiConversations.id, { onDelete: 'cascade' }),
  role: varchar('role', { length: 10 }).notNull(), // 'user' | 'assistant'
  content: text('content').notNull(),
  structuredResponse: jsonb('structured_response'), // RegionalIntelligenceResponse or null
  tokenCount: integer('token_count'),
  createdAt: timestamp('created_at').defaultNow().notNull(),
});
```

- On map click, look up existing conversation by `userId + geohash`; reopen if found within 30 days, else create new
- A BullMQ job or cron deletes conversations older than 30 days (`DELETE FROM ai_conversations WHERE updated_at < NOW() - INTERVAL '30 days'`)
- Profile page at `/dashboard/conversations` shows list sorted by `updatedAt` with mini-map pins

### Dependencies
- `@anthropic-ai/sdk` — not yet installed; must be added to `package.json`
- All other data services already exist in `src/lib/server/services/`
- Redis client already available via existing pattern in `location-context` route
- Two new database tables: `ai_conversations`, `ai_messages`

## Open Questions

- [ ] Should the panel support saving/exporting the AI report as PDF (similar to Track 30 analytics export)? Deferred — not in scope for Track 31.
- [ ] Should `interventionRecommendations[].suppliersAvailable` query the PlantCommerce API (Track 28) in real time? Initial implementation uses a static boolean based on whether the strategy type has any active suppliers in the DB; live PlantCommerce query is a Phase 5 enhancement.
- [ ] What is the preferred UX for the panel when the user clicks a new location while a stream is still in progress — cancel and restart, or queue? Decision: cancel and restart (abort the in-flight `AbortController`, clear the store, start fresh).
- [x] Should follow-up conversation history be stored in the database? **Yes** — per-location threads in PostgreSQL with 30-day retention, profile page at `/dashboard/conversations`.
