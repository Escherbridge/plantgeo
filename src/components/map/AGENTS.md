# Map interaction boundary

Location selection is a privacy boundary. `AgentInteraction` requires an explicit user choice before analysis begins and defaults to an approximate (two-decimal) location; exact coordinates are opt-in. Regional analysis remains informational only: it cannot take external actions, and unavailable data must remain visibly unavailable rather than producing substitute recommendations.

## The layer toggle is the only source of layer visibility

`activeLayers` in `map-store` decides what renders. Changing render mode — basemap style, globe/mercator projection, 3D pitch, terrain — must never add or drop a layer, and must never silently discard render-mode state.

Two rules follow. First, projection changes set the projection and nothing else: `MapView`'s globe effect used to clamp zoom to ≤5 on entry and ≥3 on exit, which crossed the `minzoom` thresholds in `src/lib/map/layers.ts` (interventions 6, `osm-roads` 10, `building-footprints` 13, `buildings-3d` 14, `osm-waterways` 8) and made toggled-on layers vanish; the exit clamp also never restored the pre-globe zoom, so the round trip was lossy. Second, an empty data feed is not the same as a toggle being off: a layer whose feed returns nothing renders an empty source and stays mounted, so a later style swap cannot be mistaken for the user having hidden it.

The legend is a consumer of this vocabulary, not a second source of it — see below. So is the left-edge layer tree (`src/components/map/layer-panel/`): its eyes call the same `useToggleLayer` the right-hand sheets' switches call, so the two surfaces are two views of one value and cannot disagree.

## Opacity is a multiplier, applied per layer type

**A layer's opacity is a MULTIPLIER over its authored paint, never an absolute value.** `1` means "exactly as designed" and cannot regress anything. `src/lib/map/layer-opacity.ts` owns the rule; `layer-store.layerOpacity` (sparse, keyed by `LayerToggleId`, persisted) holds the numbers.

Two layers make the case on their own. `watersheds` paints its fill at `0.05` — a deliberate boundary wash — and its own outline at `0.6`, so an absolute slider would need a different neutral position per style layer, and dragging it to 1 would drop an opaque blue sheet over the map. `published-fire-outlines` is worse: its `circle-opacity` is deliberately `0`, with the visible ring carried on `circle-stroke-opacity`, so an absolute write would fill every fire circle with a second opaque disc. Under a multiplier, `0 × f = 0` preserves both intentions for free.

**Three style layers carry a data-driven opacity expression a scalar write would destroy**, and nothing re-adds a layer except a basemap swap, so destroying one is permanent for the session. `soil-moisture-field-outline` and `soil-temperature-field-outline` carry `["case", ["==", ["get","aggregated"], true], 0, 0.25]` — the rule that stops isoband contours being stroked (see §soil-field) — and `buildings-3d` carries a zoom `interpolate` fade-in. `scaleOpacityValue` multiplies a number in JS and wraps anything else as `["*", base, factor]`, which is legal for every opacity property and keeps a zoom-dependent value zoom-dependent. **Never branch on an expression's shape**; read the base off the module that paints it and wrap it.

**One writer per (layer, paint property), split on the registry's `renderKind`.** Style-baked layers have no component, so `LayerManager.applyOpacity` is their single writer, reaching only the pairs `styleLayerOpacityTargets()` derives from `styleBackedLayerEntries()` crossed with `getLayers()` — basemap decoration, the service-area mask and every component-added layer are structurally out of reach. Component-mounted layers take an `opacityScale` prop and fold it into whatever they already compute, because five of them already own `setPaintProperty` on the same properties and two of those rewrite on every pan; an external writer would be silently reverted the next time the user panned. For `VegetationLayer` the multiplier must thread *through* the component for a second reason: its layout-visibility gating is semantic (it is how the layer switches between the measured cells and the GIBS composite), and an outside writer would fight it.

**The property is per layer TYPE**: fill→`fill-opacity`, line→`line-opacity`, circle→`circle-opacity` + `circle-stroke-opacity`, symbol→`icon-opacity` + `text-opacity` (`weather-wind` sets no `icon-image`, so only the text one means anything), raster→`raster-opacity`, fill-extrusion→`fill-extrusion-opacity`, heatmap→`heatmap-opacity`. **`hillshade` is absent from that table on purpose** — the MapLibre style spec defines no `hillshade-opacity`, and a rejected paint property surfaces through `MapView`'s `error` handler, which hard-codes a PMTiles-expiry message and would blame the basemap for it.

**Opacity 0 is unreachable** (`MIN_LAYER_OPACITY`, clamped in the store on write and on persist-rehydrate). `visibility: "none"` — what the eye sets — excludes a layer from `queryRenderedFeatures`; an opacity-0 layer is *not* excluded, so it would still swallow the "did the user click empty ground" check in `MapView` and still fire the fire/gauge/well popups for features nobody can see. The eye turns a layer off; the slider only makes it recede. Do not couple them.

