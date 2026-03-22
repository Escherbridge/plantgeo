# Railway Deployment Guide

Complete guide to deploying PlantGeo on Railway.app with multi-service setup.

## Prerequisites

### Accounts & Credentials

1. **Railway Account** — Sign up at https://railway.app
2. **Cloudflare Account** — For R2 storage (PMTiles tiles)
3. **GitHub Account** — For repository connection
4. **API Keys** — NASA FIRMS, Mapillary, OpenWeatherMap, Anthropic Claude

### Environment Preparation

```bash
# 1. Clone repository
git clone https://github.com/plantgeo/plantgeo.git
cd plantgeo

# 2. Create .env.local with all secrets
cp .env.example .env.local
# Fill in all API keys and credentials

# 3. Test locally
npm run docker:up
npm run db:migrate
npm run dev

# Verify at http://localhost:3000
```

## Railway Project Setup

### 1. Create Project

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login to Railway
railway login

# Create new project
railway init
# Select: Create a new project
# Name: plantgeo
```

Or via web: https://railway.app/dashboard

### 2. Add Services

#### Service 1: PostgreSQL Database

```bash
railway add
# Select: PostgreSQL
# Accept defaults
```

**Configuration:**
- Version: 16 (latest)
- Persistent volume: enabled
- Backup: daily

Railway automatically creates:
- Database: `railway`
- User: `postgres`
- Password: random (stored as `DATABASE_URL`)

#### Service 2: Redis

```bash
railway add
# Select: Redis
# Accept defaults
```

**Configuration:**
- Version: 7
- Persistent volume: enabled
- `REDIS_URL` automatically set

#### Service 3: Next.js App

```bash
railway add
# Select: GitHub
# Select repository: plantgeo
```

**Configuration in railway.json:**
```json
{
  "build": {
    "builder": "DOCKERFILE",
    "dockerfilePath": "Dockerfile"
  },
  "deploy": {
    "startCommand": "node server.js",
    "healthcheckPath": "/api/health",
    "healthcheckTimeout": 30,
    "restartPolicyType": "ON_FAILURE"
  }
}
```

#### Service 4: Martin (Tile Server)

Create Dockerfile.martin:

```dockerfile
FROM ghcr.io/maplibre/martin:v1.4

# Copy PostgreSQL connection info
ENV DATABASE_URL=$DATABASE_URL

# Expose port
EXPOSE 3100

# Start Martin
CMD ["martin"]
```

Deploy via Railway CLI:

```bash
railway service create
# Name: martin
# Select: Custom Dockerfile
# Path: Dockerfile.martin
```

#### Service 5: Valhalla (Routing)

```bash
railway service create
# Name: valhalla
# Select: Docker Image
# Image: ghcr.io/valhalla/valhalla:latest
# Port: 8002
```

## Environment Variables

### Set All Variables in Railway

Go to each service → Variables tab and add:

#### PostgreSQL Service

```
POSTGRES_PASSWORD=<random-generated>
DATABASE_URL=postgresql://postgres:<password>@<host>:5432/railway
```

#### Redis Service

```
REDIS_URL=redis://<host>:6379
```

#### Next.js App Service

```
# Database & Cache
DATABASE_URL=postgresql://postgres:<password>@postgres.<project>.railway.internal:5432/railway
REDIS_URL=redis://redis.<project>.railway.internal:6379

# Tile & Routing
MARTIN_URL=http://martin.<project>.railway.internal:3100
VALHALLA_URL=http://valhalla.<project>.railway.internal:8002
PHOTON_URL=http://photon.<project>.railway.internal:2322

# Cloudflare R2 (Storage for PMTiles)
R2_BUCKET=plantgeo-tiles
R2_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=<access-key>
R2_SECRET_ACCESS_KEY=<secret-key>

# Public URLs
NEXT_PUBLIC_PMTILES_URL=https://<r2-domain>/basemap.pmtiles
NEXT_PUBLIC_MAP_STYLE_URL=http://martin.<project>.railway.internal:3100/style.json

# Authentication
NEXTAUTH_SECRET=<openssl rand -base64 32>
NEXTAUTH_URL=https://<your-railway-domain>.railway.app

# OAuth Providers
GOOGLE_CLIENT_ID=<google-oauth-id>
GOOGLE_CLIENT_SECRET=<google-oauth-secret>
GITHUB_CLIENT_ID=<github-oauth-id>
GITHUB_CLIENT_SECRET=<github-oauth-secret>

# External APIs
NASA_FIRMS_KEY=<api-key>
MAPILLARY_ACCESS_TOKEN=<token>
ANTHROPIC_API_KEY=sk-ant-<key>

