# Track 20: Embeddable Map & Public API

## Goal
Create an embeddable map widget and public REST/GraphQL API for third-party integration, making PlantGeo a platform other applications can build on.

## Features
1. **Embed Widget**
   - `<iframe>` embed with configurable parameters
   - Embed code generator
   - Style/layer/view customization via URL params
   - Responsive sizing
   - White-label option (remove branding)

2. **Public REST API**
   - OGC API - Features compliant endpoints
   - Tile endpoints (/{z}/{x}/{y}.pbf)
   - Routing API (directions, isochrones, matrix)
   - Geocoding API (forward, reverse)
   - Feature CRUD API

3. **JavaScript SDK**
   - `@plantgeo/sdk` npm package
   - Map initialization helper
   - Routing client
   - Geocoding client
   - Real-time subscription client

4. **API Documentation**
   - OpenAPI/Swagger spec
   - Interactive API explorer
   - Code examples (JS, Python, cURL)
   - Rate limit documentation

## Files to Create/Modify
- `src/app/embed/page.tsx` - Embeddable map view
- `src/app/api/v1/` - Public API routes
- `src/app/api/v1/features/route.ts` - OGC Features API
- `src/app/api/v1/route/route.ts` - Public routing API
- `src/app/api/v1/geocode/route.ts` - Public geocoding API
- `src/app/docs/page.tsx` - API documentation page
- `packages/sdk/` - JavaScript SDK package

## Acceptance Criteria
- [ ] Embed widget loads via iframe with URL params
- [ ] REST API serves OGC-compliant feature collections
- [ ] Routing API proxies Valhalla with auth
- [ ] Geocoding API proxies Photon with auth
- [ ] OpenAPI spec is auto-generated and accurate
- [ ] SDK initializes map with one function call
