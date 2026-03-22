# Track 31 — AI Regional Intelligence: Implementation Plan

## Phase 1: Claude API Setup + Regional Context Service

### 1.1 Install Anthropic SDK
- [ ] Add `@anthropic-ai/sdk` to `package.json` dependencies
- [ ] Add `ANTHROPIC_API_KEY` to `.env.example` with a placeholder comment
- [ ] Add `ANTHROPIC_MODEL` to `.env.example` defaulting to `claude-3-5-haiku-20241022`

### 1.2 Create `src/lib/server/services/regional-context.ts`
- [ ] Define `RegionalContextPayload` interface with typed fields for all six data sources
- [ ] Define `RegionalContextResult` interface: `{ payload: RegionalContextPayload; dataFreshness: Record<string, string>; cacheHit: boolean }`
- [ ] Implement `geohash5(lat, lon): string` — truncate to 2 decimal places for ~1km precision (matches existing `locationCacheKey` approach in location-context route; use precision-5 geohash for ~5km AI cache)
- [ ] Implement `assembleRegionalContext(lat, lon): Promise<RegionalContextResult>` that:
  - Checks Redis cache key `ai-context:${geohash5}` before fetching
  - Fetches all sources in parallel via `Promise.allSettled`:
    - `calculateFireRisk` + `getFireWeatherIndex` from `fire-risk.ts` / `fire-weather-index.ts`
    - `getStrategyRecommendations(lat, lon)` from `strategy-scoring.ts` (top 3)
    - `getSoilProperties(lat, lon)` from `soilgrids.ts`
    - `getDroughtClassification()` + `getStreamflowGauges(bbox)` from `drought.ts` / `usgs-water.ts`
    - `getMTBSPerimeters(bbox, year-10, currentYear)` from `mtbs.ts` — last 10 years, 0.5° bbox, summarised to 5 most recent fires
    - `getInterventionSuitability(lat, lon)` from `carbon-potential.ts`
  - Records `dataFreshness` entry for each source (ISO timestamp on success, `"unavailable"` on failure)
  - Serialises result and writes to Redis with 900s TTL
  - Returns the assembled payload regardless of partial failures (fails only if ALL sources fail)

### 1.3 Create `src/lib/server/services/ai-prompt.ts`
- [ ] Define `ConversationTurn` interface: `{ role: 'user' | 'assistant'; content: string }`
- [ ] Define `RegionalIntelligenceResponse` interface (matching spec schema) and export it
- [ ] Implement `buildSystemPrompt(): string` — returns the static system prompt establishing Claude's role as an environmental intelligence agent, output schema instructions (JSON), tone guidelines (concise, actionable, no jargon), and explicit instruction to base all claims on the provided context only
- [ ] Implement `buildUserMessage(payload: RegionalContextPayload, userQuestion?: string): string` — serialises context payload to a structured markdown block, appends the user's question (defaults to "Provide a full regional intelligence report for this location.")
- [ ] Implement `streamRegionalIntelligence(payload, history, userQuestion, signal): AsyncIterable<string>` — initialises `Anthropic` client from `ANTHROPIC_API_KEY`, calls `client.messages.stream()` with system prompt + conversation history + new user message, yields text delta chunks, and returns the final accumulated response text

## Phase 2: Streaming SSE Endpoint

### 2.1 Create `src/app/api/ai/regional-intelligence/route.ts`
- [ ] Set `export const runtime = "nodejs"` and `export const dynamic = "force-dynamic"`
- [ ] Parse and validate `lat`, `lon`, `question` (optional), `history` (optional JSON array of `ConversationTurn`) from the POST request body
- [ ] Authenticate the request using `src/lib/server/auth.ts` — return 401 if no session
- [ ] Implement sliding-window rate limiting using Redis sorted set:
  - Key: `ai-ratelimit:${userId}`
  - Remove members older than 1 hour, count remaining, reject with 429 + `Retry-After` header if >= 20
  - Add current timestamp as member on allowed requests
- [ ] Call `assembleRegionalContext(lat, lon)` — return 503 if all data sources failed
- [ ] Construct `ReadableStream<Uint8Array>` using `TextEncoder` (same pattern as `/api/stream/[layerId]/route.ts`):
  - Send `event: context\ndata: ${JSON.stringify({ cacheHit, dataFreshness })}\n\n` immediately
  - Forward each text delta chunk as `event: delta\ndata: ${JSON.stringify({ text: chunk })}\n\n`
  - On stream completion, parse the accumulated text as `RegionalIntelligenceResponse` JSON (Claude is instructed to return valid JSON)
  - Send `event: done\ndata: ${JSON.stringify(parsedResponse)}\n\n`
  - On parse failure, send `event: error\ndata: ${JSON.stringify({ message: "Response parse failed" })}\n\n`
