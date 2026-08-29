# src/lib/net -- the one client-side request budget

## What this is

`request-budget.ts` is a framework-agnostic scheduler that every network-issuing call site in
the client is meant to pass through: a global concurrency cap, a token-bucket rate limiter, a
fair (round-robin) queue across named "lanes", and `AbortSignal`-based cancellation that never
leaks a slot. It knows nothing about `fetch`, tRPC, or React -- `acquireRequestSlot` and
`runBudgeted` take a plain async task, and `createBudgetedFetch` is a thin adapter to the one
shape (`typeof fetch`) both of this app's transports happen to need.

It is a module-level singleton with no exported constructor. That is not an oversight: if a
caller could build a second, private budget, "uniform for all layers" would decay into "uniform
for whoever remembered to use the shared one." There is exactly one budget, and the only way to
use it is the shared one.

## The problem this replaces

Before this file, the client had six independent, differently-shaped request-shaping
mechanisms and no shared limiter:

| mechanism | file:line | governs |
| --- | --- | --- |
| `MAX_CONCURRENT_REVALIDATIONS = 2`, hand-rolled semaphore | `src/lib/cache/query-persister.ts:213`, `:225-244` | SWR revalidation after an IndexedDB hit, allowlisted layers only |
| `SCRUB_SETTLE_MS = 250` | `src/stores/useMetricAtDate.ts:34` | slider scrub -> request |
| `useDebounce(value, 300)` | `src/hooks/useDebounce.ts:3` | generic, used beyond the slider |
| `PREFETCH_RADIUS_DAYS = 1` | `src/stores/useMetricAtDate.ts:54` | neighbour-day prefetch volume |
| reconnect backoff 1s->30s | `src/hooks/useSSE.ts:16-77`, `useWebSocket.ts:17-86` | reconnect only, not requests |
| MapLibre paint throttle | `src/components/ui/layer-opacity-slider.tsx:21` | render, not network |

And two confirmed-unthrottled user-triggered paths: `useOfflineSync.runSync()`
(`src/hooks/useOfflineSync.ts:80`) replays queued mutations in a plain sequential
`for (const op of ops)` loop with zero cap, zero delay, zero backoff; and `prefetchTiles`'s
no-service-worker fallback (`src/lib/offline/tile-cache.ts:80-104`) fires `fetch()` for every
tile URL in a batch with no concurrency cap at all.

Meanwhile one settled scrub with the usual layers active mints ~16 tRPC keys simultaneously (9
climate signals, 3 soil-field measures, vegetation, weather, streamflow, groundwater, drought),
and the server backing all of this is a 3 GB-capped box that has measured **1,203 MB read in 15
seconds** from a single unthrottled burst. This module exists to protect that box, not to be
polite.

## Public API

```ts
acquireRequestSlot(lane, { signal? }): Promise<RequestSlotHandle>   // low-level: caller releases
runBudgeted(lane, task, { signal? }): Promise<T>                    // preferred: releases itself
createBudgetedFetch(lane): typeof fetch                             // fetch-shaped adapter over runBudgeted

getActiveRequestCount(): number
getQueuedRequestCount(): number
getQueuedRequestCountForLane(lane): number
getRequestBudgetSnapshot(): { activeCount, queuedCount, queuedByLane }
subscribeToRequestBudget(listener): () => void                      // pairs with useSyncExternalStore

resetRequestBudgetForTests(overrides?)                              // test-only, see below
```

`runBudgeted` is the one to reach for almost everywhere: it acquires, runs the task, and
releases in a `finally`, so there is no path -- success, throw, or abort -- that leaves the slot
held. `acquireRequestSlot` exists only because `createBudgetedFetch` needs the wait (queue +
rate gate) and the actual network call to be two separate steps; a caller reaching for it
directly for any other reason should have a specific reason to.

## The numbers, and why

**`MAX_CONCURRENT_REQUESTS = 4`.** The existing precedent, `query-persister.ts`'s
`MAX_CONCURRENT_REVALIDATIONS = 2`, is deliberately conservative because it only ever gates
*background* SWR revalidation -- the user already has data on screen from the cache hit that
triggered it, so nothing they're looking at is blocked on it. This budget instead gates
*foreground* traffic the user is actively waiting on (a fresh pan, a first-paint layer load), so
it earns a bit more headroom for latency: at concurrency 1, painting a full 16-signal viewport
would take up to 16x one request's round trip, serialized. 4 is small enough to matter -- it
turns "up to 16 (more under the D6 cold-bbox pathology below) simultaneous requests" into a hard
ceiling of 4, a 4x-or-better cut to the server's worst-case simultaneous load per gesture -- while
still giving real parallelism so an ordinary pan does not feel throttled.

**`SUSTAINED_REQUESTS_PER_SECOND = 5`, `BURST_CAPACITY = 8`** (token bucket). A concurrency cap
alone cannot stop a *slow drip*: `useOfflineSync`'s replay loop and `tile-cache.ts`'s fallback
loop are each already close to "one thing at a time" in shape, yet both were firing requests
back-to-back with zero delay between them. The rate axis is what actually bounds that -- it caps
how many *new* dispatches may start per second, independent of how quickly each one finishes.

