---
type: agents
---

# src/hooks/AGENTS.md

Directory-level rationale for hooks whose "why" is too long for a one-line doc comment. Add a
section rather than a new file when the next hook needs one.

**No absolute claim without a citation** (style review W10, closing judgement). A sentence in this
file asserting that a predicate is complete, a write is safe, or a cost is bounded — "the whole
predicate", "always", "never", "cannot", "at most once" — must cite the `file:line` that enforces
it, in the same sentence. Every blocker of that run was preceded by exactly such an uncited
sentence, and each was refuted by a file the author did not have open; writing the citation is what
makes the author open it. A claim that cannot be cited is a claim that has not been checked, and it
belongs in the text as an assumption with its reversal cost, not as an invariant.

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

`useLandContextViewport` reads automatically without replacing the explicit click selection.
The client sends its zoom tier and bbox. The server chooses the finest published tier no finer
than that zoom whose area ceiling admits the view. Coarse BLM products dissolve by source/state,
so a regional view can fit the same 200-feature/2 MB response limits. Both client and server cap
area at 1,600 square degrees; the repeated constants are pinned by tests because browser modules
cannot import server modules. The response does not state a served rung, so the client reports
`rung_unknown`, never a computed rung as if the server had confirmed it.

`useCropCover` independently reads USDA CDL estimates, keyed by selected source edition and view.
The edition selector offers published release days from the census; rows supply the observed
year. It is an annual-reference selector, not a daily observation slider. Disabling availability
or a failed read suppresses geometry. Collapsing the dock does not stop the map query.

## Live viewport reads

`LiveViewportRead` (`useViewportProxiedLayers.ts:174-203`) is what a viewport query hands back
instead of the react-query result: the `answer`, whether that answer `isAnswerLive` for the
request in hand, and `isError` / `isFetching` / `isLoading` / `isSuccess` /
`isShowingRetainedAnswer` — every one of them gated on that same liveness, because a flag read off
a disabled observer describes the request that observer last ran.

**All five viewport queries in the file return one**, as of the 2026-09-19 sweep:
`useWatershedsQuery`, `useSoilSurveyQuery`, `useSoilFieldQuery`, `useClimateFieldQuery` and
`useBotanicalOccurrencesQuery`. `liveViewportRead` (`:216-236`) is the only admission point, and
`drawnDayReadState` (`:245-252`) is the only translation into the drawn-day registry's vocabulary,
so no call site reassembles that mapping from a raw observer either.

**Why it exists.** Every query here is configured `placeholderData: KEEP_PREVIOUS_WHILE_PANNING`,
which is what keeps the map from blanking on a pan. The cost is that `data` OUTLIVES the
enablement that fetched it: a disabled observer keeps serving the previous key's answer. So a
consumer asking "is this answer about the request I am rendering?" has to consult the enablement —
which is composed inside the hook, out of its reach. Three waves of consumers re-derived it from
the reported symptom and each missed a conjunct (W8 B3, W9 S1, W10 B1; the third was a dynamic one,
`requested !== null`). Liveness is therefore computed exactly where `enabled` is composed, passed
to both the observer and `liveViewportRead`, and the raw result is not exported.

**What this does and does not make impossible.** A consumer of a `LiveViewportRead` cannot read a
retained frame at all — `answer` is withheld (`:216-236`), and there is no other binding to reach
it through, so a conjunct added to the enablement propagates without any consumer changing.

A NEW hook here cannot quietly hand one back **once it carries the return annotation**, and the half
that enforces that is the TYPE, not the lint rule. An unannotated new hook in the house idiom escapes
both halves — `tsc` clean, lint silent — so **write the annotation first**; nothing in the build
checks that you did. Each of the five is annotated `LiveViewportRead<EnvironmentalAnswers[...]>`
(`useViewportProxiedLayers.ts:144-154` for the answer types; the annotations at `:262`, `:303`,
`:354`, `:444`, `:535`), and a react-query result has neither `answer` nor `isAnswerLive`, so all
four ways of leaking one — `return trpc….useQuery(…)`, `const query = …; return query;`,
`return { ...query };`, `return query.data;` — are assignment errors under `npm run type-check`.