# PlantCommerce Integration
PLANTCOMMERCE_API_URL=https://plantcommerce.example.com
PLANTCOMMERCE_WEBHOOK_SECRET=<hmac-secret>

# Admin Token
ADMIN_API_TOKEN=<random-token>
```

#### Martin Service

```
DATABASE_URL=postgresql://postgres:<password>@postgres.<project>.railway.internal:5432/railway
MARTIN_PORT=3100
```

#### Valhalla Service

```
# Valhalla doesn't require environment vars
# but mount PBF data volume (see below)
```

## Service Networking

Railway services in same project communicate via internal DNS:

```
postgres.<project>.railway.internal:5432
redis.<project>.railway.internal:6379
martin.<project>.railway.internal:3100
valhalla.<project>.railway.internal:8002
```

**Example connection string:**
```
postgresql://postgres:password@postgres.plantgeo.railway.internal:5432/railway
```

## Database Setup

### Run Migrations

After deploying Next.js service, run migrations:

```bash
# Connect to Railway project
railway shell

# Run migrations
npm run db:migrate

# Verify
psql $DATABASE_URL -c "SELECT table_name FROM information_schema.tables WHERE table_schema='public';"
```

### Initialize PostGIS Extensions

```bash
railway connect
# or
psql $DATABASE_URL
```

```sql
-- Create extensions
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Verify
SELECT version();  -- Shows PostGIS version
```

### Seed Initial Data

```bash
# Create global layers
npm run seed:layers

# Create admin user
npm run seed:admin-user
```

## Health Checks & Monitoring

### Configure Health Checks

Railway automatically hits `/api/health` (from railway.json):

```typescript
// src/app/api/health/route.ts
export async function GET() {
  try {
    // Check database
    await db.select().from(users).limit(1);

    // Check Redis
    await redis.ping();

    return Response.json({
      status: 'ok',
      services: {
        db: 'ok',
        redis: 'ok',
        timestamp: new Date().toISOString(),
      },
    });
  } catch (error) {
    return Response.json(
      { status: 'error', message: error.message },
      { status: 500 }
    );
  }
}
```

### Monitoring Dashboard

Railway provides:
- **Logs** — Real-time service logs
- **Metrics** — CPU, memory, request count
- **Deployments** — History and rollback
- **Alerts** — On service failure

Access via Railway Dashboard → Project → Service

## Build & Deployment Pipeline

### Docker Build Process

```dockerfile
# Dockerfile (src/app already optimized Next.js)
FROM node:22-alpine AS base

FROM base AS deps
RUN apk add --no-cache libc6-compat
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM base AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM base AS runtime
WORKDIR /app
ENV NODE_ENV=production

RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

COPY --from=build /app/public ./public
COPY --from=build --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
ENV PORT=3000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD wget -qO- http://localhost:3000/api/health || exit 1

CMD ["node", "server.js"]
```

### Deployment Steps

1. **Push to GitHub**
   ```bash
   git push origin main
   ```

2. **Railway auto-deploys**
   - Builds Docker image
   - Runs migrations
   - Starts service
   - Health checks pass → service live

3. **Verify deployment**
   ```bash
   railway logs
   railway env
   ```

## Scaling & Performance

### Horizontal Scaling

**Next.js Service:**
- Set replicas: 2-3 for high traffic
- Railway load balances across replicas
- Sticky sessions: via Redis store

**Database:**
- PostgreSQL vertical scaling only (upgrade plan)
- Use read replicas for analytics queries
- Connection pooling: PgBouncer

### Vertical Scaling

Upgrade Railway plan to increase:
- CPU cores
- Memory per service
- Storage for database

### Optimization

```typescript
// 1. Enable response compression
app.use(compression());

// 2. Use Redis caching aggressively
const cached = await redis.get(key);
if (!cached) {
  const data = await fetchExpensiveData();
  await redis.setex(key, 3600, JSON.stringify(data));
}

// 3. Batch database queries
const [users, teams] = await Promise.all([
  db.select().from(users),
  db.select().from(teams),
]);

// 4. Use connection pooling
DATABASE_URL=postgresql://...?sslmode=require&pool=10
```

## Cloudflare R2 Setup

### Create R2 Bucket

1. Go to Cloudflare Dashboard → R2
2. Click "Create bucket"
3. Name: `plantgeo-tiles`
4. Region: wnam (default)

### Generate API Token

1. Settings → API Tokens
2. Create token with R2 permissions
3. Copy:
   - `R2_ACCESS_KEY_ID`
   - `R2_SECRET_ACCESS_KEY`

### Upload PMTiles

```bash
# Install rclone
brew install rclone

