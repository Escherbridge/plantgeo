# Map interaction boundary

Location selection is a privacy boundary. `AgentInteraction` requires an explicit user choice before analysis begins and defaults to an approximate (two-decimal) location; exact coordinates are opt-in. Regional analysis remains informational only: it cannot take external actions, and unavailable data must remain visibly unavailable rather than producing substitute recommendations.

## The layer toggle is the only source of layer visibility

`activeLayers` in `map-store` decides what renders. Changing render mode — basemap style, globe/mercator projection, 3D pitch, terrain — must never add or drop a layer, and must never silently discard render-mode state.

Two rules follow. First, projection changes set the projection and nothing else: `MapView`'s globe effect used to clamp zoom to ≤5 on entry and ≥3 on exit, which crossed the `minzoom` thresholds in `src/lib/map/layers.ts` (interventions 6, `osm-roads` 10, `building-footprints` 13, `buildings-3d` 14, `osm-waterways` 8) and made toggled-on layers vanish; the exit clamp also never restored the pre-globe zoom, so the round trip was lossy. Second, an empty data feed is not the same as a toggle being off: a layer whose feed returns nothing renders an empty source and stays mounted, so a later style swap cannot be mistaken for the user having hidden it.

The legend is a consumer of this vocabulary, not a second source of it. `trpc.layers.list` returns `geo.layers` rows whose ids are uuids and whose names (`fire-detections`, `water-gauges`, `weather-observations`, …) do not match the toggle ids that `LayerManager` and `STYLE_LAYER_TOGGLE_MAP` consume. `Legend` therefore translates DB name → toggle id and disables rows with no renderer; toggling a raw DB id pushes an inert string into `activeLayers` that nothing ever reads.

## The layer registry and the toggle context

`src/lib/map/layer-registry.ts` is the single declaration of what a toggle id *is*: its style layer ids, its `geo.layers` name, the panel that owns its switch, and any reason governance withholds it. Three tables used to carry overlapping halves of that — `STYLE_LAYER_TOGGLE_MAP` in `layers.ts`, `TOGGLE_ID_BY_LAYER_NAME` in `Legend`, `PANEL_LAYER_MAP` in `panel-store` — keyed by the same untyped strings with nothing making them agree, so adding a layer meant three hand-edits and forgetting one failed silently (a legend row that toggles nothing, a panel that under-reports itself active). All three are now derived from the registry, and `LayerToggleId` makes a typo a compile error rather than an inert string in `activeLayers`. User-uploaded layers (`LayerItem`) are deliberately outside the registry: their ids come from the database at runtime and cannot be a static union.

`src/lib/map/layer-toggle-context.ts` is the composition layer over that registry — what is switched on, the mode each layer draws in, and the slider's day, in one place both the map and the panels read. It owns **no state**: `activeLayers` stays in `map-store`, the day stays in `time-slider-store`, per-layer mode stays in `vegetation-store`/`soil-store`. There is no React Provider and there must not be one. A provider broadcasting `selectedDate` would re-render the whole map subtree on every pointer tick of a scrub, which is the exact storm the ref discipline below exists to prevent; Zustand stores are already ambient, so a provider would buy scoping nobody needs and cost a second subscription path.

Two rules the context encodes. First, availability is advisory: `shouldRender` follows the user's switch and governance only, never `availability`. A layer that vanished because the server has not published a capability yet is indistinguishable from one the user turned off — that ambiguity is what `describeAvailability` exists to prevent, so an unanswerable day yields a *caption*, not a hidden layer. For the same reason an absent capability reads as `published` rather than `not_yet_observed`: silence is not a measurement, and captioning every layer with a claim about history nobody measured would be a fabrication. Second, the day reaches a *query* only through `useDebouncedMapDay()` and never through a `style.load` handler — see below.

## The slider's day reaches queries, not style.load handlers

