FROM node:22.16.0-alpine3.22 AS base

# Install dependencies only when needed
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
ENV NEXT_PUBLIC_PMTILES_URL=${NEXT_PUBLIC_PMTILES_URL}
ENV NEXT_PUBLIC_TERRAIN_URL=${NEXT_PUBLIC_TERRAIN_URL}
ENV NEXT_PUBLIC_DYNAMIC_TILES_URL=${NEXT_PUBLIC_DYNAMIC_TILES_URL}

# Production builds must not silently compile local or placeholder tile origins.
RUN if [ "${RAILWAY_ENVIRONMENT_NAME:-}" = "production" ]; then \
      case "${NEXT_PUBLIC_PMTILES_URL:-}" in \
        ""|*"<"*|*"your-"*|*"build.protomaps.com"*) echo "NEXT_PUBLIC_PMTILES_URL must be a PlantGeo-controlled production URL" >&2; exit 1 ;; \
      esac; \
      case "${NEXT_PUBLIC_DYNAMIC_TILES_URL:-}" in \
        ""|*"localhost"*|*"railway.internal"*) echo "NEXT_PUBLIC_DYNAMIC_TILES_URL must be Martin's public HTTPS origin" >&2; exit 1 ;; \
      esac; \
    fi
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

USER nextjs
EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD wget -qO- "http://127.0.0.1:${PORT:-3000}/api/health" || exit 1

CMD ["node", "server.js"]
