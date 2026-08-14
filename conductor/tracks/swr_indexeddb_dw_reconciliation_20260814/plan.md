# Conductor Track Execution Plan: IndexedDB SWR & Data Warehouse Reconciliation

## Phase 1: Allowlist Expansion & SWR Engine
- Modify `src/lib/cache/query-persister.ts`:
  - Add `environmental.getClimateField`, `environmental.getSoilSurvey`, and `environmental.getWatersheds` to `CACHEABLE_LAYER_QUERIES`.
  - Extend `StoredLayerQueryEntry` interface with `etag` and `dataRevision` attributes.
  - Implement `revalidateAgainstDW` logic: serves cached payload instantly, initiates background check with `If-None-Match: etag`.

## Phase 2: ETag & Revision Emission in Server Endpoints
- Add ETag and `x-data-revision` headers to tRPC geospatial routes (`environmental.ts`, `wildfire.ts`) and `/api/fires`.
- Handle HTTP 304 responses in the client persister without re-rendering or flushing active layer data.

## Phase 3: Targeted Verification
- Run targeted test suite: `npx vitest run src/__tests__/lib/cache/swr-persister.test.ts`.