`useMapDay()` is the raw, per-tick day: correct for a *label* (`WaterPanel`'s "Map date" chip must track the pointer), wrong for a request. `useDebouncedMapDay()` is what every warehouse-backed query keys on. It differs from `useDebounce(useMapDay().selectedDate, …)` in a way that matters: it reads `time-slider-store` imperatively and only sets state once the scrub settles, so a day-granular scrub costs one render rather than one per pointer tick — and `LayerManager` sits above ~8 layer children. It settles on the same `SCRUB_SETTLE_MS` boundary `useMetricAtDate` debounces to, shared rather than restated, so two consumers can never issue two waves of requests for the same day. Its `requestDate` is `undefined` at the server's today; see `src/lib/server/AGENTS.md` §slider-day for why that is load-bearing rather than an optimisation.

The day must never enter a `style.load` handler's dependency array. Listing it there re-registers the handler on every scrub, moving it behind `ServiceAreaLayer`'s and dropping the dimming mask on top of the data pins — the ordering trap described under "Style.load listener order". Nothing needs it there: the queries above own the day, and the handlers only re-apply toggle visibility. (`useSelectedMapDateRef()` existed for a handler that would have read the day inline; it was removed with its last caller rather than left as an unused escape hatch.)

## Style swaps and render-mode state

`map.setStyle(styleObject)` defaults to `diff: true`, and for an object (rather than a URL) the diff path runs with no await: `Style.setState` applies the operations and fires `style.load` **synchronously, inside the `setStyle()` call**. A `once("style.load", …)` registered *after* `setStyle` therefore never runs. This matters because the diff serializes the live terrain/projection/sky and the target style declares none, so it emits `setTerrain(undefined)`, `setProjection(undefined)` and `setSky(undefined)` — a basemap switch silently kills terrain and reverts globe to mercator while `TerrainControl` and `GlobeToggle` still report "on". Register the restore handler before calling `setStyle`, and re-assert projection and terrain explicitly in both directions rather than only on the enabled branch.

## Style.load listener order

A style swap wipes custom layers, so every layer component re-adds its own on `style.load`. MapLibre stacks later-added layers above earlier ones sharing the same `beforeId`, which makes the listener registration order load-bearing: `ServiceAreaLayer` mounts before `LayerManager` in `MapView` so its dimming mask lands beneath the data pins that later components add.

That invariant only holds if each `style.load` handler registers exactly once per map. An effect that lists changing values (`activeLayers`, bbox corners) in its deps tears down and re-registers its listener on every change, moving it to the back of the queue and inverting the stacking. Such handlers read their inputs from a ref and register with `[map]`-shaped deps only; a separate, cheap effect applies the change immediately without touching the registration. For the same reason, do not pair a `once("style.load", add)` with an `on("style.load", add)` — the persistent listener already covers every future swap, and an `isStyleLoaded()` check covers the current one.

## Custom-added layers need a retriggerable readiness signal, not `once()`

A component that adds its own sources/layers (rather than toggling visibility on layers the style already declares, like `applyVisibility` above) faces a sharper version of the same race. `FireLayer` and `WaterLayer` both hard-loaded invisibly in dark mode: `map.isStyleLoaded()` read `false` on mount, the code fell back to `map.once("style.load", () => addAllLayers(map))`, and that handler never ran. `isStyleLoaded()` requires every source's tiles to be in, not just the style JSON parsed, so it can stay `false` well after `style.load` — including the synchronous fire inside `setStyle()`'s diff path — has already come and gone. A `once` registered against that already-past event fires never; switching the basemap or toggling the layer off/on "fixed" it only because those actions register a fresh listener against a fresh `style.load`.

The fix is `src/components/map/layers/use-style-ready.ts` — `useStyleReady(map)` subscribes with `on` (never `once`) to both `style.load` and `styledata`, recomputes `map.isStyleLoaded()` on each, and returns that boolean. Consumers don't gate on the returned value directly (mid-render it can be one tick stale); they put it in a `useEffect` dependency array purely to force a re-run, then re-read `map.isStyleLoaded()` live inside the effect — the same decoupled trigger-vs-gate shape `LayerManager`'s `styleReady` state already uses. `FireLayer` and `WaterLayer` now run two effects: one registers a persistent, unconditional `on("style.load", addAllLayers)` (safe because `addLayer`/`addSource` only require the style's `_loaded` flag, which is set at the same moment `style.load` fires — see `node_modules/maplibre-gl/src/style/style.ts` `_load()`/`setState()` — so this is also the primary mechanism that survives a basemap swap); the other depends on `useStyleReady`'s output and re-checks `map.isStyleLoaded()` live, which is what catches the mount-time race where no further `style.load` will ever arrive. Both call the same `addAllLayers`, which is idempotent — every `addSource`/`addLayer` call is guarded by `getSource`/`getLayer` — so redundant invocations from the two effects, or from a rapid style-catch-up, are no-ops rather than throws.

**Remaining files with the same class of bug (not fixed in this pass — do not assume they are safe):**
- `ErosionLayer.tsx`, `CarbonPotentialLayer.tsx`, `BurnHistoryLayer.tsx`, `ReforestationLayer.tsx`, `LandFireLayer.tsx`, `RecoveryLayer.tsx`, `LandCoverLayer.tsx` (two call sites) — all use `map.once("styledata", addLayers)`.
- `SoilLayer.tsx`, `DroughtLayer.tsx`, `RouteLayer.tsx`, `IsochroneLayer.tsx` — use `map.once("style.load", ...)`, the exact shape this section fixes in Fire/Water.
- `ModelLayer.tsx`, `AnimatedBeacon.tsx`, `ThreeLayer.tsx` — use `map.once("load", addLayer)`. `"load"` is the map's own one-time init event rather than a per-style event, so these don't retry across a basemap swap at all; whether that is a live bug depends on whether the map is ever re-created versus just re-styled.
- `VegetationLayer.tsx`, `WeatherLayer.tsx`, `DemandHeatmapLayer.tsx` — already dropped `once()` in favor of `if (map.isStyleLoaded()) addAllLayers(map); map.on("style.load", onStyleLoad);`, which fixes the basemap-swap case but **not** the mount-time race: if `isStyleLoaded()` reads `false` on mount and no later `style.load` arrives (because the current style already finished loading before this component mounted), nothing retries. These are the closest candidates for a follow-up `useStyleReady` adoption since the persistent-listener half is already in place.

Adopting `useStyleReady` in the files above is a known, deliberately deferred follow-up — each has its own layer/source ids and idempotency assumptions to verify individually rather than a mechanical find-replace.

## Popups and hover labels

MapLibre's stock popup CSS hard-codes a white background but inherits its text color from the app, which is near-white under the default dark theme — popups rendered as blank cards until `globals.css` bound `.maplibregl-popup-content` to the `--card` tokens. Popup markup must therefore never hard-code text colors; use the `.map-popup-meta` class for secondary lines so both themes stay readable.

Event layers show the event's own time, not just its ingestion time: a fire carries a discovery date (and, for perimeters, a separate "perimeter updated" time), a detection carries its observation time, and a gauge reading carries when it was measured. Freshness is shown as a relative suffix on the absolute timestamp, never as a replacement for it — "3h ago" alone cannot distinguish a fire that started this morning from a decade-old incident whose record was just refreshed. `src/lib/map/time-format.ts` owns that resolution and formatting; upstream feeds disagree on encoding (WFIGS sends epoch milliseconds, USGS sends ISO, tile properties stringify both), and missing values must render as an omitted line rather than a partial label.

The action-network layer owns viewport cancellation through `useActionNetworkFeatures`. It may display only the bounded, worker-processed response and provides a retry affordance only for retryable failures. Freshness/revision labels are metadata, not a claim that a forecast or recommendation exists.

## DemandHeatmapLayer is parked, not dead

`DemandHeatmapLayer` and the chain behind it — `src/hooks/useActionNetworkFeatures.ts`, `src/workers/action-network.worker.ts` — are unreachable at runtime and are meant to stay mounted anyway. The `demand-heatmap` toggle in `CommunityPanel` carries a permanent `unavailableReason` because the endpoint answers `ACTION_NETWORK_INACTIVE`/503 unconditionally: aggregate demand would leak the very locations the private request ledger exists to protect. So `activeLayers` can never contain `demand-heatmap` and the `LayerManager` gate never opens. `usePanelHasActiveLayers("community")` still works, because the community panel also owns `interventions`; `demand-heatmap` simply contributes nothing to it.

This is a governance stub, not accidental dead code. Deleting the chain would mean rebuilding it when the gate lifts, and would quietly erase the fact that the capability was withheld on purpose rather than never built. What re-enables it: a reviewed, access-controlled warehouse publication of aggregated demand, at which point the toggle's `unavailableReason` is dropped and the existing chain lights up unchanged.

Contrast `interventions` in the same panel, which must stay freely toggleable: it is a live Martin style layer (`interventionsLayer`/`interventionsOutlineLayer` in `src/lib/map/layers.ts`, mapped in `STYLE_LAYER_TOGGLE_MAP`, flipped by `LayerManager.applyVisibility`). Its own `minzoom` of 6 already hides it when zoomed out, so a disabled switch would block a control whose preconditions the user can satisfy.

## Sensors and evacuation-zones: re-connected, one SQL fix still outstanding

Both `sensorsLayer` and `evacuationZonesLayer`/`evacuationZonesOutlineLayer` (`src/lib/map/layers.ts`) are registered exactly like every other style-backed toggle -- `LAYER_REGISTRY` entries with `renderKind: "style"`, no hand-edits needed in `LayerManager`, `Legend`, or `STYLE_LAYER_TOGGLE_MAP`, all of which derive from the registry. `evacuation-zones` reads `geo.evacuation_zone_tiles()` (Drizzle migration `0009_evacuation_zone_tiles`) and paints on `severity`, populated unconditionally alongside `evacuation_level_label` whenever the Oregon OEM feed reports an `evacuationLevel` -- see `evacuation_zones.py build_evacuation_zone_write`.

`sensors` paints on `network`, the one property `sensors.py._matches_networks` guarantees is set on every row the NWS producer writes (it rejects a station before collection otherwise). That property is not yet in `geo.sensor_tiles()`'s `ST_AsMVT` SELECT list -- the function still only emits `sensor_type`/`status`/`name`, none of which any producer populates (the same "styled on a fabricated field" bug `interventions` once had, at the SQL layer instead of the paint layer). Until a migration replaces that SELECT list with `network`/`sensor_id`/`station_name`/`observed_at`, the sensors circle layer will render every one of the 750 published stations in the neutral grey fallback color rather than by network -- a degraded but honest default, not a crash.

## Deep-linking the camera

`/feed` links each proposed intervention to its site, so the map camera has to be
addressable from outside the map. The query contract lives in
`src/lib/map/focus-params.ts` — `focusLng`, `focusLat`, `focusZoom` — because two
unrelated modules must agree on it byte for byte, and `MapFocus.tsx` applies it.

`MapFocus` moves the camera directly rather than seeding the store. `MapView`
reads `viewport` once, inside an init effect with an empty dependency list, so
writing to the store only works on a cold mount and silently does nothing when a
client-side navigation lands on an already-mounted map. Moving the camera works
in both cases, and `MapView`'s own `moveend` handler writes the result back —
which keeps exactly one writer for viewport state.

Params are validated on read, never trusted: a URL is user input and MapLibre
throws on a non-finite centre instead of ignoring it. `prefers-reduced-motion`
turns the `flyTo` into a `jumpTo`.

## One time control, projected per layer

There is exactly one notion of "when" on this map: `time-slider-store`'s `selectedDate`. Any
layer whose upstream is coarser than a day derives its own grain from that day — it does not
keep state for it.

`VegetationPanel` used to own a Year slider and a Month slider backed by `vegetation-store`'s
`year`/`month`, so the app had two clocks that could disagree, and two controls competing to be
"the" time control. Those fields and both sliders were removed on 2026-08-05.
`useVegetationDisplayMode` now projects the day onto the GIBS composite period, and
`vegetation-store` holds display state only (`mode`, `ndviMode`, `showNDWI`, `opacity`).

Two details make the projection safe to copy for the next coarse-grained layer:

- **Read the settled day, and memoize on the derived grain, not the day.**
  `useVegetationDisplayMode` reads `useDebouncedMapDay()` and returns an object memoized on
  `(year, month)`. Scrubbing thirty days inside one month therefore leaves every prop
  `VegetationLayer` keys its `setTiles` effect on referentially unchanged. Memoizing on the day
  would re-request a month-granular tile once per day scrubbed.
- **A day the upstream does not cover is stated, not drawn blank.** `resolveGibsNdviDate`
  refuses a period outside the product's published extent rather than emitting a URL that 404s,
  so `compositeUnavailableReason` names the gap and `VegetationPanel` renders it on the page.
  Coverage is judged against `serverCurrentDate`, never `new Date()`: across New Year the two
  disagree by a whole year. Before capabilities land there is no day, so `year`/`month` are
  `null` and no raster is attached — a browser-clock default would draw a period nobody chose.

## The time slider is the right-hand region's header, not a floating card

`TimeSliderPanel` renders an always-mounted right-hand panel region with the slider pinned
(`sticky`) at its top; the region's body below it is conditional. This supersedes the earlier
bottom-centre dock (`absolute bottom-24 left-1/2 -translate-x-1/2`, which lived in
`TIME_SLIDER_CONTAINER_CLASSES`): the day applies to every layer, so its marker cannot be
something a user opens a panel to reach, and a card floating over the canvas read as one more
per-layer widget.

`TIME_SLIDER_CONTAINER_CLASSES` keeps its invariant — the loaded slider and the
`time-slider-unavailable` alert render from the same class list, so a fetch that later succeeds
cannot reposition or resize anything — and now holds no positioning at all. All of it lives in
`PANEL_REGION_CLASSES` in `TimeSliderPanel.tsx`. The two offsets there are collision avoidance
against known neighbours (`right-16` for MapLibre's top-right control stack, `top-16` for
`MapControls`' centred toolbar and `SearchBar`), and the region is the single scroller: a
scroller nested inside a scroller is how the one drag control becomes unreachable on a phone.

Panel sheets still portal themselves over the whole viewport, so an open panel covers the
region exactly as it covered the old dock. Docking a panel's body into the region's body slot
instead would mean giving `src/components/ui/sheet.tsx` a top offset for the time section —
deliberately not done here, because that file is shared by every sheet in the app.
