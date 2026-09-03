# PlantGeo - Open-Source 3D Geospatial Mapping Platform

## Project Overview
PlantGeo is an enterprise-grade open-source 3D mapping platform built to provide feature parity with Google Maps Platform using entirely open-source technologies.

## Tech Stack
- **Frontend**: Next.js 15 (App Router) + React 19 + TypeScript
- **3D Map Rendering**: MapLibre GL JS v5 (globe, terrain, fill-extrusion)
- **Data Visualization**: deck.gl v9 (interleaved mode with MapLibre)
- **Custom 3D**: Three.js via CustomLayerInterface
- **Tile Serving**: Martin v1.4 (Rust, PostGIS + PMTiles + MBTiles)
- **Tile Format**: PMTiles v3 on Cloudflare R2, MVT for dynamic
- **Routing**: Valhalla (multi-modal, isochrones, turn-by-turn)
- **Geocoding**: Photon (autocomplete) backed by Nominatim
- **Database**: PostgreSQL 16 + PostGIS 3.4
- **ORM**: Drizzle ORM
- **API**: tRPC v11
- **State**: Zustand (global) + Jotai (per-layer atoms)
- **Caching**: Redis 7 (cache + pub/sub)
- **Styling**: Tailwind CSS v4
- **Deployment**: Railway Pro (multi-service) + Cloudflare R2 (tiles)

## Architecture
- `src/app/` - Next.js App Router pages
- `src/components/map/` - MapLibre GL JS components
- `src/components/ui/` - Shared UI components
- `src/components/panels/` - Sidebar panels (layers, routing, search)
- `src/stores/` - Zustand stores
- `src/lib/server/` - Server-side logic (db, services, tRPC)
- `src/lib/server/db/` - Drizzle ORM schema and queries
- `src/lib/server/services/` - Backend services (routing, geocoding, realtime)
- `infra/` - Docker configs (martin, nginx, db init scripts)
- `data/` - Local tile data, sprites, fonts

## Conventions
- Use `"use client"` only when needed (map components, interactive UI)
- Dynamic import MapLibre components with `ssr: false`
- All geospatial queries go through PostGIS, never client-side for large datasets
- Use PMTiles for basemap tiles, Martin for dynamic overlay tiles
- Redis for caching GeoJSON responses and pub/sub for real-time updates
- SSE for broadcast updates (fire alerts), WebSocket for bidirectional (tracking)

## Commands
- `npm run dev` - Start Next.js dev server
- `npm run docker:up` - Start all infrastructure services
- `npm run docker:down` - Stop infrastructure
- `npm run db:generate` - Generate Drizzle migrations
- `npm run db:migrate` - Run migrations locally (production uses the Railway
  `preDeployCommand` → `scripts/migrate.mjs`; see `docs/deployment.md`
  "Deployment workflow")

## Deployment
Single path: push to `main` → Railway builds `Dockerfile` (the build stage runs
`check:data-boundary`, `type-check`, `lint`, `test`) → `preDeployCommand`
applies Drizzle migrations → healthcheck `/api/ready` → traffic. There is no
GitHub Actions pipeline. A new migration must land with a matching
`src/lib/server/db/migration-contract.ts` update in the same commit.

## Quality gates

`type-check` and `lint` are the executable half of
[`conductor/code_styleguides/typescript.md`](conductor/code_styleguides/typescript.md); the
Docker build stage runs both, so a finding below blocks the deploy rather than accumulating.

**Unused symbols are errors, in both gates.** `tsconfig.json` sets `noUnusedLocals` and
`noUnusedParameters`; `eslint.config.mjs` sets `@typescript-eslint/no-unused-vars` to `error`
with `argsIgnorePattern`/`varsIgnorePattern`/`caughtErrorsIgnorePattern` of `^_` and
`ignoreRestSiblings`. The two overlap deliberately and neither subsumes the other — TypeScript
sees unused `React` default imports and parameters inside type positions that ESLint does not,
and ESLint covers `scripts/**`, `public/**` and other JavaScript that `tsconfig`'s `include`
does not type-check.

The convention, and the only two ways to clear a finding:

- **A parameter that must exist but is not used** — a positional argument before one that is
  used, or a signature fixed by a callback contract — is prefixed `_` (`_bbox`, `_event`). Same
  for a caught error you deliberately ignore (`catch (_e)`, or drop the binding entirely).
- **An unused local, import or piece of state is deleted**, not renamed. TypeScript does not
  honour the `_` prefix for locals and neither does this convention: a `_`-prefixed local is a
  claim that something reads it, and nothing does. Write-only React state is the common case —
  it costs a re-render per setter call and reads to a reviewer as live state.

Never clear a finding with `eslint-disable`, `@ts-expect-error` or `@ts-ignore`
(`@typescript-eslint/ban-ts-comment` is already an error). If a symbol looks load-bearing but
is genuinely unreferenced, that is a dead-code finding, not a lint problem: prove it out under
the proof-before-delete contract in
`conductor/tracks/repository_conformity_hardening_20260901/spec.md` and delete it with its
tests.

## Data layers

Every data layer must satisfy the contract in [`docs/layer-lane-standard.md`](docs/layer-lane-standard.md):
a declared history horizon, a forward refresh, gap detection that authors work rather than only reporting,
governed absences, a cron for each of those three, a serving reader registered in the slider capability
catalogue, and agent tools answering at the UI-selected day with temporal and spatial neighbours. It ends in
a definition-of-done checklist -- a layer that renders is not a layer that is finished.
