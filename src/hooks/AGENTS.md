---
type: agents
---

# src/hooks/AGENTS.md

Directory-level rationale for hooks whose "why" is too long for a one-line doc comment. Add a
section rather than a new file when the next hook needs one.

## useLayerCacheControls

The whole per-layer local-cache surface as one hook, added 2026-09-07: the resolved policy
(refresh mode, retention limit, whether the user has overridden the nature default), what is
currently held (`heldDayCount`, `heldBytes`, `isHeldKnown`, `lastFetchedAt`) and five actions
(`setRefreshMode`, `setRetainedDayLimit`, `resetToDefaults`, `refetchNow`, `clearHeldData`).

**It exists because it is the one place the three moving parts may meet.** The persisted
preference store (`src/lib/cache/layer-cache-policy-store.ts`) must not import the IndexedDB
persister that enforces it — that would be a cycle between two module singletons, the failure
mode `sync-index-store.ts` avoids with a callback seam — and neither of them may reach a
`QueryClient`, which only exists inside the provider tree. So the store stays a leaf, the
persister reads it one way, and every action that has to touch both plus react-query lives here.

**`refetchNow` stamps before it invalidates, and the order is the mechanism.**
`requestLayerRefresh` records an instant; the persister then refuses any stored entry created
strictly before it. Invalidating first would let the refetch be answered by the very copy it was
meant to replace. Nothing is deleted up front — days the reader is not looking at refetch lazily
when they next look.

**`clearHeldData` removes INACTIVE query entries only.** Removing an active one makes react-query
refetch on the spot, which writes the layer straight back to disk and makes "clear" look like it
failed. What is on screen stays on screen; what is not is gone from memory as well as disk.

**`setRetainedDayLimit` sweeps synchronously rather than waiting for the next write.** A limit
that only takes effect the next time the reader happens to land on a new day is a control whose
effect cannot be observed, and the held-days count rendered beside it would go on contradicting
the number just set.

**`isHeldKnown` is not decoration.** It is `useSyncIndexReady()`, false until IndexedDB has been
read once and permanently false where it cannot be read at all (SSR, jsdom, private browsing).
"Nothing is held" and "not yet known" must not render the same on a control whose whole job is to
make what is held falsifiable.

Rationale for the policy itself — the nature table, why `manual` is the default for
`static_lookup`/`release_series`, and what a manual layer deliberately gives up — is in
`src/lib/cache/AGENTS.md` §"per-layer cache policy".

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

**It is a controller, but not a subscription-free one.** It returns exactly three stable
callbacks -- `queryLocation`, `sendFollowUp`, `retryLastRequest` -- and reads
`useRegionalIntelligenceStore.getState()` inside them rather than subscribing to it, which is
what keeps `queryLocation` referentially stable across renders. But the hook body also calls
`useViewedLayerDays()` -> `useLayerVisibility()` -> `useActiveLayerToggles()`
(`useRegionalIntelligence.ts:101-102`), which subscribes its caller to `useMapStore.activeLayers`
and `useTimeSliderStore.capabilities` -- every layer toggle and slider-capability write
re-renders whoever calls this hook. (The hook's own header comment, `useRegionalIntelligence.ts`
lines ~93-99, still claims it "subscribes to no analysis state at all" and is stale; that file is
not owned by this pass and was left as found.)

Because of that subscription, since 2026-09-03 `MapView` no longer calls this hook directly:
doing so re-rendered the whole map subtree -- the component that owns the MapLibre instance and
every layer under it -- on every layer toggle. The call now lives only in components that are
mounted just while the subscription is worth paying for: `AgentAnalysisPrompt` (`MapView.tsx`),
mounted only while a location is selected and carrying `queryLocation` in its own `useCallback`
dependency list, and `RegionalIntelligencePanel`, mounted only while the analysis panel is open.
A layer toggle now re-renders one or both of those -- never the closed map --
`src/__tests__/components/map-view-render-count.test.tsx` pins this.

Until 2026-09-02 it did `const store = useRegionalIntelligenceStore()` and returned
`{ ...store, ... }`. That subscribed every consumer to the whole store, and the store is written
on **every streaming token** (`updateLastMessage`), so an in-flight analysis re-rendered
`MapView` once per delta. No consumer ever read the spread state: `RegionalIntelligencePanel`
already selects its eleven fields individually from the store, and `MapView` only ever
destructured `queryLocation`. A consumer that needs analysis state selects it from the store
directly.

The regional intelligence SSE `saved` event names the persisted assistant message
for private feedback. It is emitted only after recordExchange succeeds; failed
persistence never invents a saved ID. Session activity reflects received SSE events
and request outcomes. Every event still checks the active AbortController, so opening
a saved conversation or starting another chat cannot receive an abandoned request's
late response or activity update. Report payloads and source metadata remain unchanged.

## Two botanical read hooks, and why that is not a duplicate

`useBotanicalOccurrencesQuery` (in `useViewportProxiedLayers.ts`) and `useBotanicalOccurrences`
(its own file) read the same plane through the same server client and are kept apart on purpose.

