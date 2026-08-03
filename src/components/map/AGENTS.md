# Map interaction boundary

Location selection is a privacy boundary. `AgentInteraction` requires an explicit user choice before analysis begins and defaults to an approximate (two-decimal) location; exact coordinates are opt-in. Regional analysis remains informational only: it cannot take external actions, and unavailable data must remain visibly unavailable rather than producing substitute recommendations.

## The layer toggle is the only source of layer visibility

`activeLayers` in `map-store` decides what renders. Changing render mode — basemap style, globe/mercator projection, 3D pitch, terrain — must never add or drop a layer, and must never silently discard render-mode state.

Two rules follow. First, projection changes set the projection and nothing else: `MapView`'s globe effect used to clamp zoom to ≤5 on entry and ≥3 on exit, which crossed the `minzoom` thresholds in `src/lib/map/layers.ts` (interventions 6, `osm-roads` 10, `building-footprints` 13, `buildings-3d` 14, `osm-waterways` 8) and made toggled-on layers vanish; the exit clamp also never restored the pre-globe zoom, so the round trip was lossy. Second, an empty data feed is not the same as a toggle being off: a layer whose feed returns nothing renders an empty source and stays mounted, so a later style swap cannot be mistaken for the user having hidden it.

The legend is a consumer of this vocabulary, not a second source of it. `trpc.layers.list` returns `geo.layers` rows whose ids are uuids and whose names (`fire-detections`, `water-gauges`, `weather-observations`, …) do not match the toggle ids that `LayerManager` and `STYLE_LAYER_TOGGLE_MAP` consume. `Legend` therefore translates DB name → toggle id and disables rows with no renderer; toggling a raw DB id pushes an inert string into `activeLayers` that nothing ever reads.

## Style swaps and render-mode state

`map.setStyle(styleObject)` defaults to `diff: true`, and for an object (rather than a URL) the diff path runs with no await: `Style.setState` applies the operations and fires `style.load` **synchronously, inside the `setStyle()` call**. A `once("style.load", …)` registered *after* `setStyle` therefore never runs. This matters because the diff serializes the live terrain/projection/sky and the target style declares none, so it emits `setTerrain(undefined)`, `setProjection(undefined)` and `setSky(undefined)` — a basemap switch silently kills terrain and reverts globe to mercator while `TerrainControl` and `GlobeToggle` still report "on". Register the restore handler before calling `setStyle`, and re-assert projection and terrain explicitly in both directions rather than only on the enabled branch.

## Style.load listener order

A style swap wipes custom layers, so every layer component re-adds its own on `style.load`. MapLibre stacks later-added layers above earlier ones sharing the same `beforeId`, which makes the listener registration order load-bearing: `ServiceAreaLayer` mounts before `LayerManager` in `MapView` so its dimming mask lands beneath the data pins that later components add.

That invariant only holds if each `style.load` handler registers exactly once per map. An effect that lists changing values (`activeLayers`, bbox corners) in its deps tears down and re-registers its listener on every change, moving it to the back of the queue and inverting the stacking. Such handlers read their inputs from a ref and register with `[map]`-shaped deps only; a separate, cheap effect applies the change immediately without touching the registration. For the same reason, do not pair a `once("style.load", add)` with an `on("style.load", add)` — the persistent listener already covers every future swap, and an `isStyleLoaded()` check covers the current one.

## Popups and hover labels

MapLibre's stock popup CSS hard-codes a white background but inherits its text color from the app, which is near-white under the default dark theme — popups rendered as blank cards until `globals.css` bound `.maplibregl-popup-content` to the `--card` tokens. Popup markup must therefore never hard-code text colors; use the `.map-popup-meta` class for secondary lines so both themes stay readable.

Event layers show the event's own time, not just its ingestion time: a fire carries a discovery date (and, for perimeters, a separate "perimeter updated" time), a detection carries its observation time, and a gauge reading carries when it was measured. Freshness is shown as a relative suffix on the absolute timestamp, never as a replacement for it — "3h ago" alone cannot distinguish a fire that started this morning from a decade-old incident whose record was just refreshed. `src/lib/map/time-format.ts` owns that resolution and formatting; upstream feeds disagree on encoding (WFIGS sends epoch milliseconds, USGS sends ISO, tile properties stringify both), and missing values must render as an omitted line rather than a partial label.

The action-network layer owns viewport cancellation through `useActionNetworkFeatures`. It may display only the bounded, worker-processed response and provides a retry affordance only for retryable failures. Freshness/revision labels are metadata, not a claim that a forecast or recommendation exists.
