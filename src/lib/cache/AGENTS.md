# src/lib/cache -- IndexedDB-backed query persistence

## What this is

`indexeddb-store.ts` is a tiny hand-rolled typed wrapper over IndexedDB (open, get, set,
delete, iterate -- one object store). `query-persister.ts` uses it to implement react-query
v5's `persister` query option (`defaultOptions.queries.persister` in `src/lib/providers.tsx`),
so that scrubbing the time slider back to a day already viewed is instant (no network
round trip) and survives a page reload.

The `persister` option is called INSTEAD of `queryFn`; it is the persister's own job to call
`queryFn` on a miss and to decide what, if anything, to store. This is exactly the shape
`@tanstack/query-async-storage-persister` uses for offline caches -- we hand-roll it here
rather than pull that package in, because the storage we back it with (IndexedDB, not
localStorage/AsyncStorage) and the predicate/TTL/eviction rules below are all bespoke to
this app's data shape anyway; there would be nothing left to reuse from the package.

## Why hand-rolled IndexedDB instead of idb / dexie / localforage

The wrapper only needs five operations on ONE object store: open-with-lazy-create, get, put,
delete, getAll. That is the entire surface `indexeddb-store.ts` exposes. A library earns its
weight when it is abstracting over real complexity (multi-store schemas, migrations, cursors,
indexes, transactions spanning several stores); none of that exists here. Pulling in a
dependency to wrap five calls to a browser API that has been stable since IE11 would be new
supply chain surface with no corresponding reduction in code.

## The allowlist rule

`isPersistableQueryKey` in `query-persister.ts` is the single gate. It opts IN explicitly:

- The tRPC router path (the query key's dot-joined first segment) must be in
  `CACHEABLE_LAYER_QUERIES`.
- The procedure's input must actually carry a `bbox` string and/or a `date` string.

Both conditions must hold. Nothing is cached by default. To add a future layer: append its
dot-joined path to `CACHEABLE_LAYER_QUERIES` -- nothing else in this file, or anywhere else,
needs to change. This is deliberate: the predicate reads only the react-query `queryKey`
(`[[routerPath...], { input, type }]`, per `@trpc/react-query`'s `getQueryKey`), so a sibling
change that adds a `date` field to a router's input is picked up automatically the next time
that query key is built -- this file never needs to know it happened.

Never add anything user-scoped, authenticated, or mutation-shaped to the allowlist. The
`persister` option only ever applies to `defaultOptions.queries`, so mutations are structurally
excluded already; the allowlist is the second layer of defense for queries.

## TTL policy

- **Historical day** (`input.date < serverCurrentDate`): `HISTORICAL_TTL_MS` = 30 days.
  Once a day is in the past, the warehouse's observations for it are immutable -- there is
  no reason to ever refetch it before the LRU eviction below would have dropped it anyway.
- **"Today" or later, or a date we cannot yet prove is in the past** (no `serverCurrentDate`
  known yet, or the query carries no `date` at all): `LIVE_TTL_MS` = 5 minutes. "Today" keeps
  accumulating observations; caching it for 30 days would make the map show a stale "today"
  after new data lands. A query with no `date` field describes "now" by construction, so it
  gets the same short TTL.

`serverCurrentDate` is read from `useTimeSliderStore` (`src/stores/time-slider-store.ts`),
which is itself sourced from the slider-capabilities payload. This module never reads the
browser clock to decide which calendar date is live -- see that store's own comment on why
that distinction matters. The device clock IS still used for `Date.now()` elapsed-time math
(has this entry's TTL expired yet?); that use is fine, because it never claims to know which
calendar day is "today".

## Storage budget and eviction

`MAX_TOTAL_CACHE_BYTES` = 50 MB. One measured viewport read is ~1,036 polygon cells; a
GeoJSON `FeatureCollection` at that size runs roughly 0.3-1 MB once `JSON.stringify`'d. 50 MB
therefore holds on the order of 50-150 cached (layer, bbox, date) combinations at once --
comfortably more than a single scrubbing session across every allowlisted layer, while still
bounding worst-case growth instead of leaving it unbounded.

Every write estimates its own size (`TextEncoder`-encoded byte length of the JSON payload)
and, before writing, evicts least-recently-used entries (oldest `lastAccessedAt` first) until
the incoming entry would fit under budget. `navigator.storage.estimate()` is consulted as an
extra sanity check, not a dependency: when it reports this origin already past 80% of its
browser-assigned quota, the working budget for that write is halved, so the cache backs off
instead of being the thing that tips the origin over. If `navigator.storage` is unavailable
(older browsers, some private-mode configurations), the static 50 MB budget applies as-is.

## Schema-version invalidation

Each stored entry is stamped with `CACHE_SCHEMA_VERSION`. On read, a stamp that does not match
the current constant is treated exactly like an expired entry: a miss, followed by deleting
the stale row. Bumping `CACHE_SCHEMA_VERSION` after changing `StoredLayerQueryEntry`'s shape
(or the meaning of any field in it) is enough to invalidate every previously-cached row,
lazily, as each is next read -- there is no need to manage IndexedDB's own database-version
upgrade machinery for this, since the object store's *structure* never changes, only the
*meaning* of what is stored in it.

## Degradation matrix

Every one of these falls through to the real `queryFn` silently -- no thrown error, no
user-visible failure, no behavior change beyond "this particular read didn't get to skip the
network this time":

| Situation | Behavior |
| --- | --- |
| SSR / vitest+jsdom (`typeof indexedDB === "undefined"`) | `isIndexedDbAvailable()` is false; persister is a pure passthrough. |
| Private browsing with IndexedDB blocked/absent | Same as above, or `indexedDB.open` throws/errors -- caught, resolves `null`, treated as unavailable for that call. |
| Quota exceeded on write | `setEntry` resolves `false`; caught in the persister's write try/catch; the freshly-fetched result is still returned to the caller. |
| Corrupt or wrong-shape stored entry | `isStoredLayerQueryEntry` rejects it; the entry is deleted and treated as a miss. |
| Query resolves but represents a failure (`availability === "request_failed"`) | `isCacheableResult` refuses to write it. (In practice the server never emits this value -- see `src/types/time-slider.ts` -- so this is defense in depth, not the common path.) |
| `queryFn` itself throws (a real network/server failure) | The throw is never caught by the persister; it propagates to react-query exactly as it would without a persister. Nothing is ever cached from a rejected promise. |

## Files

- `indexeddb-store.ts` -- the wrapper described above.
- `query-persister.ts` -- allowlist predicate, TTL policy, cacheability check, eviction, and
  the exported `indexedDbLayerQueryPersister` wired into `src/lib/providers.tsx`.

## `environmental.getSoilMoisture` (added 2026-08-06)

The most expensive answer on the allowlist to recompute and the cheapest to store. Every
request costs a PostGIS aggregation plus a Gaussian blur plus a marching-squares pass, and
the result for a whole-PNW coarse view is at most nine polygons — a few KB. The day behind
it is an ERA5-Land archive day, which is immutable once published, so `resolveCacheTtlMs`
gives any past day the 30-day historical TTL and scrubbing back to a day already seen must
never re-run the aggregation.

Its input carries `depth` and `zoom` on top of `bbox`/`date`. Both are part of the
`queryHash`, so a different depth or a different aggregation tier is a different entry
rather than a stale hit — which is the correct behaviour, and needs no change here:
`isPersistableQueryKey` only requires that a bbox or a date be present, and never inspects
the rest of the input.