- Burst 8 lets roughly half of one settled gesture's ~16 keys clear the rate gate immediately
  (concurrency still meters how many of those are truly simultaneous); the rest wait on refill.
  This directly targets defect D6 (`viewport-bbox.ts:29`-30 serializes to 6 decimal places, so a
  ~10 cm pan mints all 16 keys cold): even a fully-cold burst can no longer reach the server as
  one instantaneous spike, because the token bucket itself refuses to admit more than 8 requests
  at once no matter how many are queued behind them.
- Rate 5/s sustained is a hard ceiling that still comfortably clears legitimate interaction: with
  `SCRUB_SETTLE_MS = 250`, even a maximally aggressive multi-row drag settles at most ~4 distinct
  layer-days per second in steady state. 5/s sits just above that real cadence while remaining a
  real, enforced limit against a runaway loop -- exactly the `useOfflineSync`/tile-prefetch case --
  that would otherwise fire on every microtask.
- Combined worst case for one 16-key gesture: 4 dispatch immediately (concurrency), up to 4 more
  clear as slots free while burst tokens remain, and the remaining ~8 trickle in at 5/s (roughly
  1.6 s) as both axes allow. The server never again sees anything resembling the measured
  1,203 MB/15 s pattern from a single gesture, because the gesture itself is now admission-
  controlled instead of firing every request in one JS tick.

These three numbers are the trade-off: tight enough to make the measured failure mode
structurally impossible to reproduce from one gesture, loose enough that no legitimate,
human-paced interaction (a scrub, a pan, an opened panel) ever visibly waits on the limiter
rather than the network.

## Fairness policy: round-robin across lanes, not FIFO, not priority

A `RequestLane` (a plain string, e.g. `"climate"`, `"drought"`, `"fires"`, `"offline-sync"`,
`"tile-prefetch"`) tags which stream a request belongs to. It is used for scheduling fairness
ONLY -- it is never a priority, and nothing in this module ranks one lane above another.

**Plain FIFO was rejected** because it fails the concrete scenario the owner named, by
construction: a global arrival-order queue serves 9 climate requests that all arrived moments
before 1 drought request in full before touching the drought one, because they got there first.
That is exactly the starvation this module exists to prevent.

**Priority was rejected** because nothing in the codebase or the owner's ask establishes an
importance ordering across the ~16 layer lanes -- is drought more urgent than air temperature?
Nobody has said so. Inventing a ranking to make the scheduler decide would be a real, unreviewed
product decision smuggled into infrastructure code, not an engineering default.

**Round-robin needs neither.** Every lane with pending work sits in a rotation queue; a lane is
re-appended to the BACK of that rotation immediately after one of its requests is dispatched (or
dropped entirely once its queue drains). A lane that arrives late waits, at most, for every lane
currently ahead of it to get one more turn each -- never for an unrelated lane's entire backlog.

Worked trace (concurrency = 1, so the effect is visible one dispatch at a time; see
`request-budget.test.ts` "fairness under a 9-vs-1 burst" for the executable version): push 9
`"climate"` requests, then 1 `"drought"` request, while nothing has completed yet. Dispatch
order as slots free up is `climate1, climate2, drought1, climate3, climate4, ...` -- drought is
served **3rd**, immediately after climate's second turn, never behind climate's remaining seven.
At the production concurrency of 4, the effect is the same shape: whichever lane didn't make the
very first synchronous burst gets served on the very next round of freed slots, not after the
lane ahead of it fully drains.

## Cancellation and "never leak a slot", improved over the copied pattern

`query-persister.ts:238-244`'s `releaseRevalidationSlot` is a single global counter with a
free-floating release function and a `Math.max(0, activeRevalidations - 1)` floor guard. That
guard stops the counter from going negative on a double-release, but nothing stops the
double-release from happening, and nothing frees a slot at all if whatever held it is aborted or
torn down without ever calling release. It is a working pattern for the one thing it does
(a single undifferentiated background queue), not a general one.

This module's `release()` is per-acquisition and idempotent (a closure-captured `released`
flag), so calling it twice -- once from the caller's own `finally`, once from this module's own
abort handling below -- is safe *by construction*, not by convention. And every granted slot
carries its own abort listener for its entire lifetime, both phases:

- **Aborted while still queued**: the entry is removed from its lane's queue and the
  `acquireRequestSlot`/`runBudgeted` promise rejects -- without ever having consumed a
  concurrency slot or a rate token. A day the user has already scrubbed past costs nothing.
- **Aborted after being dispatched**: this module calls `release()` itself, immediately, rather
  than waiting for the caller's own cleanup to get around to it. If the caller's `finally` also
  calls `release()` moments later (the normal `runBudgeted` path), the idempotency guard makes
  that a no-op. A caller that forgets to release on an aborted path -- precisely the bug class
  the copied `Math.max(0, ...)` guard exists to paper over -- cannot leak a slot here, because
  the module already released it before the caller had a chance to forget.