- [ ] Wire `request.signal` to an `AbortController` passed to `streamRegionalIntelligence` so client disconnects cancel the Anthropic stream
- [ ] Return `Response` with headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache, no-transform`, `Connection: keep-alive`, `X-Accel-Buffering: no`

## Phase 3: Zustand Store + Hook + Map Click Wiring

### 3.1 Create `src/stores/regional-intelligence-store.ts`
- [ ] Define `ChatMessage` interface: `{ id: string; role: 'user' | 'assistant'; content: string; isStreaming?: boolean; parsedResponse?: RegionalIntelligenceResponse }`
- [ ] Define store state:
  - `isOpen: boolean`
  - `selectedLocation: { lat: number; lon: number } | null`
  - `messages: ChatMessage[]`
  - `isLoading: boolean`
  - `error: string | null`
  - `dataFreshness: Record<string, string>`
  - `abortController: AbortController | null`
- [ ] Implement actions:
  - `openPanel(lat, lon)` — sets location, clears messages, sets isOpen
  - `closePanel()` — calls `abortController?.abort()`, resets state
  - `setLocation(lat, lon)` — aborts in-flight stream, resets messages, sets new location
  - `addMessage(message)` — appends to messages array
  - `updateLastMessage(partial)` — updates the last message (used for streaming token accumulation)
  - `setLoading(loading)` — sets isLoading
  - `setError(error)` — sets error string
  - `setDataFreshness(freshness)` — sets dataFreshness record
  - `setAbortController(controller)` — stores the controller for cancellation

### 3.2 Create `src/hooks/useRegionalIntelligence.ts`
- [ ] Import and use `useRegionalIntelligenceStore`
- [ ] Implement `queryLocation(lat, lon, question?)`:
  - Creates new `AbortController`, stores in Zustand
  - Adds user message to store
  - Sets `isLoading: true`
  - POSTs to `/api/ai/regional-intelligence` with `{ lat, lon, question, history }`
  - Reads the `ReadableStream` response line-by-line using `getReader()`
  - On `event: context` — calls `setDataFreshness`
  - On `event: delta` — accumulates tokens into last assistant message via `updateLastMessage`
  - On `event: done` — sets `parsedResponse` on the last message, sets `isLoading: false`
  - On `event: error` — calls `setError`, sets `isLoading: false`
  - On abort — cleans up silently
- [ ] Implement `sendFollowUp(question: string)` — calls `queryLocation` with the question and existing message history

### 3.3 Wire map click to panel
- [ ] In the main map component (identify from existing `src/components/map/`), add a `onClick` handler on the MapLibre map instance
- [ ] Handler calls `useRegionalIntelligenceStore.getState().openPanel(lat, lon)` then `queryLocation(lat, lon)`
- [ ] Add a cursor change (pointer) when the AI panel mode is active (toggle via store boolean `isOpen` or a dedicated `aiModeActive` flag)

## Phase 4: RegionalIntelligencePanel UI

### 4.1 Create `src/components/panels/RegionalIntelligencePanel.tsx`
- [ ] Mark `"use client"` — interactive panel with real-time streaming
- [ ] Subscribe to `useRegionalIntelligenceStore`
- [ ] Render a slide-in panel (right side, consistent with existing panel layout) with:
  - Header: coordinates display, close button (calls `closePanel()`)
  - Loading skeleton while `isLoading` and no messages yet
  - Message list — renders each `ChatMessage`:
    - User messages: right-aligned bubble
    - Assistant messages: left-aligned, streaming text rendered via `dangerouslySetInnerHTML` on a sanitised markdown string (use `marked` or simple newline-to-`<br>` rendering — check if `marked` is already in `package.json`)
  - Structured sections (rendered from `parsedResponse` when available):
    - `RiskSummaryCard` — coloured badge (green/yellow/orange/red by level), headline, factor chips
    - `HistoricalEventsList` — date-sorted list with type icon and severity badge
    - `ActionableItemsList` — grouped by priority (immediate / short-term / long-term), each with action text and rationale
    - `InterventionRecommendationsList` — each card shows strategy name, score bar (0–100), `whyHere` text, suppliers badge
  - `DataFreshnessFooter` — collapsed disclosure showing each source name + timestamp
  - Follow-up input bar (text input + send button) — disabled while `isLoading`

### 4.2 Streaming text display
- [ ] While `isStreaming` is true on the last assistant message, show a blinking cursor after the accumulated text
- [ ] Auto-scroll the message list to the bottom on each `updateLastMessage` call (use `useEffect` + `scrollIntoView`)

### 4.3 Error state
- [ ] When `error` is non-null, show an inline error card with the error message and a "Try Again" button that re-calls `queryLocation` with the current location

## Phase 5: NDVI Point Value + Data Freshness Indicators

### 5.1 Add `getNDVIAtPoint` to `vegetation.ts`
- [ ] Implement `getNDVIAtPoint(lat, lon, year, month): Promise<number | null>`:
  - Calls NASA GIBS WMS `GetFeatureInfo` endpoint: `https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetFeatureInfo&LAYERS=MODIS_Terra_NDVI_M&QUERY_LAYERS=MODIS_Terra_NDVI_M&INFO_FORMAT=application/json&...`
  - Parses the response to extract the NDVI value at the pixel containing the point
  - Returns `null` if the service is unavailable or the value is no-data (-28672 fill value)
  - Caches result in Redis: `ndvi-point:${lat.toFixed(2)}:${lon.toFixed(2)}:${year}-${month}` with 86400s TTL
- [ ] Integrate `getNDVIAtPoint` into `assembleRegionalContext` — replace the tile URL description with the actual value when available

### 5.2 Data freshness indicators in UI
- [ ] In `DataFreshnessFooter`, colour-code each source: green if within 1 hour, yellow if within 24 hours, red if older or unavailable
- [ ] Show a warning banner at the top of the panel if more than 2 sources are unavailable

## Phase 6: Rate Limiting + Redis Caching + Cost Optimisation

### 6.1 tRPC router for non-streaming metadata
- [ ] Create `src/lib/server/trpc/routers/regional-intelligence.ts`:
  - `getLocationPreview(lat, lon)` procedure — returns cached context metadata (data freshness, cache hit/miss, estimated token count) without triggering an AI call; used by the panel header to show data availability before the user initiates a query
  - `getRateLimitStatus()` procedure — returns `{ remaining: number; resetAt: string }` for the current user; used by the UI to show a rate limit warning when the user is approaching the limit
- [ ] Register the new router in the main tRPC router (`src/lib/server/trpc/router.ts`)

### 6.2 Cost safeguards
- [ ] Add `MAX_HISTORY_TURNS = 5` constant in `ai-prompt.ts` — truncate conversation history to the last 5 turns before sending to Claude, preventing unbounded context growth
- [ ] Add `MAX_CONTEXT_TOKENS_ESTIMATE = 2500` guard — if the serialised context payload exceeds this estimate, truncate MTBS results to 3 fires and strategy reasons to 1 each
- [ ] Log each AI request to the server console with: `userId`, `lat`, `lon`, `estimatedTokens`, `cacheHit` — for cost monitoring without storing PII in the database

### 6.3 Environment variable documentation
- [ ] Ensure `.env.example` includes:
  ```
  # Anthropic AI (Track 31 — AI Regional Intelligence)
  ANTHROPIC_API_KEY=sk-ant-...
  ANTHROPIC_MODEL=claude-3-5-haiku-20241022
  ```

## File Manifest

| File | Action |
|------|--------|
| `package.json` | Add `@anthropic-ai/sdk` |
| `.env.example` | Add `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` |
| `src/lib/server/services/regional-context.ts` | Create |
| `src/lib/server/services/ai-prompt.ts` | Create |
| `src/app/api/ai/regional-intelligence/route.ts` | Create |
| `src/stores/regional-intelligence-store.ts` | Create |
| `src/hooks/useRegionalIntelligence.ts` | Create |
| `src/components/panels/RegionalIntelligencePanel.tsx` | Create |
| `src/lib/server/trpc/routers/regional-intelligence.ts` | Create |
| `src/lib/server/trpc/router.ts` | Edit — register new router |
| `src/lib/server/services/vegetation.ts` | Edit — add `getNDVIAtPoint` (Phase 5) |
| Main map component | Edit — add onClick handler |

## Dependencies

| Dependency | Status |
|-----------|--------|
| `@anthropic-ai/sdk` | Not installed — add in Phase 1 |
| `fire-risk.ts`, `fire-weather-index.ts` | Exists |
| `strategy-scoring.ts` | Exists |
| `soilgrids.ts` | Exists |
| `drought.ts`, `usgs-water.ts` | Exists |
| `mtbs.ts` | Exists |
| `carbon-potential.ts` | Exists |
| Redis client | Exists (pattern in `location-context` route) |
| `src/lib/server/auth.ts` | Exists |

## Prompt Engineering Notes

The system prompt should instruct Claude to:
1. Return a single valid JSON object matching `RegionalIntelligenceResponse` — no markdown fences, no prose outside the JSON
2. Base all claims strictly on the provided context data; never hallucinate data values
3. Quantify risk factors using the numeric scores provided (e.g., "fire risk score of 78/100")
4. For `actionableItems`, link each item to a specific data signal (e.g., "Given D3 drought classification...")
5. For `interventionRecommendations`, use only the six strategy types from `strategy-scoring.ts`
6. Keep `historicalEvents` to MTBS fire data only unless weather data provides additional events
7. `dataFreshness` should be copied directly from the context payload, not generated by the model

For follow-up questions, the system prompt remains the same; the context payload is re-included in full (it is small enough that token cost is not a concern), and prior turns are prepended as `ConversationTurn[]` messages.
