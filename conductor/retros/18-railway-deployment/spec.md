# Track 18: Railway Pro Deployment

## Goal
Deploy the complete PlantGeo platform to Railway Pro with all services configured, monitored, and production-ready.

## Features
1. **Service Configuration**
   - Next.js app with production build
   - Martin tile server with PostGIS connection
   - PostGIS + TimescaleDB database with volume
   - Valhalla routing engine with pre-built tiles
   - Photon geocoder with regional data
   - Redis cache service

2. **Cloudflare R2 Integration**
   - PMTiles basemap upload to R2
   - CDN configuration for tile delivery
   - Custom domain for tile endpoint
   - CORS headers for map client

3. **Environment Management**
   - Development environment
   - Staging environment
   - Production environment
   - Secret management (Railway variables)

4. **Monitoring & Observability**
   - Health check endpoints for all services
   - Logging (structured JSON)
   - Error tracking (Sentry integration)
   - Uptime monitoring

5. **CI/CD**
   - GitHub Actions workflow
   - Build and test on PR
   - Auto-deploy on merge to main
   - Database migration on deploy

6. **Domain & SSL**
   - Custom domain configuration
   - SSL certificate (automatic via Railway)
   - Subdomain routing (api., tiles., etc.)

## Files to Create/Modify
- `Dockerfile` - Production Next.js image
- `railway.json` - Railway service configuration
- `.github/workflows/deploy.yml` - CI/CD pipeline
- `infra/railway/` - Per-service Railway configs
- `scripts/deploy-pmtiles.sh` - R2 upload script
- `scripts/build-valhalla-tiles.sh` - Valhalla tile build

## Acceptance Criteria
- [ ] All 6 services deploy to Railway successfully
- [ ] Internal networking connects services
- [ ] PMTiles serve from Cloudflare R2
- [ ] Health checks pass for all services
- [ ] CI/CD pipeline deploys on merge
- [ ] Custom domain with SSL works
- [ ] Monitoring alerts on service failure
