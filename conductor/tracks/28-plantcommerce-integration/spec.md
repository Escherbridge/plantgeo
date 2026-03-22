# Track 28: PlantCommerce Integration API

## Goal
Expose a clean API endpoint on PlantGeo that PlantCommerce can query for location-aware strategy context, and implement the `/api/v1/strategy-suppliers` consumption layer so strategy cards (Track 26) surface the right equipment and services from PlantCommerce suppliers at a given location.

## Features

1. **Strategy-Supplier Endpoint (Consumer)**
   - PlantGeo strategy cards call PlantCommerce's `/api/v1/strategy-suppliers?strategy={type}&lat={lat}&lon={lon}`
   - Response: array of `{ supplierId, supplierName, productName, priceRange, distance, listingUrl }`
   - Results cached in Redis (TTL 1 hour) keyed by strategy + geohash
   - Graceful degradation: empty array on timeout or non-200

2. **PlantGeo Context API (Producer)**
   - `GET /api/v1/location-context?lat={lat}&lon={lon}` — returns environmental summary for a given point
   - Response: fire risk score, drought class, water stress level, soil SOC, erosion risk, dominant land cover, active priority zones
   - Authenticated via API key (PlantCommerce registers as an API consumer)
   - Used by PlantCommerce to contextualize supplier recommendations and show relevant products

3. **Team Discovery API (Producer)**
   - `GET /api/v1/teams?lat={lat}&lon={lon}&strategy={type}` — returns teams serving that location with the requested specialty
   - Response: array of `{ teamId, teamName, orgType, specialties, verified, contactUrl }`
   - Used by PlantCommerce to recommend local practitioners alongside product listings

4. **Webhook: Priority Zone Notifications (Producer)**
   - When a new Priority Zone is created/updated, POST to PlantCommerce webhook URL
   - Payload: zone ID, geom bbox, dominant strategy, total votes, location name
   - Enables PlantCommerce to surface relevant supplier promotions in high-demand areas

5. **API Key Management**
   - `apiKeys` table: key hash, name, permissions (read:context / read:teams / webhook:receive), createdAt, lastUsedAt
   - Admin UI to issue/revoke keys (minimal — Next.js admin route)
   - Rate limit: 100 req/min per key via Redis sliding window counter

## Files to Create/Modify
- `src/app/api/v1/location-context/route.ts` — Next.js route handler, API key auth, calls strategy-scoring service
- `src/app/api/v1/teams/route.ts` — Teams discovery endpoint
- `src/lib/server/db/schema.ts` — Add `apiKeys` table
- `src/lib/server/services/plantcommerce-api.ts` — Consumer client (already in Track 26, extend here)
- `src/lib/server/services/api-auth.ts` — API key validation + rate limiting middleware
- `src/lib/server/jobs/priority-zone-webhook.ts` — BullMQ job to POST webhook on zone creation/update

## Acceptance Criteria
- [ ] `GET /api/v1/location-context` returns valid JSON for any lat/lon with API key auth
- [ ] Invalid or missing API key returns 401
- [ ] Rate limiting returns 429 after 100 req/min
- [ ] Strategy cards in Track 26 display supplier results from PlantCommerce API
- [ ] Priority zone webhook fires within 30 seconds of zone creation
- [ ] `GET /api/v1/teams` returns correct teams for location + strategy type
- [ ] Supplier results cached in Redis — second request for same location is cache hit

## Dependencies
- Track 24 (Soil Health) — SOC, erosion risk for location-context response
- Track 21-23 — Fire risk, drought, vegetation for location-context response
- Track 25 (Community Requests) — Priority zones in location-context + webhook trigger
- Track 26 (Strategy Cards) — Consumer of PlantCommerce supplier API
- Track 27 (Teams) — Producer of team discovery API

## Tech Stack Note
API key auth is a lightweight custom middleware (no OAuth needed for v1 — PlantCommerce is a trusted first-party consumer). Redis sliding window for rate limiting: `ZADD key timestamp timestamp; ZREMRANGEBYSCORE key 0 (now-60s); ZCARD key`.