The react-query one exists because a map layer and the panel describing it must issue the SAME
cache entry — that is the whole subject of this directory's proxied-viewport section, and splitting
it would let the drawing and the caption disagree about which generation they describe. The
plain-fetch one is the standalone lane: one abortable request per viewport, nothing to key, and the
semantic states (`idle` / `loading` / `success` / `empty` / `error`, plus `isStale` and `isPartial`)
a layer needs to caption itself. Neither can reach the plane with inputs the other would refuse,
because both bottom out in `botanical-occurrences-client.ts`.

**Superseded answers are discarded twice** — the in-flight request is aborted AND every response is
checked against a monotonic sequence before it becomes state. Abort alone is not enough: a response
can already be queued as a microtask when the abort lands, and applying it draws a viewport the
reader has already left. The react-query hook gets this structurally from its query key; a hand-
rolled fetch does not, and the second check is what replaces it.

**`empty` is a positive answer and `error` is the absence of one.** They are separate phases so a
caption can say which happened; collapsing them is how "this release holds nothing here" starts
reading as an outage. A stale answer is retained across a PENDING request and dropped on a FAILURE,
matching the `keepPreviousData` rule stated for the proxied-viewport queries.
## land-context-viewport

`useLandContextViewport.ts` is the automatic viewport read the owner admitted on 2026-09-18 --
the third of the three options `conductor/RUNBOOK.md` "Finding 4" left open. It is **additive**:
`useLandContextQuery` keeps answering the click-driven point/parcel selection unchanged, and the
two key different react-query entries, so neither can shrink the other's answer.

Three numbers live here as restated copies, not imports, because their homes are server-only
(`scripts/check-client-server-imports.mjs` forbids `@/lib/server/**` from a hook) -- the same
device `WATERSHED_LIST_MAX_SQUARE_DEGREES` uses in `useViewportProxiedLayers.ts`:

- `LAND_CONTEXT_MAX_AOI_SQUARE_DEGREES` mirrors `MAX_AOI_AREA_SQUARE_DEGREES` (`budgets.ts`). It
  is the **binding** constraint: at 1 square degree the automatic read only fires from roughly z10
  in, and a wider viewport reports `area_over_budget` rather than collecting a refusal banner on
  every pan. Raising it is a scoped, reviewed budget change and an owner decision, not a knob.
- `LAND_CONTEXT_RUNG_MAX_BBOX_SQUARE_DEGREES` mirrors `RUNG_MAX_BBOX_SQUARE_DEGREES`
  (`land-context/parquet-reader.ts`).
- The rung is **labelled** here and **selected** on the server from the bbox it receives. The
  procedure takes no zoom on purpose: a zoom in the input would be part of the query key, and the
  map and any panel reading the same viewport would split into two entries drawing two different
  aggregations of it.

`landContextRungForViewport` picks from zoom AND bbox size -- the finest rung at or below the
zoom's own tier that still admits the area. Zoom alone is the defect the 2026-09-14 handoff
confirmed against the botanical plane: a normal regional viewport landed on a rung bounded at 100
square degrees and was refused, while the rung below it would have answered at 16x the budget.

Mount snippet for `LayerManager.tsx` (not applied here; `LayerManager.tsx` is another lane's file):

```tsx
const enabledGroups = useLandContextStore((state) => state.enabledGroups);
const landContextViewport = useLandContextViewport({ enabledGroups });
// `state` is a caption, not a failure: "area_over_budget" means zoom in, not that anything broke.
```

### land-context-viewport: mounted

The snippet above is **applied** as of the 2026-09-18 wave-3 mount: `LayerManager.tsx` holds the
hook beside its other viewport reads, and `area_over_budget` renders through
`ParquetLayerFaultBanner` with `tone: "notice"` -- the caption tone, never the fault tone. An
automatic read that silently does not fire is indistinguishable from one that failed, which is the
whole reason the state is surfaced at all.

`landContextRungForViewport` now delegates its walk to `selectFinestAdmittingRung`
(`src/lib/map/rung-selection.ts`); the ladder, the ceilings and the zoom gate stay here. See
`src/lib/map/AGENTS.md` "rung-selection.ts".

## useBotanicalOccurrences: the proxy detail lane

Mounted in `LayerManager.tsx` as of 2026-09-18, feeding `BotanicalOccurrencesLayer`'s geojson and
its `readPhase`, with `describeBotanicalOccurrencesState` as the one caption wording.

**Two botanical lanes run at a detail zoom, deliberately.** This hook (the proxy route) draws the
UBC detail points; `useBotanicalOccurrencesQuery` (tRPC) still serves the two aggregate layers,
GBIF's own toggle, and the store the filters and details panels read. They are the same generation
under the same filters, so they cannot disagree about what is published -- but with the UBC toggle
on at a detail zoom, one viewport does cost two upstream reads. Collapsing them is the follow-up
owed when the aggregate layers move to the proxy too; narrowing the tRPC gate first would take the
release-set pin and the filters panel's state down with it.

**The rung is selected, not assumed.** `servingBand` is the rung that actually answered -- the
route's `servingRung` once an answer lands, the hook's own selection before that, and null only
when no rung admits the viewport (the one case still refused client-side, without a round trip).
`describeBotanicalOccurrencesState` says so out loud whenever the served rung is not the one the
zoom asked for: a coarser rung is a SUBSTITUTION OF EVIDENCE, and a reader who is not told has no
way to know the drawing changed meaning.
