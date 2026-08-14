# Track 18: Railway Deployment - Implementation Plan

## Phase 1: Dockerfiles
- [x] Create production Next.js Dockerfile (multi-stage)
- [ ] Test all Docker images build correctly
- [ ] Optimize image sizes

## Phase 2: Railway Configuration
- [x] Create Railway project with all services
- [x] Configure environment variables
- [ ] Set up PostGIS with volume
- [ ] Deploy Martin with PostGIS connection

## Phase 3: Valhalla & Photon
- [ ] Build Valhalla tiles for target region
- [ ] Deploy Valhalla with volume
- [ ] Import Photon data for target region
- [ ] Deploy Photon with volume

## Phase 4: Cloudflare R2
- [x] Create R2 bucket for PMTiles
- [x] Upload basemap PMTiles
- [ ] Configure CORS and caching
- [ ] Test PMTiles loading from R2

## Phase 5: CI/CD
- [x] Create GitHub Actions workflow
- [x] Add build, lint, type-check steps
- [x] Configure Railway deploy triggers
- [ ] Add database migration step

## Phase 6: Production
- [ ] Configure custom domain
- [ ] Set up health monitoring
- [ ] Add Sentry error tracking
- [ ] Load test with expected traffic
