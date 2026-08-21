---
type: agents
---

# src/hooks/AGENTS.md

Directory-level rationale for hooks whose "why" is too long for a one-line doc comment. Only
`useFireData` is documented here today; add a section rather than a new file when the next hook
needs one.

## useFireData

`/api/fires` is the one dated map feed that is a raw `fetch`, not a tRPC/react-query hook, so it
carries its own retry/staleness/caching machinery instead of getting it for free.

**Retention is deliberate; asserting it silently is not.** On a 304, a failed fetch, or a date
change whose own fetch has not landed, `data` keeps whatever it last held rather than blanking
to empty — matching the map lane's `placeholderData: keepPreviousData` pattern, and avoiding the
blank-and-refill flash a stricter reset would reintroduce. What must never happen is captioning
that retained data as the CURRENTLY requested day's own answer. `isStaleForRequestedDate` /
`dataDate` exist so a caller can tell the difference: `dataDate` is the day `data` was actually
fetched for (`undefined` for the live window, or before anything has ever loaded — the two are
told apart by a separate `hasLoadedOnce` flag, since both read `undefined`), and
`isStaleForRequestedDate` is `!hasLoadedOnce || dataDate !== date`. No explicit "reset on date
change" effect is needed: the moment the `date` prop changes, that comparison goes stale on its
own, before the new fetch even starts.

**An ETag cache needs the payload beside it, not just the string.** A 304 says the SERVER's copy
of one cache key is unchanged since it was last fetched — it says nothing about whether that
key's content is what is CURRENTLY painted, if the slider visited a different day in between.
`responseCacheRef` therefore stores `{ etag, data }` per cache key, so a 304 can restore the
exact payload it is a 304 FOR, rather than leaving whatever a different key's fetch last left
behind mislabelled as this key's answer. Bounded at `MAX_CACHED_RESPONSES` (LRU by
delete-then-reinsert) because this is
NOT a small cache: at `MAX_ROWS` (2,000 features, `environmental-read-model.ts`) and roughly
1.0–1.6 KB of retained heap per parsed FIRMS feature, one full entry is ~2–2.4 MB. This hook
mounts twice concurrently — `LayerManager.tsx` (always) and `FireDetails.tsx` (while the fire
panel section is expanded) — each with its own private cache, so the bound is chosen small
enough that the worst case across both stays in the single-digit megabytes, not the hundreds.

**Cache bookkeeping lives in refs, not state.** `fetchFires` is a `useCallback` keyed only on
`[date]`. If it also closed over `dataDate`/cache state as a dependency, its identity would
change on every successful fetch, tearing down and re-arming the `useEffect`'s poll interval on
every tick instead of every 2 minutes. Refs are read imperatively inside the callback instead.

**Omitted `date` asks for the live FIRMS lookback window**, not "today" as a single calendar
day — a materially different, WIDER answer than any other slider-day reader's "omitted". See
`src/app/api/fires/route.ts`'s own doc comment for the full contract, and
`useDebouncedLayerDay` in `lib/map/layer-toggle-context.ts` for why `date` is `undefined` only
when the caller POSITIVELY knows the selection is the server's today.
