# Track 20: Embed & API - Implementation Plan

## Phase 1: Embed Widget
- [ ] Create /embed route with minimal UI
- [ ] Add URL parameter parsing (center, zoom, layers, style)
- [ ] Build embed code generator page
- [ ] Add responsive sizing

## Phase 2: Public API
- [ ] Create /api/v1 routing structure
- [ ] Implement OGC Features endpoints
- [ ] Add API key authentication middleware
- [ ] Rate limiting per API key

## Phase 3: Proxy APIs
- [ ] Routing API proxying Valhalla
- [ ] Geocoding API proxying Photon
- [ ] Add response caching
- [ ] Error handling and status codes

## Phase 4: Documentation
- [ ] Generate OpenAPI spec from routes
- [ ] Build interactive API explorer page
- [ ] Add code examples
- [ ] Document rate limits

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
