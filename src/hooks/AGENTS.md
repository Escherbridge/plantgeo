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

## useFireData (deleted 2026-09-02)

**The hook and `/api/fires` are gone from the tree.** Both had had no map caller since the
2026-09-01 cutover; slice r3 deleted `src/hooks/useFireData.ts`,
`src/app/api/fires/route.ts` and their two suites once the reader track had its parity
evidence. Rollback for the whole fire lane is a revert of the cutover commit, not a
re-enablement switch — see
`conductor/tracks/parquet_reader_cutover_acceptance_20260901/evidence/reader-cutover-verdict.md`.

What the section here used to document, and where the answer lives now: the ETag/payload cache,
the two-minute poll, the `isStaleForRequestedDate` retention rule and the "omitted `date` means
the live FIRMS lookback" contract were all private to that hook. `useParquetFireDetections`
replaces every one of them with react-query's own machinery plus the day in the query key —
see the section above. The one reader still calling PostgreSQL for fire is
`getPublishedFireDetections`, and its only caller is the server-side alert engine
(`src/lib/server/services/alert-engine.ts`), which is not a map or agent read.

## useRegionalIntelligence

**It is a controller, not a view model.** It returns exactly three stable callbacks --
`queryLocation`, `sendFollowUp`, `retryLastRequest` -- and subscribes to no analysis state at
all. Everything it needs at send time is read through
`useRegionalIntelligenceStore.getState()` inside the callbacks, which is also what keeps
`queryLocation` referentially stable: `MapView` carries it in a `useCallback` dependency list.

Until 2026-09-02 it did `const store = useRegionalIntelligenceStore()` and returned
`{ ...store, ... }`. That subscribed every consumer to the whole store, and the store is written
on **every streaming token** (`updateLastMessage`), so an in-flight analysis re-rendered
`MapView` -- the component that owns the MapLibre instance and every layer under it -- once per
delta. No consumer ever read the spread state: `RegionalIntelligencePanel` already selects its
eleven fields individually from the store, and `MapView` only ever destructured
`queryLocation`. A consumer that needs analysis state selects it from the store directly.
