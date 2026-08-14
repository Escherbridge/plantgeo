# Track 20: Embed & API - Implementation Plan

## Phase 1: Embed Widget
- [x] Create /embed route with minimal UI
- [x] Add URL parameter parsing (center, zoom, layers, style)
- [x] Build embed code generator page
- [x] Add responsive sizing

## Phase 2: Public API
- [x] Create /api/v1 routing structure
- [x] Implement OGC Features endpoints
- [x] Add API key authentication middleware
- [x] Rate limiting per API key

## Phase 3: Proxy APIs
- [x] Routing API proxying Valhalla
- [x] Geocoding API proxying Photon
- [x] Add response caching
- [x] Error handling and status codes

## Phase 4: Documentation
- [x] Generate OpenAPI spec from routes
- [x] Build interactive API explorer page
- [x] Add code examples
- [x] Document rate limits

## Phase 5: SDK
- [ ] Create @plantgeo/sdk package
- [ ] Build map initialization helper
- [ ] Add routing and geocoding clients
- [ ] Publish to npm

## Phase 6: Polish
- [ ] White-label embed option
- [ ] SDK documentation
- [ ] Integration guides
- [ ] Example applications