**The opacity record must never enter the `style.load` handler's dependency array.** It is read through `layerOpacityRef`, exactly as `layerVisibility` and the slider day are — see "Style.load listener order" below for what re-registering that handler does to the service-area mask. A slider fires far more often than a settled scrub, so this is the likeliest place to hit that trap.

## The legend legends what is drawn

`Legend` renders one section per switched-on layer, resolved from `useLayerVisibility()` against the specs in `src/lib/map/layer-legends.ts`, and renders nothing while every toggle is off. It is mounted in `MapView` beside `TimeSliderPanel`, so an encoding stays readable with every panel closed. It used to list `trpc.layers.list` rows — `geo.layers` names, one flat `stylePresets`/`styleOverrides` swatch each — which named warehouse publications rather than drawn encodings: a fill keyed to a `severity` match showed as one arbitrary colour, a ramp showed as nothing at all, and the card needed a network round-trip before it could say anything. The 2026-08-07 rewrite dropped tRPC and every `layer-store` field but `legendVisible` from it.

Two invariants keep it honest, both enforced in `layer-legends.ts`. First, **no colour is written in the legend**: every swatch, class row and ramp stop is imported from the module whose paint expression uses it, and where a ramp was inline in a renderer the renderer now reads an exported constant (`FIRE_CONTAINMENT_COLOR_STOPS`, `DEMAND_DENSITY_COLOR_STOPS`, `BURN_SEVERITY_ACRES_STOPS`, the `StyleClass` tables in `layers.ts`), so the two cannot drift. Second, **a toggle earns a spec only if switching it on paints something**: `soil` has none because `getEnvironmentalTileTemplate` returns `""` and `SoilLayer` adds no source at all; `vegetation` legends NDVI only, because `getNDWITileUrl` returns `""` unconditionally and NBR is unpublished for the same reason; `building-footprints` has none because the registry withholds it. `LEGENDLESS_TOGGLE_REASONS` records each. Legending a colour the map never draws is the failure this module exists to prevent, so an entry that "looks missing" is a claim to check against the renderer, not a gap to fill.

Where the inventory of encodings and the map disagree, the map wins: `burn-severity` is a ramp over **acres**, not MTBS severity classes (that column is null on every published row), and the class tables in `BurnHistoryLayer`/`LandFireLayer` are legended nowhere because `LayerManager` mounts neither component.

## The layer registry and the toggle context

`src/lib/map/layer-registry.ts` is the single declaration of what a toggle id *is*: its style layer ids, its `geo.layers` name, the panel that owns its switch, and any reason governance withholds it. Three tables used to carry overlapping halves of that — `STYLE_LAYER_TOGGLE_MAP` in `layers.ts`, `TOGGLE_ID_BY_LAYER_NAME` in `Legend`, `PANEL_LAYER_MAP` in `panel-store` — keyed by the same untyped strings with nothing making them agree, so adding a layer meant three hand-edits and forgetting one failed silently (a legend row that toggles nothing, a panel that under-reports itself active). All three are now derived from the registry, and `LayerToggleId` makes a typo a compile error rather than an inert string in `activeLayers`. The registry/`layer-toggle-context` pairing above is the only layer-visibility system: a second, unmounted toggle system (the old `src/components/panels/LayerPanel.tsx` with `LayerItem`/`LayerStyler`/`LayerFilter`, plus the `styleOverrides`/`filterExpressions` fields they alone read on `layer-store`) sat dormant and dead since it predated the registry; it was deleted 2026-08-07 rather than kept as a second source of the same vocabulary. The `LayerPanel` in `src/components/map/layer-panel/` is a different, mounted component built on the registry — see "The layer tree is an additional surface" below.

`src/lib/map/layer-toggle-context.ts` is the composition layer over that registry — what is switched on, the mode each layer draws in, and the slider's day, in one place both the map and the panels read. It owns **no state**: `activeLayers` stays in `map-store`, the day stays in `time-slider-store`, per-layer mode stays in `vegetation-store`/`soil-store`. There is no React Provider and there must not be one. A provider broadcasting `selectedDate` would re-render the whole map subtree on every pointer tick of a scrub, which is the exact storm the ref discipline below exists to prevent; Zustand stores are already ambient, so a provider would buy scoping nobody needs and cost a second subscription path.

