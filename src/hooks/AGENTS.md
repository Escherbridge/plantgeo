---
type: agents
---

# src/hooks/AGENTS.md

Directory-level rationale for hooks whose "why" is too long for a one-line doc comment. Add a
section rather than a new file when the next hook needs one.

## useParquetFireDetections

The map's only fire read since the 2026-09-01 Parquet cutover. It calls
`wildfire.getFireDetections` with the `fire` row's settled day, the viewport bbox and the
viewport zoom, and both consumers — `LayerManager` (the drawn cells) and `FireDetails` (the
count) — go through it.

**It reads its own key inputs; callers pass only `enabled`.** Day, bbox and zoom are taken
inside the hook from `useDebouncedLayerDay("fire")` and `useViewportBounds()` rather than from
arguments. `useViewportProxiedLayers` documents the hazard this closes — "a panel describing a
layer must never key its read differently from the map drawing it" — but leaves the agreement
to the two call sites. Here there is nothing to disagree about: one derivation, one react-query
entry, and a panel that cannot silently ask for a different day than the one on the canvas.
`enabled` stays an argument because it is deliberately NOT part of the key: the map passes the
layer's switch, the panel passes its section being open, and neither splits the entry.

**Every refusal is a state, not a zero.** `ready` / `absent` / `not_generated` /
`upstream_unavailable` are the reader's own four, and the hook adds `pending` (no typed answer
yet) and `request_failed` (the transport failed before any state was returned). A caller
renders the refusal; the count is defined only for `ready`. This is the whole reason the
cutover happened — `/api/fires` answered a never-written day and an empty day with the same
empty `FeatureCollection`, so the map could not tell "no fires burned" from "nothing has been
published".

**`truncated` is surfaced, never absorbed.** The reader raises it when it hits its row budget,
which means the cells drawn stop short of the viewport rather than at the edge of the fire.
`LayerManager` renders it as an amber notice beside the fault overlay; `FireDetails` says the
count is a subset. Nothing in this lane may accept a truncated answer silently. The same overlay
now carries the two refusals an empty canvas cannot be told apart from "no fires burned here":
`absent` quotes the governed absence's own recorded reason, and `not_generated` names which
silence it is (this day unwritten, or the lane never written).

**Governance gates the request, not just the render.** The query is disabled when
`isLayerPermanentlyWithheld("fire")` — `layer-registry.ts`, the same predicate every proxied lane
in `useViewportProxiedLayers` applies. It matters more here than there: this hook has two callers,
and without the gate an open `FireDetails` would go on requesting a layer the map is forbidden to
draw, making a panel the sole requester of a withheld layer.

**The zoom tier is latched to the last LANDED request, not to the current zoom.** The payload
carries no tier, so the label is derived from the zoom that was sent. Under
`placeholderData: keepPreviousData` a retained frame outlives the zoom it was fetched at, so
labelling it with the tier being requested would state an aggregation those cells were never
aggregated at. The hook keeps the last landed tier in state and uses it whenever
`isPlaceholderData` is true — the same latch, for the same reason, that
`usePublishedDrawnLayerDays` keeps for the drawn DAY.

**No poll.** `useFireData` re-requested the live window every two minutes; a day partition is
immutable once written and revised only at the live edge, so the 15-minute `staleTime` (the
same one the streamflow and weather reads use) replaces it. A settled past day is never
re-requested at all, because the day is in the key.

## useFireData (legacy, no map caller)

**Nothing on the map calls this any more.** `LayerManager` and `FireDetails` moved to
`useParquetFireDetections` on 2026-09-01; this hook and `/api/fires` stay on disk only until the
acceptance track has parity evidence, and a later slice deletes both. Do not add a caller.

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