## Both transports

- **tRPC** (all layer-specific reads plus the deliberately narrow fire-perimeter
  `getMetricAtDate` procedure): wire `fetch: createBudgetedFetch("trpc")` into the ONE link chain,
  `trpcLinks()` in `src/lib/trpc/client.ts`. Both `trpc.createClient` (React hooks, wired in
  `src/lib/providers.tsx:38`) and `getVanillaTrpcClient()` (used by
  `src/stores/useMetricAtDate.ts`'s retained fire-perimeter transport) build their link array
  from this one function. The live map layers use their dedicated procedures rather than that
  retained hook, but both paths share the same budget structurally.
- **Raw `fetch`** (`/api/fires`, the offline-sync replay, the tile-prefetch fallback): each of
  these gets its own `createBudgetedFetch("<lane>")` (or a `runBudgeted` wrapper around the
  existing call), a drop-in replacement with the exact same call shape.

Either way, the caller's own `AbortSignal` (react-query's per-query controller for tRPC;
`useFireData`'s own `AbortController` for fires) is what the budgeted fetch uses for BOTH queue
cancellation and, once dispatched, the real network call. React Query already aborts a
superseded query's in-flight fetch when its inputs change (a bbox pan, a settled scrub landing
on a new day) -- so a query that goes stale while still queued in this budget is dropped for
free, with no code needed beyond the `httpBatchLink` wiring above.

## SSR-safety

Nothing in this file touches `window` or `navigator`, at module scope or inside any function --
the whole scheduler is `Date.now()`, `setTimeout`/`clearTimeout`, `Map`, `Set`, `Promise`, and
`AbortSignal`/`DOMException`, all of which exist in every Next.js runtime (Node server, Edge,
browser). `request-budget.test.ts` asserts this directly by reading the file's own source and
checking neither identifier appears, rather than trying to simulate SSR by deleting jsdom
globals mid-suite.

## Non-goals -- what this deliberately does NOT fold in

- **`useDebounce` / `SCRUB_SETTLE_MS` / `PREFETCH_RADIUS_DAYS`** decide WHEN a request is
  generated and how many neighbour-days to warm -- upstream of this module. This module decides
  what happens to a request once it is about to be dispatched -- downstream. They compose (a
  debounce that fires less often sends this budget less work); neither needs to change for the
  other to exist.
- **`useSSE` / `useWebSocket` reconnect backoff** (1 s -> 30 s exponential) governs persistent
  *connections*, not discrete requests. A reconnect is not a unit of work this scheduler's
  concurrency/rate math applies to; forcing it through here would conflate two different
  failure/backpressure models for no benefit.
- **The MapLibre opacity-slider's `requestAnimationFrame` coalescing** throttles *paint*, not
  network. Out of scope for the same reason the prompt calls out explicitly: do not conflate
  render throttling with request throttling.

## Six mechanisms this is meant to eventually subsume

None of the edits below are made in this file's own change -- this module ships standalone by
design (see the file-boundary note in the PR/task this shipped under). Left here as the map for
whoever wires or extends it next:

1. **`query-persister.ts`'s SWR revalidation queue** (`MAX_CONCURRENT_REVALIDATIONS`,
   `acquireRevalidationSlot`, `releaseRevalidationSlot`, lines 213-244) could become
   `runBudgeted("swr-revalidation", () => queryFn(context))`, dropping its private semaphore
   entirely. Deliberately not done automatically: background revalidation may deserve its own
   tier/lane treatment rather than a silent merge, which is a decision worth making on purpose.
2. **`useOfflineSync.ts`'s `runSync` loop** (`:80`, fetch at `:89-98`) -- the primary motivating
   case; wire via `createBudgetedFetch("offline-sync")` or a `runBudgeted` wrapper per iteration.
3. **`tile-cache.ts`'s no-SW `prefetchTiles` fallback** (`:80-104`, fetch at `:90`) -- same
   treatment, lane `"tile-prefetch"`.
4. **`useFireData.ts`'s raw fetch** (`:66-69`) -- `createBudgetedFetch("fires")`.
5. **The ~16 tRPC-routed layer queries** -- one edit, `trpcLinks()` in `src/lib/trpc/client.ts`.
6. **`useDebounce` / `SCRUB_SETTLE_MS` / `PREFETCH_RADIUS_DAYS` and the SSE/WS reconnect
   backoff** -- explicitly NOT subsumed; see "Non-goals" above. Listed here only because the
   audit that motivated this module named them, not because they belong in it.

## Files

- `request-budget.ts` -- the scheduler described above: concurrency cap, token-bucket rate
  limiter, round-robin lane fairness, abort-safe acquire/release, the fetch adapter, and the
  observability surface (`getRequestBudgetSnapshot`, `subscribeToRequestBudget`).
