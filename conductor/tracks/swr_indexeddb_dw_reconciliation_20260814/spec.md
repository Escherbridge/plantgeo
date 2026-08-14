# Conductor Track Specification: IndexedDB SWR & Data Warehouse Reconciliation

## Track ID: `swr_indexeddb_dw_reconciliation_20260814`

### Overview
This track enhances the client-side IndexedDB caching layer into a Stale-While-Revalidate (SWR) cache engine with background DW dataset reconciliation. When a user toggles a layer or scrubs dates, IndexedDB serves stored layer payloads immediately (0 ms latency). Simultaneously, a background revalidation task sends `If-None-Match` / `ETag` checks to the DW, transferring 0 bytes on HTTP 304 Not Modified, or hot-swapping fresh data into IndexedDB on revision updates.

### Objectives
1. 100% layer coverage in `CACHEABLE_LAYER_QUERIES` (`getClimateField`, `getSoilSurvey`, `getWatersheds`, `getSoilField`, `getStreamflow`, `getGroundwater`, `getDroughtClassification`, `getWeatherForBbox`, `/api/fires`).
2. SWR background revalidation against DW dataset revisions (`x-data-revision`, `ETag`, `latestObservedDate`).
3. HTTP 304 Not Modified optimization to eliminate unnecessary payload downloads.
4. Concurrency-throttled background revalidation queue (max 2 active requests) to prevent request storms.

### Key Deliverables
- Expanded `query-persister.ts` supporting SWR metadata, revision stamps, and ETag revalidation.
- Updated `useFireData.ts` and tRPC router headers to emit dataset `ETag` and `x-data-revision`.
- Targeted feature test `src/__tests__/lib/cache/swr-persister.test.ts`.
