# Track 28: PlantCommerce Integration API — Implementation Plan

## Phase 1: Database Schema
- [x] Add `apiKeys` table to schema (id, keyHash, name, permissions text[], createdAt, lastUsedAt, revokedAt)
- [x] Run `npm run db:generate && npm run db:migrate`

## Phase 2: API Auth Middleware
- [x] Create `src/lib/server/middleware/api-auth.ts`
- [x] Implement `validateApiKey(request)` — extract `X-Api-Key` header, SHA-256 hash, lookup in db, check not revoked
- [x] Implement `checkRateLimit(keyId)` — Redis sliding window INCR+EXPIRE (100 req/min)
- [x] Return 401 on invalid key, 429 on rate limit exceeded with `Retry-After` header

## Phase 3: Location Context Endpoint
- [x] Create `src/app/api/v1/location-context/route.ts`
- [x] Apply `validateApiKey` + `checkRateLimit` middleware
- [x] Fetch in parallel: fire risk (Track 21), drought class (Track 22), soil SOC (Track 24), NDVI tile URL (Track 23), active priority zones (Track 25)
- [x] Return normalized JSON: `{ fireRisk, droughtClass, soilProperties, ndviTileUrl, priorityZones[], bbox }`
- [x] Cache response in Redis (TTL 15 min) keyed by `location-context:{lat.toFixed(2)}:{lon.toFixed(2)}`

## Phase 4: Teams Discovery Endpoint
- [x] Create `src/app/api/v1/teams/route.ts`
- [x] Apply `validateApiKey` middleware
- [x] Query `teams` table with specialty filter + bbox distance filter
- [x] Return GeoJSON FeatureCollection: `{ teamId, teamName, orgType, specialties, verified, contactUrl }[]`

## Phase 5: PlantCommerce Consumer Client
- [x] Extend `src/lib/server/services/plantcommerce-api.ts` (created in Track 26)
- [x] Add `PLANTCOMMERCE_API_URL` + `PLANTCOMMERCE_WEBHOOK_URL` + `PLANTCOMMERCE_WEBHOOK_SECRET` env vars to `.env.example`
- [x] Add `retryWithBackoff(fn, maxRetries=2)` helper
- [x] Add `encodeGeohashKey(lat, lon)` — precision-2 decimal truncation for cache keys
- [x] Cache results in Redis: key = `suppliers:{strategyType}:{geohash}`, TTL 1 hour

## Phase 6: Priority Zone Webhook
- [x] Create `src/lib/server/jobs/priority-zone-webhook.ts`
- [x] `dispatchPriorityZoneWebhook()` — POST all current priority zones to webhook URL
- [x] POST payload: `{ event, zones[{ zoneId, bbox, strategyType, totalVotes, locationName }], timestamp }`
- [x] Sign with HMAC-SHA256 using webhook secret in `X-Signature` header
- [x] Graceful skip when `PLANTCOMMERCE_WEBHOOK_URL` is not set

## Phase 7: Admin Key Management
- [x] Add `src/app/api/v1/admin/keys/route.ts` — POST to create key (generates random 32-byte token, stores SHA-256 hash), DELETE to revoke
- [x] Protect with session auth (platformRole = admin) or ADMIN_API_TOKEN Bearer token