**The eslint ban is a second signal and is narrower than it looks.** The `no-restricted-syntax`
block in `eslint.config.mjs` scoped to this path matches the literal
`return <…>.useQuery(...)` shape ONLY. It does not catch a result assigned to a local and then
returned — which is the idiom the file now uses on every one of the five screens — nor a spread of
one, nor its `data`. It is kept because `npm run lint` is a stage of the Docker build
(`Dockerfile:67`) and fails in seconds with a message naming the fix, not because it is the
guarantee. Neither half covers a hook placed in some OTHER file, and neither survives someone
deleting it.

Nor does any of this speak for lanes that are not react-query observers —
`useBotanicalOccurrences` (below) resets to `IDLE` when disabled (`useBotanicalOccurrences.ts:161-162`),
so it retains nothing and needs no gate.

## useBotanicalOccurrences: the proxy detail lane

Mounted in `LayerManager.tsx` as of 2026-09-18, feeding `BotanicalOccurrencesLayer`'s geojson and
its `readPhase`, with `describeBotanicalOccurrencesState` as the one caption wording.

**One botanical lane per band since 2026-09-18 (W8-D).** This hook (the proxy route) is the ONLY
read at the detail band: the UBC points, GBIF's toggle, the release-set pin and the store the
filters and details panels read all come off its one answer. `useBotanicalOccurrencesQuery` (tRPC)
serves the two aggregate layers and nothing else. The earlier note here -- that both lanes run at a
detail zoom -- described the state this replaced.

Which lane speaks is decided by the BAND, and whether it is speaking NOW by the read's own
published liveness -- never by which answer is in hand, and never by a predicate the consumer
assembles for itself. This query keeps `placeholderData: KEEP_PREVIOUS_WHILE_PANNING`, and a
DISABLED observer still serves the previous key's answer, so the raw `data` stays populated after a
zoom out of the aggregate band, after both aggregate toggles go off WITHIN it, and after the map
container collapses to zero size with every toggle still on.

Three consumer-side gates were written for that retained frame in three consecutive waves --
presence, then the band (W8 B3), then the caller's toggle gate (W9 S1) -- and each missed a
conjunct of an enablement it could not see. The third miss was `requested !== null`
(`useViewportProxiedLayers.ts:454`), which is DYNAMIC: `viewportBbox` returns null for a zero-size
or hidden container (`src/lib/map/viewport-bbox.ts:57-67`), and this repo has a named memory for
exactly that class of state (`plantgeo-hidden-tab-blank-map`).

So the gate moved to the definition (style review W10, B1). `useBotanicalOccurrencesQuery` returns
a `LiveViewportRead` (`useViewportProxiedLayers.ts:174-203`), not the react-query result: the
enablement is composed once at `useViewportProxiedLayers.ts:452-462`, passed to the observer as
`enabled` at `:481` and to `liveViewportRead` at `:489` unchanged, and the answer is withheld
whenever it does not hold (`:216-236`). The raw result is not exported, so a consumer has nothing
to re-derive a gate from, and a conjunct added to that expression reaches every consumer without
any consumer changing. See `src/components/map/AGENTS.md` section "The pin names the lane that drew
the cells" for the pin this corrupted three times.

**A refusal is not a failure.** The route answers governed 400/503 refusals with
`kind: "governed_refusal"` and their own `detail`; everything else is `transport_fault`.
`describeBotanicalOccurrencesState` quotes the former verbatim and keeps "could not be loaded" for
the latter -- see `src/components/map/AGENTS.md` section "Governed refusals read as refusals".

**The rung is selected, not assumed.** `servingBand` is the rung that actually answered -- the
route's `servingRung` once an answer lands, the hook's own selection before that, and null only
when no rung admits the viewport (the one case still refused client-side, without a round trip).
`describeBotanicalOccurrencesState` says so out loud whenever the served rung is not the one the
zoom asked for: a coarser rung is a SUBSTITUTION OF EVIDENCE, and a reader who is not told has no
way to know the drawing changed meaning.

## Regional selection windows

The regional intelligence stream requires a terminal `done`, `error` or `refusal` event. Clean
EOF before one arrives is a retryable incomplete-stream failure and ends the assistant's
streaming state. It preserves any partial narration and never overrides a terminal result or
the user's explicit cancellation.

`useRegionalIntelligence` reads the current map zoom, analysis time scale/range and sparse layer
date overrides when each request is sent. Visible layer days retain their independent dates; a
hidden layer with an explicit date retains that date too. Other queried layers inherit the most
recent selected comparison day (or the server day when no selection exists). Visibility never
limits tool discovery. The 64-row request limit accommodates the complete current map registry.
The analysis panel's window control affects the next request and never mutates a map layer date.