# Configure R2
rclone config create r2 s3 \
  provider Cloudflare \
  access_key_id $R2_ACCESS_KEY_ID \
  secret_access_key $R2_SECRET_ACCESS_KEY \
  endpoint $R2_ENDPOINT \
  region auto

# Upload basemap
rclone copy basemap.pmtiles r2:plantgeo-tiles/
```

### Public URL

Enable public read access:

```
https://<bucket>.r2.dev/<file>
# Example: https://plantgeo-tiles.r2.dev/basemap.pmtiles
```

## CI/CD Automation

### GitHub Actions Workflow

Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy to Railway

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Install Railway CLI
        run: npm install -g @railway/cli

      - name: Deploy
        env:
          RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}
        run: |
          railway up --detach
          railway run npm run db:migrate

      - name: Health check
        run: |
          sleep 30
          curl -f https://${{ secrets.RAILWAY_DOMAIN }}/api/health
```

### Pre-deployment Checks

```bash
# Lint code
npm run lint

# Run tests
npm run test

# Build locally
npm run build

# Check bundle size
npm run build -- --analyze
```

## Troubleshooting

### Service Won't Start

```bash
# View logs
railway logs

# Common issues:
# - Missing env var: check Variables tab
# - Migration failed: run manually
# - Health check timeout: increase in railway.json
```

### Database Connection Error

```bash
# Verify connection string
echo $DATABASE_URL

# Test connection
psql $DATABASE_URL -c "SELECT 1"

# Check firewall rules (Railway allows all internal)
```

### OutOfMemory Error

```bash
# Check memory usage
railway env
# Look for memory in metrics

# Increase service memory
railway service
# Select service → Change plan
```

### High Latency

```bash
# Check region
railway env
# Deploy closer service if available

# Enable caching
# Increase Redis TTLs
# Use CDN for static assets
```

## Backup & Disaster Recovery

### PostgreSQL Backups

Railway creates daily backups automatically:
- **Retention:** 7 days (Pro plan)
- **Access:** Railway Dashboard → Backups
- **Restore:** Click backup → Restore

Manual backup:
```bash
pg_dump $DATABASE_URL > plantgeo-backup.sql
```

### Restore from Backup

```bash
# Download backup file
railway run psql $DATABASE_URL < plantgeo-backup.sql
```

### Redis Persistence

Enabled by default:
- **RDB snapshots:** Hourly
- **AOF logs:** Real-time
- **Retention:** Matches backup retention

## Rollback & Versioning

### Rollback Deployment

Railway keeps last 10 deployments:

```bash
railway env
# Shows current deployment

railway deployments ls
# Lists all deployments

railway deployments rollback <deployment-id>
```

### Semantic Versioning

Tag releases:

```bash
git tag -a v1.0.0 -m "First production release"
git push origin v1.0.0

# Railway can auto-deploy tags
```

## Security Best Practices

### Network Security

```
✓ All internal traffic encrypted
✓ Public domains via HTTPS only
✓ Database isolated (no public IP)
✓ API keys in environment variables (not code)
```

### Secret Management

```bash
# Store secrets in Railway Variables
# Never commit .env files
echo ".env.local" >> .gitignore

# Rotate API keys regularly
# Use separate keys per environment
```

### Authentication

```typescript
// NextAuth.js configuration
export const authOptions: NextAuthOptions = {
  providers: [
    GoogleProvider({
      clientId: process.env.GOOGLE_CLIENT_ID!,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET!,
    }),
  ],
  callbacks: {
    async signIn({ user, account }) {
      // Add custom logic
      return true;
    },
  },
};
```

### Rate Limiting

```typescript
// Implement rate limiting middleware
import rateLimit from 'express-rate-limit';

const limiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 100,
  message: 'Too many requests from this IP',
});

app.use('/api/', limiter);
```

## Production Checklist

- [ ] All environment variables set
- [ ] Database migrations completed
- [ ] PostGIS extensions installed
- [ ] Redis persistence enabled
- [ ] Cloudflare R2 configured and tiles uploaded
- [ ] NextAuth.js secrets generated
- [ ] OAuth providers configured
- [ ] External API keys obtained
- [ ] Health checks passing
- [ ] Monitoring/alerting configured
- [ ] Backup strategy documented
- [ ] SSL/HTTPS enforced
- [ ] CORS configured appropriately
- [ ] Rate limiting enabled
- [ ] Logging aggregation set up
- [ ] Load testing completed
- [ ] Runbooks created for common issues

## Support

- **Railway Docs:** https://docs.railway.app
- **PlantGeo Issues:** https://github.com/plantgeo/plantgeo/issues
- **Community:** Discord/Slack channels
