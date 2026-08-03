FROM node:22.16.0-alpine3.22 AS base

# Install dependencies only when needed. NODE_ENV must stay unset here so that
# npm ci keeps devDependencies: the build stage runs eslint, tsc, and vitest.
FROM base AS deps
RUN apk add --no-cache libc6-compat
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# Build the application
FROM base AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1

# Next.js substitutes public variables at build time. Railway only exposes Docker
# build variables declared as ARG, so keep this list explicit and public-only.
ARG RAILWAY_ENVIRONMENT_NAME
ARG NEXT_PUBLIC_PMTILES_URL
ARG NEXT_PUBLIC_TERRAIN_URL
ARG NEXT_PUBLIC_DYNAMIC_TILES_URL
ARG PLANTGEO_PMTILES_ALLOWED_HOST
ARG PLANTGEO_TERRAIN_ALLOWED_HOST
ARG PLANTGEO_DYNAMIC_TILES_ALLOWED_HOST
ENV NEXT_PUBLIC_PMTILES_URL=${NEXT_PUBLIC_PMTILES_URL}
ENV NEXT_PUBLIC_TERRAIN_URL=${NEXT_PUBLIC_TERRAIN_URL}
ENV NEXT_PUBLIC_DYNAMIC_TILES_URL=${NEXT_PUBLIC_DYNAMIC_TILES_URL}
ENV PLANTGEO_PMTILES_ALLOWED_HOST=${PLANTGEO_PMTILES_ALLOWED_HOST}
ENV PLANTGEO_TERRAIN_ALLOWED_HOST=${PLANTGEO_TERRAIN_ALLOWED_HOST}
ENV PLANTGEO_DYNAMIC_TILES_ALLOWED_HOST=${PLANTGEO_DYNAMIC_TILES_ALLOWED_HOST}

# Production builds must reject placeholders, private origins, and credential-bearing public URLs.
RUN if [ "${RAILWAY_ENVIRONMENT_NAME:-}" = "production" ]; then \
      node -e 'const checks = [ \
        ["NEXT_PUBLIC_PMTILES_URL", process.env.NEXT_PUBLIC_PMTILES_URL, false, false, process.env.PLANTGEO_PMTILES_ALLOWED_HOST], \
        ["NEXT_PUBLIC_TERRAIN_URL", process.env.NEXT_PUBLIC_TERRAIN_URL, true, false, process.env.PLANTGEO_TERRAIN_ALLOWED_HOST], \
        ["NEXT_PUBLIC_DYNAMIC_TILES_URL", process.env.NEXT_PUBLIC_DYNAMIC_TILES_URL, false, true, process.env.PLANTGEO_DYNAMIC_TILES_ALLOWED_HOST], \
      ]; \
      const net = require("node:net"); \
      for (const [name, value, requiresTemplate, requiresOrigin, allowedHost] of checks) { \
        if (!value || value.includes("<") || value.includes("your-") || value.includes("build.protomaps.com")) { \
          throw new Error(`${name} must be a reviewed production URL`); \
        } \
        let parsed; \
        try { parsed = new URL(value); } catch { throw new Error(`${name} must be an absolute URL`); } \
        const hostname = parsed.hostname.toLowerCase().replace(/^\[(.*)\]$/, "$1"); \
        if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.search || parsed.hash || net.isIP(hostname) || hostname !== allowedHost?.toLowerCase()) { \
          throw new Error(`${name} must be a credential-free HTTPS URL at its reviewed host`); \
        } \
        if (requiresTemplate && !["{z}", "{x}", "{y}"].every((token) => decodeURIComponent(parsed.pathname).includes(token))) { \
          throw new Error(`${name} must contain {z}, {x}, and {y} path placeholders`); \
        } \
        if (requiresOrigin && parsed.pathname !== "/") { \
          throw new Error(`${name} must be a Martin public origin without a path`); \
        } \
      }'; \
    fi

# Quality gates run in the image so a failure fails the build instead of the
# post-deploy healthcheck. Ordered cheapest-first: filesystem scan, then the
# type program, then eslint, then the test suite.
RUN npm run check:data-boundary
RUN npm run type-check
RUN npm run lint
RUN npm test

RUN npm run build

# Production runtime image
FROM base AS runtime
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

COPY --from=build /app/public ./public
COPY --from=build --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /app/.next/static ./.next/static

# Railway's preDeployCommand applies pending Drizzle migrations from this image.
# The standalone trace only carries the package files the app itself imports, so
# copy drizzle-orm and postgres whole — the migrator entrypoints are not traced.
COPY --from=build --chown=nextjs:nodejs /app/drizzle ./drizzle
COPY --from=build --chown=nextjs:nodejs /app/scripts/migrate.mjs ./scripts/migrate.mjs
COPY --from=build --chown=nextjs:nodejs /app/node_modules/drizzle-orm ./node_modules/drizzle-orm
COPY --from=build --chown=nextjs:nodejs /app/node_modules/postgres ./node_modules/postgres

USER nextjs
EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD wget -qO- "http://127.0.0.1:${PORT:-3000}/api/health" || exit 1

CMD ["node", "server.js"]