The registry also owns the **label** and the **icon** a reader sees. Until 2026-08-08 every layer name was a hand-typed `label` prop at one of sixteen `<LayerToggle>` call sites, so nothing that was not one of those five panels could name a layer — the layer tree would have had to duplicate all sixteen strings or import five panels to render a list. `LayerToggle`'s `label` prop is now an optional override defaulting to `layerLabel(toggleId)`, and no call site passes one; `layer-registry.test.ts` pins the exact strings, so a silent rewording fails there. The icon is a NAME (`LayerIconName`), not a component: this module is imported by stores, by `layers.ts` and by node-run tests, and `src/components/map/layer-panel/layer-icons.tsx` is the one place it becomes React.

## The layer tree is an additional surface, not a replacement

`src/components/map/layer-panel/` is the left-edge dock: every switchable layer grouped by `panelId` in registry declaration order, each row carrying an eye, a colour chip read from `layerLegendSpec()`, the registry's name, and an opacity slider. The right-hand sheets keep their switches. Both write `map-store.activeLayers`, which the section above makes the single source of visibility, so the two cannot disagree by construction — that is what made adding the surface safe without migrating the sheets in the same change.

It is an **overlay inside `MapView`**, never a `MapLayout` side panel: reflowing the canvas would force a MapLibre `resize()` and a tile refetch on every collapse. The map's reaction is camera `padding` instead, which shifts the optical centre without touching canvas size and composes with `resetView` and `MapFocus`. Chrome that would sit under the dock reads `--layer-panel-inset` (set on `MapView`'s root, `0px`/`19rem`) — the icon rail and the bottom-left toolbar are its only consumers. `SearchBar` does not read it; the dock starts below it (`top-20`) instead.

