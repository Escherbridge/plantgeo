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

The wrapper needs open-with-lazy-create, get, put, delete, getAll, a forward cursor and a
batched delete on ONE object store. That is the entire surface `indexeddb-store.ts` exposes. A
library earns its weight when it is abstracting over real complexity (multi-store schemas,
migrations, indexes, transactions spanning several stores); none of that exists here. Pulling in
a dependency to wrap seven calls to a browser API that has been stable since IE11 would be new
supply chain surface with no corresponding reduction in code.

`forEachEntry` exists alongside `getAllEntries` for one reason: `getAll()` deserializes the
whole store into a single array, so every pass costs as much resident memory as the cache is
large. The cursor holds one payload at a time, so a sweep's peak memory is one entry however far
the store grows. Every production pass (capacity, expiry sweep, the sync index's scan) uses the
cursor and keeps only small metadata; `getAllEntries` survives for tests, which read whole rows
on purpose.

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

  **A past day is NOT immutable in this warehouse, and nothing here may assume it is.** This
  file said the opposite until 2026-08-16 and the claim was wrong: the agri gap-reopen lane
  reopens a past window and republishes it (`sql/ingest/reopen_gap_windows.sql`; commit
  `c14e36b` caps it at five generations *because* it recurs), and the USDM/ERA5 lanes backfill.
  A 30-day TTL is safe only because background revalidation runs for historical days too and
  rewrites the entry in place when the warehouse's answer has moved — see "revalidation policy".
  Remove that and a corrected day cannot reach a reader who already has the old one for a month,
  while the sync track draws "saved on this device" over it.
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

## Storage budget, expiry sweep and eviction

### The metadata store

Two object stores, not one (database version 2): the payloads, and one small row per payload
holding `expiresAt`, `lastAccessedAt`, `approxByteSize`, `layerId` and `day`. Both are written in
a single transaction, so a payload never exists unindexed for more than that transaction.

**This split is what makes a large budget affordable, and it is not an optimisation detail.** A
sweep that reads payloads structured-clone-deserializes every one of them on the main thread —
at 512 MB and ~98 KB an entry that is ~5,300 GeoJSON deserializations *per app mount*, since the
sync index hydrates on every mount. Worse, IndexedDB serialises readwrite transactions behind an
overlapping readonly one, so that pass would block every `setEntry` behind it, including the
recency bump fired on every cache hit. Sweeps therefore read `getAllMetadata()` plus a key-only
`getAllKeys()` of the payload store: cost scales with the NUMBER of entries, not their size, and
no payload is touched. Recency bumps write metadata only — ~100 bytes per hit instead of
re-serializing the payload that was just read.

`forEachEntry` (the payload cursor) survives for exactly one job: backfilling metadata for rows
written before the split. It runs once per key, ever. Nothing on a recurring path may use it.
Metadata whose payload has vanished, and payloads the backfill could not read, are swept as dead.

### The budget

`MAX_TOTAL_CACHE_BYTES` = 512 MB, raised from 50 MB on 2026-08-16. The 50 MB figure was measured
against scrubbing sessions; a real production profile then measured what happens under it:
**49.57 MB of 50 MB — 99.1% full, 504 entries at ~98 KB each**. A saturated cache evicts on
essentially every write, which for a feature whose entire point is accumulating synced days means
days silently un-syncing themselves. Disk is not the constraint and never was; main-thread time
was, and the metadata split removes it. 512 MB is roughly a year of daily entries for the rows a
reader actually scrubs, at the measured per-entry size.

**Why a generous static cap is safe here, and a conservative one would not be.** The static
number is not the real ceiling and is not trying to be:

- `navigator.storage.estimate()` halves the working budget when the origin passes 80% of its
  browser-assigned quota. On Chrome's tens-of-gigabytes quota that never fires — which is the
  point: there is nothing to protect against there.
- **A refused write is what actually sets the ceiling.** `putEntryWithMetadata` resolving `false`
  is almost always `QuotaExceededError`, and it is the only honest measurement of a limit the
  browser will not otherwise disclose (iOS Safari binds far below 512 MB and, before 16.4,
  exposes no `estimate()` at all). On a refusal the cache records a learned ceiling at 90% of the
  live total, invalidates its running totals, evicts down to it and retries the write once. Every
  later budget check honours the learned ceiling, floored at `MIN_CACHE_BUDGET_BYTES` (16 MB).

  Without that, the first refusal froze the running total above the browser's real limit and
  every subsequent write short-circuited on a check that could never pass — the whole cache
  failing silently for the rest of the session, with nothing observing it. The old code re-read
  the entire store on every write, so it noticed by accident; the running totals removed that
  accident and had to replace it deliberately.

The asymmetry is the argument: a cap set too high is corrected by the browser at the first
refusal, while a cap set too low silently caps the offline feature and nothing ever corrects it.

### Running totals, sweeping and eviction

Entry sizes are estimated at write time (`TextEncoder`-encoded byte length of the JSON payload).
Two running totals — the live byte total and the last effective budget — make the write path cost
no read at all: a write that fits returns immediately. Only a write that does not (or the first
of a session) pays for a pass, and **passes are serialized behind one promise**, so twelve layer
queries mounting at once collapse into one pass rather than twelve that would each clobber the
others' totals with a snapshot taken before them. Writes landing during a pass are re-added to
its result rather than lost, and `deleteEntries` reports whether its transaction COMMITTED —
IndexedDB transactions being atomic, that is strictly more informative than a count of requests
that appeared to succeed before an abort rolled them back. An uncommitted delete invalidates the
total instead of lowering it: a total that drifts DOWN under-evicts forever.

A pass does two things:

1. **Sweeps expired rows.** Nothing used to drop an entry that had expired and was never read
   again, and the same production profile measured **235 of 504 entries (47%) expired but still
   resident**, holding budget against live data. The pass deletes them (and any row whose shape
   or schema version no longer parses) before considering eviction at all.
2. **Evicts least-recently-used live rows** (oldest `lastAccessedAt` first) only if the incoming
   entry still would not fit.

The sync index's hydration scan is the same pass, so a session typically pays for one.

## Schema-version invalidation

Each stored entry is stamped with `CACHE_SCHEMA_VERSION`. On read, a stamp that does not match
the current constant is treated exactly like an expired entry: a miss, followed by deleting
the stale row. Bumping `CACHE_SCHEMA_VERSION` after changing the meaning of a stored VALUE is
enough to invalidate every previously-cached row, lazily, as each is next read -- there is no
need to manage IndexedDB's own database-version upgrade machinery for this, since the object
store's *structure* never changes, only the *meaning* of what is stored in it.

**The 2026-08-16 attribution fields did NOT bump it, deliberately.** `layerId`, `day` and
`lastRevalidatedAt` are additive and optional; no payload changed meaning. The one argument for
bumping was that an unstamped entry would be reported as "not synced", which is a lie — but the
key-parse fallback below attributes those entries exactly, so the lie never existed, and a bump
would have thrown away a measured 504 live entries (49.6 MB, most of a working cache) to fix a
problem that was already solved.

## Layer/day attribution, and the shared router paths

Each entry is stamped at write time with the `LayerToggleId` it draws on and the day it answers
for (`input.date`). Without them the store is a heap of opaque hashes and no UI can say which
days a layer already holds.

**The discriminator problem.** Two allowlisted paths do not serve one layer each:

- `environmental.getSoilField` backs `soil-moisture`, `soil-temperature` AND `soil-vpd`,
  discriminated by `input.measure`.
- `environmental.getClimateField` backs nine climate rows, discriminated by `input.signal`.

A flat path-to-toggle map would put all twelve on one track, and it would mislabel the majority
of a real cache: `getSoilField` alone is the largest group in the measured production profile
(137 of 504 entries). `toggleIdForRouterPath` therefore resolves those two paths through the
shared vocabulary tables (`SOIL_FIELD_MEASURES`, `climateFieldToggleId`) rather than a local
list, so a fourth measure or a tenth signal is attributed correctly with no change here. When
the discriminator is ABSENT, attribution resolves to the same default the *server* resolves it
to (`moisture`, `air-temperature`) — because that is the answer the entry actually holds, not a
guess. Everything else maps by path, and `getStreamflow`/`getGroundwater` both map to `water`:
two feeds, one toggle, one slider, so one track.

**Legacy entries are parsed, not discarded.** react-query's default `hashKey` is
`JSON.stringify` with sorted object keys, verified against live production keys, so the IDB key
parses straight back into the query key it was built from — router path, day and discriminator
included. Unstamped rows are attributed that way. It is a fallback rather than the primary path
because that format belongs to react-query, not to us: a future change to how it hashes would
strand every unstamped row, and the stamp would not care. Stamping is also O(1) per entry
instead of a `JSON.parse` per entry per scan.

**A dateless entry is never indexed.** Some allowlisted reads carry a bbox and no date
(`getWatersheds`, and in practice some `getClimateField` calls); they describe the live edge,
which is not a day any slider can point at. Stamping them with "today" would claim a day the
entry does not answer for once the clock rolls over. They are cached as before and simply do not
appear on any track.

**A day is indexed on the day alone, not on (day, bbox).** One day accumulates a separate entry
per viewport it was viewed at — the bbox is serialized to six decimal places, so a pan of a few
centimetres mints another key (defect D6, owned elsewhere) — so a track that only lit a day when
the CURRENT viewport's entry was cached would be dark almost always. The track therefore states
"an answer for this day is on disk", not "the next read of this day will certainly hit". That is
a deliberate over-claim in the one direction that keeps the feature useful; the alternative is a
feature that never lights up.

## The synced-day index

`src/stores/sync-index-store.ts` holds the answer in memory. Its public surface is
`useSyncedDays`, `useSyncIndexReady`, `useLayerSyncedBytes`, `clearLayerSyncedDays` and
`useHydrateSyncIndex` (mounted by `Providers`).

**Coherence rule: the index is a projection of IndexedDB, hydrated by exactly one cursor walk
per app mount and mutated incrementally afterwards.** `scanSyncedDays()` builds it; every later
write reaches it through `CacheIndexObserver`, a one-slot callback the store registers on the
cache. The dependency runs one way only (store -> cache), which is why the cache does not import
the store: a cycle between two module singletons is the kind of thing that works until an import
order changes.

Each indexed day carries an `entryCount` as well as its bytes, because one day accumulates an
entry per viewport it was read at: a drop decrements, and the day disappears only when its last
entry does. Byte deltas are SIGNED — an expired entry deleted on read reports a negative delta
before its replacement reports a positive one. Getting that wrong inflates the number rendered on
a destructive control ("clear N saved days (X KB)") on every scrub-pan cycle, which is the one
place an over-report is actually dangerous.

Where it can drift, and which way:

- A write that lands while the hydration scan is in flight is replayed into its result, tagged by
  cache key so a write the scan already saw is skipped rather than counted twice. For a scan a
  *capacity* pass started, there is no replay: that write can be missed until the next mount.
  Under-reporting is the safe direction, and the whole design errs that way.
- A drop leaves the day's `expiresAt` alone, since recomputing the max across the remaining
  entries would need per-key state. The only drop that arrives incrementally is an EXPIRED entry,
  which can never hold the newest expiry among the fresh entries still covering that day.
  Eviction and the per-layer reset republish a rebuilt index instead.
- Another tab's writes are invisible to this tab's index until it remounts.
- Freshness is enforced, not assumed: each indexed day carries the latest `expiresAt` across the
  entries covering it, a timer is armed for the soonest of them, and `pruneExpired` drops days as
  they pass. With 47% of a real cache expired-but-resident, an expired day drawn as "synced"
  would be the common case rather than an edge case.
- `useSyncIndexReady()` is false until a scan has completed, and stays false when IndexedDB
  cannot be read at all. "Nothing is synced" and "not yet known" must not render the same.

## Publishing a revalidated value

`persister` is called as `(queryFn, context, query)` and is never handed a `QueryClient`. Until
2026-08-16 the background revalidator wrote its fresh payload to IndexedDB and returned it into a
floating promise, so nothing ever reached react-query: every allowlisted layer rendered exactly
one fetch behind, permanently, and only caught up the next time that query key was mounted. That
is the "doesn't update after changing controls; right on the second look" report.

The client is now closed over by `createIndexedDbLayerQueryPersister`, called in
`src/lib/providers.tsx` where the client is created, and the value is published with
`queryClient.setQueryData` — the documented public API. Two alternatives were rejected:

- **`query.setData()`.** The persister does receive the `Query`, and `setData` exists on its
  type, but it is a `query-core` class method rather than part of the documented app-facing API —
  a private-API trap that a minor bump may change under us.
- **A module-global client registered at import time.** Hidden mutable state that binds to
  whichever client registered last; tests and any second client get it wrong, and nothing in the
  types says so.

Nothing is published into a key react-query no longer holds (`getQueryState` first): the IDB
write already saved it for the next mount, and materialising an entry for an unobserved query
only gives the collector work. Ordering is safe by construction — the publish follows a network
round trip AND an IndexedDB write, both many turns of the event loop after the persister returned
the stale value that settles the fetch.

## Revalidation policy

Background revalidation is **the only path by which a corrected warehouse day reaches a reader
who already has the old one cached.** Treat it as a correctness mechanism, not a freshness
nicety, and be very careful about narrowing it.

- **It runs for historical days too.** A gate skipping them shipped for one afternoon and was a
  strict regression: with a 30-day TTL, no revalidation, no schema bump and no revision compare,
  a republished past day had no route to the reader at all short of the destructive per-layer
  reset. See the TTL policy above for why past days move in this warehouse.
- **At most once per `REVALIDATION_MIN_INTERVAL_MS` (60 s) per entry**, tracked by
  `lastRevalidatedAt`, on top of the two-at-a-time concurrency throttle. That is the only bound,
  and it is what keeps re-reads of one row from becoming a request per read. It is strictly
  fewer requests than the original unconditional-per-hit behaviour, and healing is delayed by at
  most that minute.
- **A queued revalidation checks it is still the same generation before writing or publishing.**
  It can wait behind the throttle for longer than `LIVE_TTL_MS`; by then the entry may have
  expired, missed, and been replaced by a NEWER fetch, and writing the older result would revert
  a layer the reader just watched update. `createdAt` is the marker: revalidation preserves it, a
  cold write resets it.

**There is no revision/etag short-circuit, because there is no revision signal to read.** One was
half-built here and removed on 2026-08-16 after a check of every allowlisted procedure:
`environmental-read-model.ts` emits neither `etag` nor `dataRevision`; `ProxiedFeatureCollection`
carries a field named `revision` and every producer of it hardcodes `null`. The one real producer
the 2026-08-16 check found -- `/api/fires`, a REST route, on HTTP headers, and not allowlisted --
was deleted on 2026-09-02, so as of that date **no revision producer exists anywhere in this
repository**. The comparison could therefore never fire, and a green test asserting a hand-built
`{ dataRevision: "rev-7" }` response made a mechanism that does nothing look load-bearing.

Adding one is a SERVER change first: a procedure must actually emit a revision that moves when
the warehouse's answer moves. When wiring it, read it with a guard that rejects non-strings —
`String(null)` is `"null"`, and a hardcoded `revision: null` compared that way makes every
response look unchanged and freezes that layer's cache permanently.

## Degradation matrix

Every one of these falls through to the real `queryFn` silently -- no thrown error, no
user-visible failure, no behavior change beyond "this particular read didn't get to skip the
network this time":

| Situation | Behavior |
| --- | --- |
| SSR / vitest+jsdom (`typeof indexedDB === "undefined"`) | `isIndexedDbAvailable()` is false; persister is a pure passthrough. |
| Private browsing with IndexedDB blocked/absent | Same as above, or `indexedDB.open` throws/errors -- caught, resolves `null`, treated as unavailable for that call. |
| Quota exceeded on write | `putEntryWithMetadata` resolves `false`; the cache learns a ceiling at 90% of its live total, evicts down to it and retries once. The freshly-fetched result is returned to the caller either way. Never a silent permanent wedge — see "the budget". |
| Corrupt or wrong-shape stored entry | `isStoredLayerQueryEntry` rejects it; the entry is deleted and treated as a miss. |
| Query resolves but represents a failure (`availability === "request_failed"`) | `isCacheableResult` refuses to write it. (In practice the server never emits this value -- see `src/types/time-slider.ts` -- so this is defense in depth, not the common path.) |
| `queryFn` itself throws (a real network/server failure) | The throw is never caught by the persister; it propagates to react-query exactly as it would without a persister. Nothing is ever cached from a rejected promise. |
| IndexedDB unreadable, sync index | `scanSyncedDays()` resolves `null` (never an empty index); the store's status is `unavailable`, `useSyncIndexReady()` stays false and `useSyncedDays` returns a stable empty set. |
| A subscriber to the index throws | Caught at the observer seam; a display projection can never fail a layer read. |

## Files

- `indexeddb-store.ts` -- the wrapper described above.
- `query-persister.ts` -- allowlist predicate, TTL policy, layer/day attribution, cacheability
  check, expiry sweep and eviction, the synced-day scan and per-layer reset, and
  `createIndexedDbLayerQueryPersister`, wired into `src/lib/providers.tsx`.
- `src/stores/sync-index-store.ts` -- the in-memory projection the sync track reads.

## `environmental.getSoilMoisture` (added 2026-08-06)

The most expensive answer on the allowlist to recompute and the cheapest to store. Every
request costs a PostGIS aggregation plus a Gaussian blur plus a marching-squares pass, and
the result for a whole-PNW coarse view is at most nine polygons — a few KB. `resolveCacheTtlMs`
gives any past day the 30-day historical TTL so that scrubbing back to a day already seen does
not re-run the aggregation on the critical path.

> **Corrected 2026-08-16.** This section originally justified that TTL with "the day behind it is
> an ERA5-Land archive day, which is immutable once published". That is not true of this
> warehouse — the ERA5 lane backfills and the gap-reopen lane republishes past windows — and the
> claim was one of four places asserting it. The TTL is safe because the entry is revalidated in
> the background and rewritten in place, not because the day cannot move. See "TTL policy".

Its input carries `depth` and `zoom` on top of `bbox`/`date`. Both are part of the
`queryHash`, so a different depth or a different aggregation tier is a different entry
rather than a stale hit — which is the correct behaviour, and needs no change here:
`isPersistableQueryKey` only requires that a bbox or a date be present, and never inspects
the rest of the input.
