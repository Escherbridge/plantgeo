# Track 19: Testing & Quality - Implementation Plan

## Phase 1: Setup
- [x] Configure Vitest with TypeScript
- [x] Configure Playwright *(2026-08-14: `playwright.config.ts`, chromium project,
  hermetic `e2e/` suite with network fixtures; `npm run test:e2e`)*
- [x] Set up ESLint for Next.js *(pre-existing: `eslint.config.mjs`
  core-web-vitals + typescript, gated in the Dockerfile build; checkbox was stale)*
- [x] Add pre-commit hooks *(2026-08-14: husky v9 + lint-staged —
  `.husky/pre-commit` runs `eslint --fix` on staged `*.{ts,tsx,mjs}`)*

## Phase 2: Unit Tests
- [x] Test map-store actions
- [x] Test routing service
- [x] Test geocoding service
- [x] Test GeoJSON utilities

## Phase 3: Integration Tests
- [x] Test tRPC routers with test DB
- [x] Test API endpoints
- [x] Test Redis operations *(2026-08-14:
  `src/__tests__/services/redis-operations.test.ts` — cache roundtrip/TTL/JSON,
  pub/sub delivery, error degradation via ioredis-mock)*
- [x] Test PostGIS spatial queries *(2026-08-14:
  `src/__tests__/services/postgis-spatial.test.ts` — env-gated on
  `POSTGIS_TEST_DSN`, runs the four `drizzle/tests/*.test.sql` fixtures against
  real PostGIS; fixtures corrected to the 0004 properties-geometry trigger
  contract)*

## Phase 4: E2E Tests
- [x] Test map loading *(2026-08-14: `e2e/map-loading.spec.ts` — canvas mounts,
  PMTiles fixture served, skeleton detaches, zero console/page errors)*
- [x] Test search flow *(2026-08-14: `e2e/search-flow.spec.ts` — Ctrl+K,
  intercepted `/api/geocode`, listbox selection, Recent persistence)*
- [x] Test routing flow *(2026-08-14: `e2e/routing-flow.spec.ts` written and
  `test.fixme`-gated — the routing UI is deliberately unmounted dead code per
  `src/components/map/AGENTS.md` (no click path exists); routing API/store flows
  are covered by unit/API tests above; the spec arms itself once a RoutingPanel
  is mounted)*
- [x] Test mobile layouts *(2026-08-14:
  `src/__tests__/components/MapManagerMobileLayout.test.tsx` — DockSections,
  SearchDockSection, ViewDockSection `max-sm:*` assertions, plus the
  pre-existing LayerPanel phone-overlay test)*

## Phase 5: Performance
- [x] Tile rendering benchmark *(2026-08-14:
  `src/__benchmarks__/tile-processing.bench.ts` — clustering, activity-grid,
  measurement math at per-tile scale; `npm run bench`)*
- [x] Large dataset test *(2026-08-14:
  `src/__benchmarks__/large-dataset.bench.ts` — 100k-feature runs through the
  real transforms)*
- [x] Memory profiling *(2026-08-14: heap/RSS/external deltas captured around
  the 100k-feature hot path in `large-dataset.bench.ts`)*

## Phase 6: CI Integration
- [x] GitHub Actions test workflow *(superseded 2026-08-03 by commit `778a271`:
  CI collapsed onto Railway push-deploy with the quality gates running inside
  the Dockerfile build (`check:data-boundary` → `type-check` → `lint` → `test`
  → `build`); the repo deliberately has no `.github/` — see
  `conductor/release-governance.md`)*
- [x] Coverage reporting *(2026-08-14: `@vitest/coverage-v8` installed;
  `npm run test:coverage` with v8 provider and 60% line/function thresholds in
  `vitest.config.ts`)*
- [x] Performance regression detection *(2026-08-14: committed
  `scripts/bench/baseline.json` + `npm run bench:compare` fails >30% p75
  regressions; `bench:update-baseline` refreshes — ad-hoc-script model per the
  2026-08-07 quality-gates decision, not a CI gate)*