`panel-scroll.ts` states the scroll contract once and `LayerPanel.test.tsx` asserts it: exactly one scrolling descendant, `min-h-0 flex-1` on it, `shrink-0` on every other direct child, and height from the container rather than from `vh`. Each rule is a fix for a defect on the surfaces this panel joins. The sharpest: **`overflow-y-auto` alone makes an element a scroll container on BOTH axes** (CSS Overflow 3 §3.1 — when one axis is not `visible`, the other's `visible` computes to `auto`). The icon rail carried it on an absolutely-positioned box whose shrink-to-fit width was its widest child, 44px, and the active-layer badge at `-right-0.5` pushed the scrollable region 2px past that — so Chrome drew a full horizontal scrollbar across a 44px column, appearing exactly when the first data layer was switched on. The vertical cap it was paired with never fired either: without `shrink-0` the buttons squashed toward their 16px icons long before 344px of content could overflow a 70vh box, silently trading away the 44px tap target.

**Deliberately not built, each for a reason rather than for time.** Drag reordering: paint order here is code, not data — the `beforeId` at each `addLayer` plus `style.load` listener registration order, which is load-bearing (see below) — and `activeLayers` is toggle-insertion order that means nothing spatially; a `map.moveLayer` would be discarded by the next basemap swap, giving a control that silently stops working at the style switcher. Blend modes: MapLibre has no per-layer blend mode, so anything shipped under that label would be a fake. Lock: Photoshop's lock guards against direct manipulation on canvas, and nothing here moves or edits a layer.

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

## DemandHeatmapLayer was parked; the gate has lifted

**Corrected 2026-08-08.** This section described `demand-heatmap` as carrying a permanent `unavailableReason`, with `activeLayers` unable ever to contain it. That has not been true since 2026-08-03: `layer-registry.ts` sets `permanentlyUnavailableReason: null` for it, `useLayerVisibility` reports it toggleable, and `CommunityPanel` renders a live switch. It is a real, controllable layer — it gets a row and an opacity slider in the layer tree like any other, on `heatmap-opacity`.

What satisfied the gate: `/api/v1/action-network` serves a k-anonymity-floored activity grid (`aggregateActivityGrid`, with a `HAVING count(*) >= 3` floor and bbox-independent cell membership), so publishing it never leaks a single private submission's location. That was the "reviewed, access-controlled warehouse publication" the switch was withheld pending, and the 2026-08-03 owner decision to open the governance stubs rather than preserve them settled the rest. `DemandHeatmapLayer`, `useActionNetworkFeatures` and the worker needed no changes.

The reason the chain was kept mounted while it was parked still stands as a rule: a withheld capability keeps its wiring, so the withholding stays visible as a decision rather than looking like something never built.

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

## The time slider is a collapsed top-bar pill, expanding to the region's header

`TimeSliderPanel` renders an always-mounted right-hand panel region whose sticky top is a
compact date pill in the top bar (`top-4`, level with `SearchBar`); the full scrubber card
opens below it behind a disclosure, collapsed by default, and the region's body below that is
conditional. This supersedes two earlier shapes in turn: the bottom-centre floating card
(`absolute bottom-24 left-1/2 -translate-x-1/2`), then the always-open card pinned at
`top-16` (2026-08-05 to 2026-08-06). The invariant that survived both moves: the day applies
to every layer, so its marker — now the pill, showing the date plus a "Past day" / "Beyond
record" chip whenever the selection is off the server's today — cannot be something a user
opens a panel (or a disclosure) to reach. Only the *controls* are behind the disclosure; the
*claim* never is. The pill also surfaces a bar-level Today reset while collapsed, because an
off-today date silently filters every layer and the way back must not cost a disclosure.

`TIME_SLIDER_CONTAINER_CLASSES` keeps its invariant — the loaded slider and the
`time-slider-unavailable` alert render from the same class list, so a fetch that later succeeds
cannot reposition or resize anything — and holds no positioning at all. All of it lives in
`PANEL_REGION_CLASSES` in `TimeSliderPanel.tsx`. The offsets there are collision avoidance
against known neighbours (`right-16` for MapLibre's top-right control stack, `top-4` to sit on
the `SearchBar` row), and the region is the single scroller: a scroller nested inside a
scroller is how the one drag control becomes unreachable on a phone. The region itself is
`pointer-events-none` with each interactive child opting back in — collapsed, it is a mostly
empty column over the canvas, and without the pass-through it would swallow map drags.

Panel sheets still portal themselves over the whole viewport, so an open panel covers the
region exactly as it covered the old dock. Docking a panel's body into the region's body slot
instead would mean giving `src/components/ui/sheet.tsx` a top offset for the time section —
deliberately not done here, because that file is shared by every sheet in the app.

## Picking a point to query

`SoilPanel` has accepted a `queryPoint` prop since it was written, and nothing ever passed
one: `PanelManager` mounted the panel without it and no `map.on("click")` handler anywhere
in `src/` produced one. The point query — SoilGrids properties and intervention
suitability at a place — was unreachable from the UI.

The wiring is deliberately split three ways rather than put in one component:

- **`map-store` holds the point** (`queryPoint`, `isCapturingQueryPoint`), beside
  `selectedFeatureId`. It is map interaction state, and a store is what lets the click
  handler, the panel and the pin layer see it without prop-drilling through `MapView`.
- **`PanelManager` arms capture**, through `useMapQueryPoint(map, isSoilPanelOpen)`. The
  panel that has a point query to answer is the one that turns clicks into points; capture
  is not always on, because a click on the map means different things depending on what is
  open.
- **`LayerManager` draws the pin**, via `QueryPointLayer`. The map owns its layers, and a
  GeoJSON source rather than a `maplibregl.Marker` so the pin re-attaches on `style.load`
  and survives a basemap swap like every other layer here.

**One click, one meaning.** `MapView`'s own click handler opens the agent popup on empty
ground. Both handlers would otherwise fire on the same click and the popup would cover the
pin, so `MapView` reads `isCapturingQueryPoint` from the store and stands down. It reads
the store imperatively rather than taking a prop, because that handler is registered once
for the life of the map and must not be re-registered as panels open and close.
Right-click still reaches the agent popup, so nothing becomes unreachable.

**Three ways to cancel.** Clicking the pin again, pressing Escape, and closing the panel
(`setCapturingQueryPoint(false)` clears the point as well as disarming). Clicking anywhere
else *moves* the pin — that is a second question about a second place, not a cancellation.
`SoilPanel` also renders an explicit "Clear queried point" button, because a pin the user
cannot obviously get rid of is worse than no pin.

## §soil-moisture

`SoilMoistureLayer` draws whatever `environmental.getSoilMoisture` served: the stored
0.25° cells at zoom ≥ 9, or dissolved isobands over a coarser aggregation lattice below
it. One source and one `fill` either way, because every served feature carries a `value` —
a cell's measurement, or a band's representative value. Branching the paint on granularity
would be a second place for the colour ramp to drift from the panel's legend; both read
`soilMoistureColorStops()` from `lib/environmental/soil-moisture.ts`.

Outlines are drawn only on unaggregated cells, where they say something true (these are
discrete samples). An isoband boundary is a contour through interpolated space, and
stroking it would draw a hard edge the data does not have.

**Why not deck.gl.** `@deck.gl/core`, `/layers`, `/geo-layers`, `/mapbox` and `/react` are
dependencies; **`@deck.gl/aggregation-layers` is not**, so `ContourLayer`, `ScreenGridLayer`
and `HeatmapLayer` are not available without adding one (this is also why
`layers/HeatmapLayer.tsx` is a `ScatterplotLayer` under the hood). Adding it would not help
anyway: those layers aggregate *on the client*, which is the thing the repo rule forbids
and the thing `geo.soil_moisture_field` exists to avoid. By the time geometry reaches the
browser it is at most nine polygons, which a MapLibre `fill` — already WebGL — draws for
free. No custom shader was needed and none was written.

The depth selector in `SoilPanel` is a **depth**, not a second clock: the day always comes
from the global time slider, as "One time control, projected per layer" above requires.
