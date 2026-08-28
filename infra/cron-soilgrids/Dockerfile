# Scheduled SoilGrids topsoil warm: Railway runs this container on the cronSchedule in
# railway.json, it places up to `maxCells` centroids into `public.soil_grid_cache`, and exits.
#
# WHY THIS IS NOT infra/cron-ingest/Dockerfile. Every other cron service runs a Python
# `agri-service` verb. This one runs `scripts/warm-soilgrids.mjs`, a Node driver whose whole point
# is to reuse `src/lib/server/services/soilgrids.ts` in place -- its response validation,
# d_factor handling and coverage-gap semantics -- rather than reimplement them in Python.
#
# WHY IT IS NOT THE ROOT Dockerfile EITHER. The root image ships `.next/standalone`, which
# carries neither `scripts/` nor the untranspiled `src/` the driver loads through jiti, and its
# build refuses to run without the reviewed NEXT_PUBLIC_* tile URLs. Neither is anything this
# driver needs, so it builds no Next application at all.
#
# Build context is the REPOSITORY ROOT. The Railway service's Root Directory must therefore be
# "/" with this file named as its Config-as-code path -- see infra/cron-soilgrids/railway.json
# and the note in infra/cron-ingest/Dockerfile, which fails the same way when only one of the
# two settings is changed.
FROM node:22.16.0-alpine3.22

WORKDIR /app
ENV NODE_ENV=development \
    NEXT_TELEMETRY_DISABLED=1 \
    JITI_FS_CACHE=false

# Production dependency tree only: the driver needs `postgres`, `drizzle-orm`, `zod` and `jiti`
# (a direct dependency; also reachable transitively through `@tailwindcss/node`, which is what
# let this build work before it was declared directly). The resolve is still asserted at build
# time -- a lockfile/tree drift should fail this build rather than surface as a crashed cron
# tick at 03:00.
#
# `--ignore-scripts` is load-bearing, not hygiene. package.json's `prepare` script runs `husky`,
# husky is a devDependency, and `--omit=dev` means it is not installed -- so the lifecycle script
# exits 127 (`sh: husky: not found`) and takes the whole build with it. That was latent for as
# long as Railway kept this layer cached and surfaced the moment the cache went cold, which is
# the worst possible time to discover it: the image cannot rebuild while the last good one keeps
# ticking, so nothing looks broken until a deploy needs to produce a new image. Nothing this
# driver depends on (`postgres`, `jiti`, `zod`, `drizzle-orm`) has an install script, so skipping
# them costs nothing here. The jiti assertion below is unaffected and still gates the build.
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts \
    && node -e "require.resolve('jiti')"

# The whole `src/` tree, because the driver's jiti alias maps "@" to it and the reachable set
# is defined by imports rather than by directory. Copying only `src/lib/server` would work
# today and break silently the first time an import moves.
COPY tsconfig.json ./
COPY src/ src/
COPY scripts/warm-soilgrids.mjs scripts/warm-soilgrids.mjs

# Busybox short flags, not the GNU long forms infra/cron-ingest/Dockerfile uses: that image is
# Debian and this one is Alpine, where `adduser --ingroup` is not an option that exists.
RUN addgroup -S -g 10001 plantgeo \
    && adduser -S -u 10001 -G plantgeo -H -s /sbin/nologin plantgeo

USER plantgeo

# NODE_ENV stays "development" above, deliberately: `providerUrl()` in
# src/lib/server/http/bounded-upstream.ts throws for any unconfigured upstream under
# production. SoilGrids itself hardcodes its endpoint and never calls it, but a future import
# that does would turn a warm tick into a hard failure for a reason unrelated to soil.
#
# The cell count comes from railway.json's startCommand, not from here, so the cadence and the
# per-tick budget are read in one place. Bare, the driver's own default of 50 applies.
ENTRYPOINT ["node", "scripts/warm-soilgrids.mjs"]
