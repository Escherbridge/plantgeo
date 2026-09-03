# Map interaction boundary

Which spatial form a layer may be drawn in is frozen in `src/lib/map/AGENTS.md` §The layer render contract.

Location selection is a privacy boundary. `AgentInteraction` requires an explicit user choice before analysis begins and defaults to an approximate (two-decimal) location; exact coordinates are opt-in. Regional analysis remains informational only: it cannot take external actions, and unavailable data must remain visibly unavailable rather than producing substitute recommendations.

## The layer toggle is the only source of layer visibility

`activeLayers` in `map-store` decides what renders. Changing render mode — basemap style, globe/mercator projection, 3D pitch, terrain — must never add or drop a layer, and must never silently discard render-mode state.

Two rules follow. First, projection changes set the projection and nothing else: `MapView`'s globe effect used to clamp zoom to ≤5 on entry and ≥3 on exit, which crossed the `minzoom` thresholds in `src/lib/map/layers.ts` (interventions 6, `osm-roads` 10, `buildings-3d` 14, `osm-waterways` 8) and made toggled-on layers vanish; the exit clamp also never restored the pre-globe zoom, so the round trip was lossy. Second, an empty data feed is not the same as a toggle being off: a layer whose feed returns nothing renders an empty source and stays mounted, so a later style swap cannot be mistaken for the user having hidden it.

The legend is a consumer of this vocabulary, not a second source of it — see below. So is the left-edge manager (`src/components/map/layer-panel/`), which since 2026-08-08 owns the only switch a layer has: its eyes call `useToggleLayer`, and the sixteen `<LayerToggle>` switches that used to call it from seven right-hand sheets went with the sheets. The last competing writer went on 2026-08-09 with the bottom toolbar, whose 3D-footprints button wrote the same `activeLayers` entry the manager's own row for that layer wrote — and whose other five controls (basemap, terrain, globe, tilt) were render mode, which this rule says must never touch a layer. They are now a section that owns no layer switch at all, so the separation is structural rather than remembered.

## Opacity is a multiplier, applied per layer type

**A layer's opacity is a MULTIPLIER over its authored paint, never an absolute value.** `1` means "exactly as designed" and cannot regress anything. `src/lib/map/layer-opacity.ts` owns the rule; `layer-store.layerOpacity` (sparse, keyed by `LayerToggleId`, persisted) holds the numbers.

Two layers make the case on their own. `watersheds` paints its fill at `0.05` — a deliberate boundary wash — and its own outline at `0.8`, so an absolute slider would need a different neutral position per style layer, and dragging it to 1 would drop an opaque blue sheet over the map. `published-fire-outlines` is worse: its `circle-opacity` is deliberately `0`, with the visible ring carried on `circle-stroke-opacity`, so an absolute write would fill every fire circle with a second opaque disc. Under a multiplier, `0 × f = 0` preserves both intentions for free.

**Three style layers carry a data-driven opacity expression a scalar write would destroy**, and nothing re-adds a layer except a basemap swap, so destroying one is permanent for the session. `soil-moisture-field-outline` and `soil-temperature-field-outline` carry `["case", ["==", ["get","aggregated"], true], 0, 0.25]` — the rule that stops isoband contours being stroked (see §soil-field) — and `buildings-3d` carries a zoom `interpolate` fade-in. `scaleOpacityValue` multiplies a number in JS and wraps anything else as `["*", base, factor]`, which is legal for every opacity property and keeps a zoom-dependent value zoom-dependent. **Never branch on an expression's shape**; read the base off the module that paints it and wrap it.

**One writer per (layer, paint property), split on the registry's `renderKind`.** Style-baked layers have no component, so `LayerManager.applyOpacity` is their single writer, reaching only the pairs `styleLayerOpacityTargets()` derives from `styleBackedLayerEntries()` crossed with `getLayers()` — basemap decoration, the service-area mask and every component-added layer are structurally out of reach. Component-mounted layers take an `opacityScale` prop and fold it into whatever they already compute, because five of them already own `setPaintProperty` on the same properties and two of those rewrite on every pan; an external writer would be silently reverted the next time the user panned. For `VegetationLayer` the multiplier must thread *through* the component for a second reason: its layout-visibility gating is semantic (it is how the layer switches between the measured cells and the GIBS composite), and an outside writer would fight it.

**The property is per layer TYPE**: fill→`fill-opacity`, line→`line-opacity`, circle→`circle-opacity` + `circle-stroke-opacity`, symbol→`icon-opacity` + `text-opacity` (`weather-wind` sets no `icon-image`, so only the text one means anything — that is still true, but the `weather` toggle now also owns a `circle` layer, `weather-temperature`, and `WeatherLayer` writes both layers' properties itself), raster→`raster-opacity`, fill-extrusion→`fill-extrusion-opacity`, heatmap→`heatmap-opacity`. **`hillshade` is absent from that table on purpose** — the MapLibre style spec defines no `hillshade-opacity`, and a rejected paint property surfaces through `MapView`'s `error` handler, which hard-codes a PMTiles-expiry message and would blame the basemap for it.

**Opacity 0 is unreachable** (`MIN_LAYER_OPACITY`, clamped in the store on write and on persist-rehydrate). `visibility: "none"` — what the eye sets — excludes a layer from `queryRenderedFeatures`; an opacity-0 layer is *not* excluded, so it would still swallow the "did the user click empty ground" check in `MapView` and still fire the fire/gauge/well popups for features nobody can see. The eye turns a layer off; the slider only makes it recede. Do not couple them.

**The opacity record must never enter the `style.load` handler's dependency array.** It is read through `layerOpacityRef`, exactly as `layerVisibility` and the slider day are — see "Style.load listener order" below for what re-registering that handler does to the service-area mask. A slider fires far more often than a settled scrub, so this is the likeliest place to hit that trap.

## The legend IS the active-layer chips

`LayerLegend` (`src/components/map/layer-panel/LayerLegend.tsx`) resolves the drawn layers once from `useLayerVisibility()` against the specs in `src/lib/map/layer-legends.ts`, and renders that one resolution in two states of itself. **Collapsed it is a chip row** — a swatch and the registry label per drawn layer, scrolling sideways with no painted scrollbar. **Expanded it is the taxonomy** — the same layers grouped under the same category headings the layer tree uses, each with the blocks that say what its colours mean. It opens on hover, on focus and on click, because a pointer, a keyboard and a touch screen each have exactly one of those; Escape closes a pinned one.

**One component, because it is one fact.** The alternative shipped in most map UIs — a chip strip naming the drawn layers plus a legend card explaining their colours — is two renderings of `activeLayers` that have to be kept in step, and the failure mode is silent: a layer appears in one and not the other. Here `entries` is computed once and the collapsed state is a projection of it, so there is nothing to synchronise.

It renders nothing while every toggle is off, which is how the map starts, and nothing while the manager is open: the layer tree in there already carries a swatch, a name and a category heading per layer. It is mounted inside `ManagerRail`, which is the whole of the collapsed manager.

Two shapes preceded it. Until 2026-08-07 it listed `trpc.layers.list` rows — `geo.layers` names, one flat `stylePresets`/`styleOverrides` swatch each — which named warehouse publications rather than drawn encodings: a fill keyed to a `severity` match showed as one arbitrary colour, a ramp showed as nothing at all, and the card needed a network round-trip before it could say anything. That rewrite dropped tRPC and every `layer-store` field but `legendVisible`. `legendVisible` itself went on 2026-08-09: it was a global boolean with two controls over it (the card's own eye and a second one in the manager's header, the latter governing a card the reader could not see while using it), and disclosure here is now local to the component that owns it.

Two invariants keep it honest, both enforced in `layer-legends.ts`. First, **no colour is written in the legend**: every swatch, class row and ramp stop is imported from the module whose paint expression uses it, and where a ramp was inline in a renderer the renderer now reads an exported constant (`FIRE_DETECTION_FRP_COLOR_STOPS`, `DEMAND_DENSITY_COLOR_STOPS`, `BURN_SEVERITY_ACRES_STOPS`, the `StyleClass` tables in `layers.ts`), so the two cannot drift. Second, **a toggle earns a spec only if switching it on paints something**: `soil` has none because `getEnvironmentalTileTemplate` returns `""` and `SoilLayer` adds no source at all; `vegetation` legends NDVI only, because `getNDWITileUrl` returns `""` unconditionally and NBR is unpublished for the same reason. `LEGENDLESS_TOGGLE_REASONS` records each. Legending a colour the map never draws is the failure this module exists to prevent, so an entry that "looks missing" is a claim to check against the renderer, not a gap to fill.

Where the inventory of encodings and the map disagree, the map wins: `burn-severity` is a ramp over **acres**, not MTBS severity classes (that column is null on every published row). The unmounted `BurnHistoryLayer`/`LandFireLayer` components, whose class tables were legended nowhere for that reason, were deleted 2026-08-08 with the rest of the never-mounted layer files. `src/lib/server/services/landfire.ts` is a *server* module with the same name and is **not** dead in the same sense — it is the read-model side and out of a control-surface refactor's reach; check its callers before assuming.

The chip in a layer row and the chip in the legend are the same `LayerSwatch`, and it decides its own shape from the toggle id (`POINT_LAYER_TOGGLE_IDS` lives in that file). It took an `isRound` prop until 2026-08-09, which meant the point-layer set was written out at each call site — two copies of one classification, in two files, with nothing making them agree.

## The layer registry and the toggle context

`src/lib/map/layer-registry.ts` is the single declaration of what a toggle id *is*: its style layer ids, its `geo.layers` name, the panel that owns its switch, and any reason governance withholds it. Three tables used to carry overlapping halves of that — `STYLE_LAYER_TOGGLE_MAP` in `layers.ts`, `TOGGLE_ID_BY_LAYER_NAME` in `Legend`, `PANEL_LAYER_MAP` in `panel-store` — keyed by the same untyped strings with nothing making them agree, so adding a layer meant three hand-edits and forgetting one failed silently (a legend row that toggles nothing, a panel that under-reports itself active). All three are now derived from the registry, and `LayerToggleId` makes a typo a compile error rather than an inert string in `activeLayers`. The registry/`layer-toggle-context` pairing above is the only layer-visibility system: a second, unmounted toggle system (the old `src/components/panels/LayerPanel.tsx` with `LayerItem`/`LayerStyler`/`LayerFilter`, plus the `styleOverrides`/`filterExpressions` fields they alone read on `layer-store`) sat dormant and dead since it predated the registry; it was deleted 2026-08-07 rather than kept as a second source of the same vocabulary. The `LayerPanel` in `src/components/map/layer-panel/` is a different, mounted component built on the registry — see "One dock, no sheets" below.

`src/lib/map/layer-toggle-context.ts` is the composition layer over that registry — what is switched on, the mode each layer draws in, and the slider's day, in one place both the map and the panels read. It owns **no state**: `activeLayers` stays in `map-store`, the day stays in `time-slider-store`, per-layer mode stays in `vegetation-store`/`soil-store`. There is no React Provider and there must not be one. A provider broadcasting `selectedDate` would re-render the whole map subtree on every pointer tick of a scrub, which is the exact storm the ref discipline below exists to prevent; Zustand stores are already ambient, so a provider would buy scoping nobody needs and cost a second subscription path.

The registry also owns the **label** and the **icon** a reader sees. Until 2026-08-08 every layer name was a hand-typed `label` prop at one of sixteen `<LayerToggle>` call sites, so nothing that was not one of those five panels could name a layer — the dock would have had to duplicate all sixteen strings or import five panels to render a list. The labels moved here, `LayerToggle`'s `label` prop became an optional override that no call site passed, and later the same day `LayerToggle` itself was deleted with the sheets: a hand-typed layer name is now inexpressible rather than merely discouraged. `layer-registry.test.ts` pins the exact strings, so a silent rewording fails there. The icon is a NAME (`LayerIconName`), not a component: this module is imported by stores, by `layers.ts` and by node-run tests, and `src/components/map/layer-panel/layer-icons.tsx` is the one place it becomes React.

## One manager, no floating surfaces

**Superseded 2026-08-09.** This section was "The layer tree is an additional surface, not a replacement" (a dock beside seven sheets), then "One dock, no sheets" (2026-08-08). `src/components/map/layer-panel/` is now the only **control** surface on the map: every setting a reader can change is a section of one left-edge column, and what is left outside it collapses with it.

### The 2026-08-09 wave: search, the toolbar, the legend card

Three floating surfaces went. Each removal answers a specific defect, not a taste:

- **`SearchBar` (+ `SearchResults`, `RecentSearches`) → the Search section.** The field was a glass card at `top-4 left-4`, which is exactly where the manager's own header sits: the two overlapped, and search was the only control in the app that was neither in the manager nor reachable from it. The two result lists were the *same list rendered twice* — a `ZOOM_BY_TYPE` table, a `ResultIcon` switch and a `max-h-[300px] overflow-y-auto` in each — so they became one list told which rows to draw, and the nested scroller went with them (`panel-scroll.ts` rule 2). Its arrow-key handler was bound to the `<ul>` behind `tabIndex={-1}`, which cannot receive keys while someone is typing in the field above it, so arrow selection had never actually worked; it is bound to the field now.
- **`MapControls` (+ `TerrainControl`, `GlobeToggle`) → the View section.** A floating bar across the bottom of the canvas. Four of its controls are render mode, and the fifth was a **3D-footprints switch writing the same `activeLayers` entry the layer tree's own row wrote**, with one of the two always out of sight — the identical defect the sixteen `<LayerToggle>`s had. (That layer is gone entirely as of 2026-08-15; see "Removed 2026-08-15" below.) The View section carries no layer switch at all, which turns "render mode never touches a layer" from a rule into a structural property. `StyleSwitcher` survived the move (a swatch row is a swatch row); the two icon-button files did not, because an unlabelled 40px icon button is toolbar chrome and a sidebar row wants its name visible.
- **`CommandPalette` (+ `src/components/ui/command.tsx`) → nothing, and Ctrl/Cmd+K repointed.** The palette duplicated basemap, terrain, globe, 3D and one layer toggle as *commands*, i.e. a second writer for every one of them. Ctrl/Cmd+K was never advertised as a palette shortcut — the chip that named it sat inside the search field — so it now opens the manager at the Search section and focuses the field. The one capability the palette had that geocoding does not is a jump to a typed coordinate pair (Photon answers names, not numbers); that is preserved as `parseCoordinatePair` in the Search section, bounds-checked, because MapLibre throws on a non-finite centre.
- **`Legend` → `LayerLegend`, inside the collapsed manager.** See "The legend IS the active-layer chips" above.
- **`DockToggle` → `ManagerRail`.** The button did not disappear; it moved into the one row that is the collapsed manager, alongside the alert bell (removed 2026-08-15) and the legend.

**What the collapsed state is.** One row at the bottom-left — where the toolbar used to be, deliberately **not** where the search field used to be, since the top-left corner is what the manager's header claims. Both its parts have a fuller form inside the open manager: the button becomes the header's close control, and the legend becomes the layer tree. `ManagerRail` therefore returns `null` while the manager is open rather than repositioning, and `--layer-panel-inset` — the variable that used to slide the toolbar and the toggle out from under the panel — was deleted with its last two consumers.

### Removed 2026-08-15: Alerts, Environmental Analytics, 3D Building Footprints

Three surfaces went, and the shared reason is that each could only ever render its own absence:

- **Environmental Alerts** (`AlertDetails`, `AlertBell`, `useUnreadAlertCount`, `alerts-store`, the unmounted `tracking/AlertManager`). The section rendered "No alerts — add watched locations to get started" and nothing else, and the rail's bell was a permanent zero.
- **Environmental Analytics** (`AnalyticsDetails`, `lib/export/analytics-export.ts`). `analyticsRouter` answers its four procedures with `PRECONDITION_FAILED` until a versioned warehouse aggregate is published, so the panel's only reachable state was the notice saying so — with a CSV/PDF export beside it that could export nothing.
- **3D Building Footprints** (the `building-footprints` registry entry, `buildingFootprintsLayer`, the `building_tiles` composite member, its legend/hover/opacity wiring). `geo.osm_buildings` has 0 rows because the osm2pgsql import has never been run for the covered region, so the row was a permanently disabled switch with an explanation attached.

**No stubs were left behind.** Removing the layer took `panelId: PanelId | null` down to `panelId: PanelId` and took the ungoverned "Basemap" bucket (`UNGOVERNED_GROUP_KEY`, `UNCATEGORISED_LAYER_TOGGLE_IDS`) with it — that bucket existed for exactly one layer. `DockDetailsId` lost `"alerts"`, `PanelId` lost `"analytics"`, and `DetailsSection` lost its `badge` prop, whose only user was the unread count.

**The server side is untouched on purpose.** `alertsRouter`, `analyticsRouter`, `alert-engine`, the alert dispatcher/digest jobs and their tables all remain, and Martin still serves `building_tiles`. Restoring any of the three is a front-end change: re-add the panel and its section id, or re-list `building_tiles` in `DYNAMIC_TILE_SOURCE_IDS` and re-add the registry entry.

**Mobile is the same tree in a different box.** Under `max-sm` the shell is `inset-0 z-30`, square-cornered and borderless: a full-screen overlay. A 19rem drawer on a 360px screen leaves a 56px strip that is neither a usable map nor a usable panel. Nothing about the content branches on viewport — the sections, the reports and the one scroller are identical — and `useMapPaddingForPanel` skips the camera shift there, because there is no remaining map to re-centre and moving it would leave the reader somewhere else on dismissal. A bottom sheet with detents was considered and rejected: it needs a second scroll story (sheet drag versus content scroll) for the one surface that has exactly one scroller by contract.

**Keyboard bindings cannot live in a collapsing surface.** `MapControls` held r/t/g/1/2/3 and `CommandPalette` held Ctrl/Cmd+K, and both were mounted unconditionally. A section is unmounted while collapsed — that is the whole point of the mounting rule below — so the same `useEffect` inside `ViewDockSection` would silently unbind `t` the moment someone shut it. `MapKeyboardShortcuts` is headless, renders `null` and never unmounts, exactly as `TimeSliderCapabilitiesLoader` does and for the same reason. It reads `useMapStore.getState()` imperatively rather than through selectors, so the listener registers once.

### The 2026-08-08 wave: the sheets

What was removed then, and why each removal was a fix rather than a tidy-up:

- **`PanelManager.tsx` and its icon rail.** Seven unlabeled 44px buttons, one per sheet, each opening a full-height overlay over the map you were reading. `panel-store.openPanel` made them mutually exclusive, so comparing the fire counts with the water gauges meant closing one report to open the other. The dock stacks all eight, and the map stays visible beside them.
- **Every `<Sheet side="right">`.** A sheet portals a `fixed inset-0` scrim over the whole viewport (`src/components/ui/sheet.tsx`) and sets `document.body.style.overflow = "hidden"`. That covered the then-`TimeSliderPanel` region — the app's one time control — whenever any data panel was open.
- **`src/components/ui/layer-toggle.tsx`.** Sixteen switches across five sheets, each writing the same `activeLayers` entry the dock's own eye writes, half of them out of sight behind a closed sheet. Two controls over one value, and the one you could see did not always look like the one you had used. Deleted with its last call site; the dock's `LayerRow` eye is the only switch for a layer now.
- **Four never-mounted panels** (`RoutingPanel`, `IsochronePanel`, `EcosystemTracker`, `TeamProfilePanel`), which nothing had rendered in any tree.

The manager's shape:

```
ManagerRail           collapsed: [manager button] + LayerLegend + hint
LayerPanel            open: header (close), the one scroller, footer
  SearchDockSection   ┐ ControlDockSection ×2 — caret + name + a card that issues no
  ViewDockSection     ┘ warehouse query. Where and how it is drawn: both govern the
                        whole map rather than one category. There is no "when" section
                        here any more — see "Every layer draws its own day" below.
  DockSections        the rest of the scroller
    LayerGroupSection ×6   caret + group eye + "n of m" + LayerRow list
      DetailsSection       ×6 — the category's report, one per group
    DetailsSection    ×2   team, offline — the reports that own no layer
      DockDetailsBody      dynamic()-imported region + the map props it needs
```

The two control sections lead on purpose: each governs *every* layer, so one filed among the
categories would read as belonging to whichever it landed beside. Search is open on a cold load
(`INITIALLY_EXPANDED_SECTIONS` in `panel-store`); View is not, because a basemap is picked once a
session. `TimeDockSection` was a third control section, seeded open beside Search, from
2026-08-08 until per-layer sliders replaced it the very next day with one scrubber per row — see
"One capabilities fetch; the controls are per-layer rows, not a dock section" below for what
replaced it and why it could not simply move into a `LayerGroupSection`. That does not weaken the
mounting rule below — it is the exception that rule is stated for, and `ControlDockSection` is the
type-level statement of it: a section whose body issues nothing. `DetailsSection` and
`ControlDockSection` share their caret's class list and their `pendingScrollSection` handshake
through `dock-disclosure.ts`, so the manager has one disclosure vocabulary rather than several
that merely look alike.

Search's single query is the honest edge case: `IngestionCoverageBadge` calls
`layers.getIngestionCoverage` with an hour's `staleTime`. It is not a report's query — the
always-mounted search field used to issue it on every map load, so hosting it in a section that
can be shut strictly *reduces* requests. `useGeocode` itself issues nothing below two characters,
so a seeded-open Search section costs no request until someone types.

**Two carets, and they are not the same control.** A group's caret shows its layer rows: free, and open by default, because this is a layer manager first. A `DetailsSection`'s caret MOUNTS a report — `FireDetails`, `WaterDetails`, `VegetationDetails`, `SoilDetails`, `ClimateDetails`, `CommunityDetails`, `TeamDetails`, `OfflinePanel` — each with its own warehouse queries, and is closed until asked for. One caret over both would have had to choose, and either choice is wrong for half the dock. A group's caret deliberately does not hide the report under it, so a collapsed dock reads as an index of reports.

**Mounting is what "open" means now.** Every one of those regions used to take an `open` prop and gate its queries on it (`enabled: open && …`), staying mounted-but-disabled while its sheet was shut. A collapsed section is not mounted at all, so each region dropped the prop and the `open &&` term with it. This is load-bearing, not cosmetic: eight regions mounted on every dock open would fire eight panels' worth of queries before anyone had asked a question. `panel-store.expandedDetails` is therefore not a rename of `openPanel` — it is a list, several may be open at once, and it decides which queries exist. `viewport-proxied-query-sharing.test.tsx` pins both halves: one shared query entry when a section is expanded, and **no observer at all** when it is not.

Which is exactly why a control section may be seeded open and no report may: its body issues
nothing — Search's issues only what `IngestionCoverageBadge` and a typed `useGeocode` query cost,
both accounted for above; View's issues nothing at all. The id vocabulary widened to say that in
the type system rather than in a comment: `DockDetailsId` still means "a section with a report
behind it" and stays exhaustive over `DETAILS_LABELS` and `DETAILS_BODIES`, while `DockSectionId`
— `DockDetailsId | "search" | "view"` — is what `expandedDetails` and `pendingScrollSection`
speak. Adding those two to the report union instead would have forced a title and a
dynamically-imported body for something that is neither. `DockSectionId` carried a third member,
`"time"`, from 2026-08-08 until per-layer sliders replaced the single shared scrubber the very
next day, and `focusDockSection` spoke it too, on behalf of the top-bar date pill's click handler.
Both are gone rather than merely unused — there is no longer a single date to focus onto — see
"One capabilities fetch; the controls are per-layer rows, not a dock section" below.

**The one scroller survived the merge.** Each sheet body arrived carrying its own `overflow-y-auto max-h-[calc(100vh-8rem)]` wrapper, plus two `max-h-64` list boxes in the water report. Nested inside the dock's scroller those are exactly the second-scrollbar defect `panel-scroll.ts` rule 2 names, so they were stripped on the way in; the lists were already capped upstream (`WATERSHED_LIST_LIMIT`) and the dock scrolls them now.

**What is left outside the manager.** One thing, and it is a control: `ManagerRail`, the
collapsed manager itself — a 44px button back in and `LayerLegend` — which unmounts
the moment the manager opens. `TimeDatePill` used to be the other, a marker rather than a
control, mounted unconditionally over the top-right of the canvas; it was deleted 2026-08-09 with
the single shared day it named. See "One capabilities fetch; the controls are per-layer rows, not
a dock section" below for what marks a layer's day now that there is no longer one shared day to
mark.

One shortcut speaks `focusDockSection`, which docks the panel, expands the section and queues `pendingScrollSection` for that section to consume on arrival: Ctrl/Cmd+K with `"search"`. There were two others. The date pill spoke `"time"`, and both it and the `"time"` member of `DockSectionId` were deleted on 2026-08-09 — `panel-store.ts` now reads `DockDetailsId | "search" | "view"`. `AlertBell` spoke `"alerts"`, and went with the alerts feature on 2026-08-15, taking that member of `DockDetailsId` with it.

`MapDateSummary` replaced the pill, and is a marker rather than a shortcut — it speaks no `focusDockSection` and docks nothing. It mounts unconditionally in `MapView` as a sibling of `LayerPanel`, reading `useViewedLayerDays()`, `capabilities.serverCurrentDate` and the drawn-day registry below, and that independence is the whole point: every other statement of a layer's day now lives on its row, which requires the dock to be docked, the group expanded and the layer switched on. With the dock closed, three layers on three different months would otherwise render as one image carrying no date information at all — a composite anyone would read as a single moment. It states the shared day when the visible layers agree, and when they do not it says so with the span and the layer count. It must never grow controls; the controls are the rows.

## A layer must not blank between days, and what it draws meanwhile must be labelled

Two halves of one fix, landed together on 2026-08-16, and **neither may ship without the other**.

**The blanking.** Every live feed fell back to `EMPTY_FEATURE_COLLECTION` while pending, so each date change *and each pan* dropped the layer to zero features for a whole warehouse round trip before refilling. To a reader that is latency and staleness at once, and it is indistinguishable from a day the warehouse genuinely holds nothing for. `placeholderData: keepPreviousData` on each of them (`LayerManager`'s five tRPC reads, plus `useSoilSurveyQuery`/`useSoilFieldQuery`/`useClimateFieldQuery`) keeps the previous collection painted while the next request is **pending**.

**§retained-answers — it does not retain across a failure.** An errored query has `data: undefined` and `isPlaceholderData: false`, so the layer blanks exactly as it did before. Three consequences, all load-bearing: a failed day is *not* a retained frame; `!isPlaceholderData` is therefore **not** a test for "this request landed" (it is also false on error); and a surface must not treat "no placeholder" as "the answer arrived". Use a positive term — `isSuccess && !isPlaceholderData && data !== undefined` — which `drawnDayFlagsFromQuery` derives once for every reader. Recording a failed day as landed poisons the ledger: the next request's retained frame paints the day *before* the failure while the caption names the failed one.

**The label.** Retaining the previous day is only safe while nothing states the *requested* day over it — the rule `useMetricAtDate.resolvedDate` was written for: the surface may lag, it may not misstate. `MapDateSummary` read the sliders' positions, so with `keepPreviousData` alone it would have spent every fetch asserting the new day over the old day's features, and ordinary loading would read as a data bug.

**The plumbing.** `src/stores/useMetricAtDate.ts` owns it — `DrawnLayerDay`, `useDrawnLayerDayStore`, `usePublishedDrawnLayerDays`, `drawnDayFlagsFromQuery`. A store rather than a context, for the reason `layer-toggle-context.ts` gives for having no Provider: a provider broadcasting this would re-render the whole map subtree on every fetch. Neither persisted nor devtools-wrapped — a replayed "what is drawn" is a claim about a canvas that no longer exists.

Five rules the registry encodes:

- **The drawn day is the last requested day whose collection actually LANDED** — never merely the last one that was not a placeholder. See §retained-answers.
- **`isLoading` is narrowed to a request whose answer is not yet on the canvas.** A background refresh of the day already painted (`staleTime` expiring, or a refetch on window focus) is a fetch nobody is waiting on; publishing it blinked an "Updating" mark over an idle map every two minutes, back when the fire feed also polled on a timer.
- **A layer that is switched off publishes nothing.** TanStack keeps `isPlaceholderData` true off `keepPreviousData` even once a query is *disabled*, so a hidden layer would otherwise report itself permanently mid-load and leave a mark nothing could clear. This is the same trap `resolvedDate` documents, met from the other side.
- **Publishers own disjoint sets.** `LayerManager` publishes nine ids; each of the nine `ClimateSignalLayer`s publishes its own, because it owns its own read. The store merges per publisher, so one reader's silence cannot erase another's.
- **`isOnLatest` is re-answered against the DRAWN day** in `resolveDrawnViewedDays`. Carrying over the row's answer put two different days in one sentence: click "Latest" on a scrubbed layer and the line read an old date with no "behind its latest" mark, while the reverse marked a day that *was* the latest as behind it. Same rule as `useViewedLayerDays`, different subject — that hook answers for the requested day, which is right for the agent payload and wrong for a caption.

**A layer given `keepPreviousData` MUST get a report here in the same change.** The fallback for an unpublished layer — keep the row's day, claim no loading state — is honest only while unpublished layers blank during a load, and it is no longer only a caption concern: `DockSections` renders each row's pending indicator from `drawnDays[layerId]?.isLoading ?? false`, so an unreported layer's indicator is silently dead forever.

Coverage as of 2026-08-17, 18 of 27 toggles: `LayerManager` publishes nine (fire, drought, water, vegetation, soil-survey, soil-moisture, soil-temperature, soil-vpd, weather) and each `ClimateSignalLayer` publishes its own. The nine that do not are correct to be silent for two different reasons, and only one of them is permanent:

- **Six are style-baked** (`fire-perimeters`, `evacuation-zones`, `burn-severity`, `sensors`, `watersheds`, `interventions`). They issue no query at all — the day reaches them as a MapLibre filter applied in place, with no round trip — so there is never a request to be pending and never a retained frame to mislabel. A dead indicator is the right indicator here.
- **Three are component-mounted with reads this registry does not cover**: `soil` (SoilGrids raster tiles), `demand-heatmap` (`useActionNetworkFeatures`), `strategy-recommendations` (`StrategyLayer`). None carries `keepPreviousData` today, so nothing is currently mislabelled — but each is one option away from it, and their rows show no pending state in the meantime. Adding retention to any of them means adding a report in the same change.

The surface says two compatible things: an **"Updating" chip** whenever a visible layer has an open request whose answer is not yet painted, and a **second-line count** — *"N layers on earlier days"* — of layers whose painted day is older than the one their row asks for. They are different populations. Offline is why the second is not worded "still loading": `fetchStatus: "paused"` leaves a retained frame standing with nothing in flight, so the chip is correctly absent while the count is correctly present.

**Deliberately excluded: `useWatershedsQuery`.** Its only consumer is `WaterDetails`, which renders the basins as a *list* under a heading claiming they are the ones in view. A retained list is a false statement about the current viewport rather than an incomplete drawing of it, and that surface has no caption to say otherwise. Drawn geometry is self-locating and may lag; a list may not.

**Reachability is now answerable by import.** `dock-sections.ts` derives the groups from the registry and is deliberately React-free, so `layer-registry.test.ts` can assert that every layer has a row by calling `dockReachableLayerToggleIds()`. It used to answer the same question by regex-scanning `<LayerToggle>` out of the panel sources — the only handle available when a layer's sole switch was buried in a sheet's JSX.

Both surfaces wrote `map-store.activeLayers` while they coexisted, which the section above makes the single source of visibility. That is what made the merge safe to do in stages: the dock could ship beside the sheets without either one becoming authoritative, and the sheets could then be deleted without a visibility migration.

It is an **overlay inside `MapView`**, never a `MapLayout` side panel: reflowing the canvas would force a MapLibre `resize()` and a tile refetch on every collapse. The map's reaction is camera `padding` instead, which shifts the optical centre without touching canvas size and composes with `resetView` and `MapFocus`. `--layer-panel-inset` used to let the chrome that would sit under it slide out of the way; it was deleted on 2026-08-09 with its only two consumers (the toolbar, absorbed; the toggle button, now inside a rail that unmounts). The shell moved from `top-20` to `top-4` in the same pass, `top-20` having existed solely to clear the floating search field. **Its width did not change through either merge**: one 19rem column, eleven sections scrolling inside it, and no further pixel taken from the map.

`panel-scroll.ts` states the scroll contract once and `LayerPanel.test.tsx` asserts it: exactly one scrolling descendant, `min-h-0 flex-1` on it, `shrink-0` on every other direct child, and height from the container rather than from `vh`. Each rule is a fix for a defect on the surfaces this panel joins. The sharpest: **`overflow-y-auto` alone makes an element a scroll container on BOTH axes** (CSS Overflow 3 §3.1 — when one axis is not `visible`, the other's `visible` computes to `auto`). The icon rail carried it on an absolutely-positioned box whose shrink-to-fit width was its widest child, 44px, and the active-layer badge at `-right-0.5` pushed the scrollable region 2px past that — so Chrome drew a full horizontal scrollbar across a 44px column, appearing exactly when the first data layer was switched on. The vertical cap it was paired with never fired either: without `shrink-0` the buttons squashed toward their 16px icons long before 344px of content could overflow a 70vh box, silently trading away the 44px tap target.

**Deliberately not built, each for a reason rather than for time.** Drag reordering: paint order here is code, not data — the `beforeId` at each `addLayer` plus `style.load` listener registration order, which is load-bearing (see below) — and `activeLayers` is toggle-insertion order that means nothing spatially; a `map.moveLayer` would be discarded by the next basemap swap, giving a control that silently stops working at the style switcher. Blend modes: MapLibre has no per-layer blend mode, so anything shipped under that label would be a fake. Lock: Photoshop's lock guards against direct manipulation on canvas, and nothing here moves or edits a layer.

Two rules the context encodes. First, availability is advisory: `shouldRender` follows the user's switch and governance only, never `availability`. A layer that vanished because the server has not published a capability yet is indistinguishable from one the user turned off — that ambiguity is what `describeAvailability` exists to prevent, so an unanswerable day yields a *caption*, not a hidden layer. For the same reason an absent capability reads as `published` rather than `not_yet_observed`: silence is not a measurement, and captioning every layer with a claim about history nobody measured would be a fabrication. Second, the day reaches a *query* only through each layer's own `useDebouncedLayerDay(layerId)` and never through a `style.load` handler — see below.

## Each layer's day reaches its own queries, not a style.load handler

There is no map-wide day to read any more — see "Every layer draws its own day; there is no
map-wide 'when'" below for the full design this replaced. `useLayerDay(layerId)` is the raw,
per-tick day for ONE layer: correct for a *label* (a row's own date field must track the
pointer), wrong for a request. `useDebouncedLayerDay(layerId)` is what that layer's
warehouse-backed queries key on. It differs from
`useDebounce(useLayerDay(layerId).selectedDate, …)` in a way that matters: the settle timer
behind it reads `time-slider-store` imperatively and resolves THIS layer's day on every
notification, returning immediately when it did not move — so a scrub on one row arms one timer,
not one per mounted layer, and `LayerManager` sits above ~8 of them. It settles on the same
`SCRUB_SETTLE_MS` boundary `useMetricAtDate` debounces to, shared rather than restated, so a
layer's map feed and its own details region can never issue two waves of requests for the same
day. Its `requestDate` is `undefined` at the server's today; see `src/lib/server/AGENTS.md`
§slider-day for why that is load-bearing rather than an optimisation.

The day must never enter a `style.load` handler's dependency array, for any layer. Listing it
there re-registers the handler on every scrub, moving it behind `ServiceAreaLayer`'s and dropping
the dimming mask on top of the data pins — the ordering trap described under "Style.load listener
order". Nothing needs it there: the queries above own the day, and the handlers only re-apply
toggle visibility.

## Style swaps and render-mode state

`map.setStyle(styleObject)` defaults to `diff: true`, and for an object (rather than a URL) the diff path runs with no await: `Style.setState` applies the operations and fires `style.load` **synchronously, inside the `setStyle()` call**. A `once("style.load", …)` registered *after* `setStyle` therefore never runs. This matters because the diff serializes the live terrain/projection/sky and the target style declares none, so it emits `setTerrain(undefined)`, `setProjection(undefined)` and `setSky(undefined)` — a basemap switch silently kills terrain and reverts globe to mercator while the View section's Terrain and Globe rows still report "on". Register the restore handler before calling `setStyle`, and re-assert projection and terrain explicitly in both directions rather than only on the enabled branch.

## Style.load listener order

A style swap wipes custom layers, so every layer component re-adds its own on `style.load`. MapLibre stacks later-added layers above earlier ones sharing the same `beforeId`, which makes the listener registration order load-bearing: `ServiceAreaLayer` mounts before `LayerManager` in `MapView` so its dimming mask lands beneath the data pins that later components add.

That invariant only holds if each `style.load` handler registers exactly once per map. An effect that lists changing values (`activeLayers`, bbox corners) in its deps tears down and re-registers its listener on every change, moving it to the back of the queue and inverting the stacking. Such handlers read their inputs from a ref and register with `[map]`-shaped deps only; a separate, cheap effect applies the change immediately without touching the registration. For the same reason, do not pair a `once("style.load", add)` with an `on("style.load", add)` — the persistent listener already covers every future swap, and an `isStyleLoaded()` check covers the current one.

## `isStyleLoaded()` is a signal to retry on, never a gate to drop writes behind

**`isStyleLoaded()` requires every SOURCE's tiles to be in**, not just the style JSON to be parsed. One slow or failing source — an expired PMTiles pin, a 404ing Martin tile function — holds it false for the whole session. A Martin tile source routinely takes **84–117 seconds cold in production**, and until a CORS fix on 2026-08-17 the Martin composite never loaded at all, so this is the ordinary state of a session rather than a corner.

An `if (!map.isStyleLoaded()) return;` at the top of an effect therefore does not defer a write, it **discards** it: `styleReady` never changes, so the effect never re-runs and nothing is left to retry. Three appliers in `LayerManager` sat behind that gate at different times, and each failure was silent in its own way:

- **Visibility** (fixed 2026-08-15). Every style-baked layer painted whatever `getStyle()` authored while the dock reported "0 of 5". The toggles were never broken; they were never applied.
- **The date filter** (fixed 2026-08-16, and the worst of the three). Its only other writer is the direct call inside the `style.load` handler, which on a cold load runs before capabilities have arrived and therefore writes *no filter at all*. So while any source was slow, every date-filterable tile layer drew its **whole multi-year record** underneath a row whose slider read one day — no error, no caption, and dragging the slider changed nothing. Before the CORS fix that was 100% of production sessions.
- **Opacity** (fixed 2026-08-16). A slider dragged during the window was dropped outright, leaving the layer at its authored strength under a control reading something else.

**The shape all three now share.** The applier guards every write with `getLayer()`, so a pass against a half-built style is a no-op per missing layer rather than an error — which is what makes the gate unnecessary. (`Style._loaded` is set *before* `_createLayers()` populates `_layers`, so `getLayer()` returning truthy implies the style is loaded, and the `_checkLoaded()` throw inside each setter is unreachable from this path.) Each applier has (a) an unguarded effect keyed on its own input, which answers the reader's action immediately and **is what actually fixes the cold-load case**, and (b) a convergence pass on `styledata`, which catches a style layer that did not exist when (a) last ran. `styleReady` stays in the effects' deps purely as an extra trigger, never as a condition.

**What `styledata` actually is — do not repeat the folklore.** It is *not* fired per tile. `Style.update()` fires it only when `_changed`, and the twelve sites that set `_changed` are sprite/image mutations, `setGeoJSONSourceData`, `moveLayer`, `addLayer`/`removeLayer`, `_updatePaintProperty` and friends. **A tile landing fires `sourcedata`, not `styledata`** (verified in maplibre-gl 5.22.0). So the convergence pass is not what rescues a slow Martin source — (a) is. What it does rescue is layers added late, which every component that owns its own sources does on `style.load`.

**It is still the high-frequency path, for a different reason, and that is why it is throttled.** `setGeoJSONSourceData` sets `_changed`, and roughly twenty GeoJSON-backed layers refill on **every pan** — more so now that `keepPreviousData` lands a fresh collection per pan rather than one empty one. Visibility runs unthrottled there (a `setLayoutProperty` over a handful of layers), but the date filter and the opacity multiplier each build an expression per style layer before MapLibre gets to look at it, so both are coalesced onto one `requestAnimationFrame`. The effects in (a) are deliberately *not* on that frame: they answer the reader's own input. **Do not throttle with `SCRUB_SETTLE_MS`**; that constant exists to coalesce network requests, and neither of these issues one.

**Why redundant passes are cheap rather than merely tolerable.** `Style.setLayoutProperty`, `Style.setPaintProperty` and `Style.setFilter` all `deepEqual` the incoming value against the live one and return before touching the style, so re-writing an unchanged value is a genuine no-op — it marks nothing dirty, triggers no repaint, and crucially fires no further `styledata`. Without that early return a `styledata` handler that writes style properties would feed itself one pass per frame forever. Any new writer added to that path must be checked against the same property.

## Custom-added layers need a retriggerable readiness signal, not `once()`

A component that adds its own sources/layers (rather than toggling visibility on layers the style already declares, like `applyVisibility` above) faces a sharper version of the same race. `FireLayer` and `WaterLayer` both hard-loaded invisibly in dark mode: `map.isStyleLoaded()` read `false` on mount, the code fell back to `map.once("style.load", () => addAllLayers(map))`, and that handler never ran. `isStyleLoaded()` requires every source's tiles to be in, not just the style JSON parsed, so it can stay `false` well after `style.load` — including the synchronous fire inside `setStyle()`'s diff path — has already come and gone. A `once` registered against that already-past event fires never; switching the basemap or toggling the layer off/on "fixed" it only because those actions register a fresh listener against a fresh `style.load`.

The fix is `src/components/map/layers/use-style-ready.ts` — `useStyleReady(map)` subscribes with `on` (never `once`) to both `style.load` and `styledata`, recomputes `map.isStyleLoaded()` on each, and returns that boolean. Consumers don't gate on the returned value directly (mid-render it can be one tick stale); they put it in a `useEffect` dependency array purely to force a re-run, then re-read `map.isStyleLoaded()` live inside the effect — the same decoupled trigger-vs-gate shape `LayerManager`'s `styleReady` state already uses. `FireLayer` and `WaterLayer` now run two effects: one registers a persistent, unconditional `on("style.load", addAllLayers)` (safe because `addLayer`/`addSource` only require the style's `_loaded` flag, which is set at the same moment `style.load` fires — see `node_modules/maplibre-gl/src/style/style.ts` `_load()`/`setState()` — so this is also the primary mechanism that survives a basemap swap); the other depends on `useStyleReady`'s output and re-checks `map.isStyleLoaded()` live, which is what catches the mount-time race where no further `style.load` will ever arrive. Both call the same `addAllLayers`, which is idempotent — every `addSource`/`addLayer` call is guarded by `getSource`/`getLayer` — so redundant invocations from the two effects, or from a rapid style-catch-up, are no-ops rather than throws.

**Remaining files with the same class of bug (not fixed in this pass — do not assume they are safe):**
- `SoilLayer.tsx`, `DroughtLayer.tsx` — use `map.once("style.load", ...)`, the exact shape this section fixes in Fire/Water. (The other files this list named — `ErosionLayer`, `CarbonPotentialLayer`, `BurnHistoryLayer`, `ReforestationLayer`, `LandFireLayer`, `RecoveryLayer`, `LandCoverLayer`, `RouteLayer`, `IsochroneLayer`, `ModelLayer`, `AnimatedBeacon`, `ThreeLayer` — were never mounted and were deleted 2026-08-08; recover them from git history if a producer ever ships.)
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

## Every layer draws its own day; there is no map-wide "when"

**Rewritten 2026-08-09.** This section used to be titled "One time control, projected per layer"
and opened with "There is exactly one notion of 'when' on this map: `time-slider-store`'s
`selectedDate`." That field is gone. The global slider, `TimeDockSection` and `TimeDatePill` were
deleted the same day per-layer sliders landed, and the map is now a genuinely mixed-time
composite: fire can draw 2026-08-07 beside vegetation drawing 2025-06-14, and nothing forces them
to agree.

**The store, sparse.** `time-slider-store.layerDates` is `Record<string, string>`, keyed by
`LayerToggleId`, and it is deliberately sparse: a layer with no entry is not "on today", it is
"following its own record". `resolveLayerDate(layerDates, capabilities, layerId)` is the one
function that resolves a layer's day, in order: the layer's own override if `setLayerDate` has
ever been called for it; else that layer's own `latestObservedDate` from the capabilities
payload, because under one shared "today" every layer but vegetation used to render an empty,
confusingly-correct hole; else the server's `serverCurrentDate`, for a layer whose newest day
nobody knows (no warehouse stream at all, or a stream that has published nothing) — the same day
such a layer already drew before per-layer dates existed; else `UNINITIALIZED_DATE` before
capabilities have arrived at all, which is not a calendar date, so every consumer reports no day
rather than guessing one. Every reader of a layer's day — `useLayerDay`, `useDebouncedLayerDay`,
`useMetricAtDate`, `LayerTimeSlider` — calls this one function; none of them re-implement the
fallback order.

**One settle timer per layer, not one per pointer tick and not one for the whole map.**
`useSettledLayerDate(layerId)` (`src/lib/map/layer-toggle-context.ts`) subscribes to the store
imperatively and resolves THIS layer's day on every notification, returning immediately when it
did not move. A scrub on one row therefore arms one timer, on the same `SCRUB_SETTLE_MS` boundary
`useMetricAtDate` debounces to (shared, not restated, so a layer's map feed and its own details
region can never issue two waves of requests for the same day) — and every other mounted layer's
timer never fires at all. `useDebouncedLayerDay(layerId)` wraps that settled day into what a
request needs: `requestDate: undefined` at the server's today, exactly as the single global
slider used to report, because the server treats an omitted day and today identically and
sending it explicitly would mint a second react-query entry for the same answer. See
`src/lib/server/AGENTS.md` §slider-day for why that omission is load-bearing.

The one exception is `useSettledEveryLayerDateKey`, which exists only for `useViewedLayerDays` —
the single place every layer's day is read at once, for the agent payload and for the mixed-time
report below — and it settles one joined key on the same boundary rather than minting a timer per
layer, because that consumer re-renders on any layer's change regardless.

**The day must never enter a `style.load` handler's dependency array, for any layer.** See "Each
layer's day reaches its own queries, not a style.load handler" above.

**The mixed-time risk, and how the UI guards it.** A screenshot with every layer on its own
newest day looks like one moment even though it may span years, and the guard against that is in
three places, not one:

- **`LayerTimeSlider` always renders the date**, even for a layer with no scrubbable axis at all
  (a snapshot, an unpublished stream, one with no warehouse layer behind it) — see
  `describeMissingAxis`. Hiding the date for those layers would be the same mislabelling this
  whole feature exists to prevent; a layer is drawing as of some day whether or not a reader can
  move it.
- **The "behind its own latest" mark is a WORD, not a colour**, so it survives greyscale and a
  screen reader. `MapDay.isBehindLatestObservedDate` is a positive claim and stays false unless
  the layer's own `latestObservedDate` is known AND the selected day is provably before it —
  never true for a layer whose newest day nobody has measured, because there is nothing measured
  for it to be behind.
- **`useViewedLayerDays` hands the agent and any cross-layer report every visible layer's own
  settled day**, never a single "the map's day". A layer with no nameable day is omitted rather
  than reported with a sentinel — "uninitialized" is not a day anyone is looking at — and each
  entry's `isOnLatest` carries the same not-provably-behind rule `isBehindLatestObservedDate`
  does, so the two can never disagree about the same layer.
- **A surface that states a day to the reader states the day DRAWN, not the day requested.**
  Those two are the same only while a layer blanks during a load, which stopped being true on
  2026-08-16 — see "A layer must not blank between days" above. `useViewedLayerDays` answers the
  first question (what each row is asking for) and is the right input for the agent; a caption
  must resolve it through the drawn-day registry first. **The registry only knows about layers
  whose reader publishes to it** — currently `LayerManager`'s nine and the nine NASA POWER
  signals. For the other nine toggles it answers with the row's day, which is correct only
  because those layers still blank rather than retain.

**What survives from the single-slider design, unchanged.** `VegetationPanel` used to own a Year
slider and a Month slider backed by `vegetation-store`'s `year`/`month`, so the app had two
clocks that could disagree even before per-layer dates existed. Those fields and both sliders
were removed on 2026-08-05; `useVegetationDisplayMode` projects vegetation's OWN settled day (via
`useDebouncedLayerDay("vegetation")`, never another layer's) onto the GIBS composite month, and
`vegetation-store` holds display state only (`mode`, `ndviMode`, `showNDWI`, `opacity`). Two
details still make that projection safe to copy for the next coarse-grained layer:

- **Read the settled day, and memoize on the derived grain, not the day.**
  `useVegetationDisplayMode` returns an object memoized on `(year, month)`. Scrubbing thirty days
  inside one month therefore leaves every prop `VegetationLayer` keys its `setTiles` effect on
  referentially unchanged. Memoizing on the day would re-request a month-granular tile once per
  day scrubbed.
- **A day the upstream does not cover is stated, not drawn blank.** `resolveGibsNdviDate`
  refuses a period outside the product's published extent rather than emitting a URL that 404s,
  so `compositeUnavailableReason` names the gap and `VegetationPanel` renders it on the page.
  Coverage is judged against `serverCurrentDate`, never `new Date()`: across New Year the two
  disagree by a whole year. Before capabilities land there is no day, so `year`/`month` are
  `null` and no raster is attached — a browser-clock default would draw a period nobody chose.

**Dropped, not carried forward: per-resource axis focus.** The single global slider used to carry
`time-slider-store.focusedLayerName`, narrowing the track to one `geo.layers` publication's own
axis (`earliestObservedDate` … `serverCurrentDate + forecastHorizonDays`) while `selectedDate`
still applied to every layer, with a caption stating that explicitly. That field,
`focusedResourceDomain`, `publishesAnyForecast` and the whole focus picker are gone: every row
now shows its OWN axis by construction, so there is nothing left for a picker to focus onto —
narrowing a shared thumb to "look at fire's range" made sense when one control had to serve every
layer, and stops meaning anything once fire already has its own thumb. Do not reintroduce a
resource picker; give the layer its own row instead, as every layer already has one.

## One capabilities fetch; the controls are per-layer rows, not a dock section

**Superseded 2026-08-09.** This section used to be titled "The scrubber is a dock section; the
pill is a marker, not a disclosure" and described three components sharing one shared day:
`TimeSliderCapabilitiesLoader` (the fetch), `TimeDockSection` (one scrubber card in the manager,
titled "Map date"), and `TimeDatePill` (a top-bar marker over the canvas, whose click called
`focusDockSection("time")`). The latter two are deleted along with the store field they
controlled. `DockSectionId` lost `"time"` the same day — it was a member for exactly one day,
from 2026-08-08 until per-layer sliders landed on 2026-08-09 — and `focusDockSection("time")` is
now unreachable code that must not be reintroduced: there is no longer a single date to focus.

**`TimeSliderCapabilitiesLoader` is the one part that did NOT change shape.** Still headless,
still renders `null`, still mounted in `MapView` and never unmounted, still the ONE read of
`environmental.getSliderCapabilities` and the one writer of `setCapabilities` /
`setCapabilitiesUnavailable`, with the same 5-minute `staleTime`/`refetchInterval` pair (a
UTC-midnight rollover is a date change no user action coincides with, and the poll is a
server-side cache hit) and the same "only never-succeeded-and-errored counts as unavailable"
rule. What changed is what the payload feeds: ONE fetch still supplies every layer, because
per-layer dates split the DAY, not the capabilities — a fetch per row's slider would be one
whole-warehouse scan per visible layer. It cannot live inside a collapsible dock section: every
layer's day, and so every warehouse-backed query on the map, depends on it through
`useDebouncedLayerDay(<toggle>)`, and a closed section is an unmounted section.

**The controls are `LayerTimeSlider`, one per row, gated on that row's own axis.** `LayerRow`
renders one beside its opacity slider whenever the layer is switched on AND `hasOwnTimeAxis` —
`sliderDomain(capabilities, warehouseLayerName) !== null` — so a snapshot or a layer with no
warehouse feed behind it gets no track rather than a dead one, and a layer with a track always
has a control (`hasSelectableDay` in `src/stores/time-slider-store.ts` is the one function both
a layer's map read and its row must agree with — see "The layer registry and the toggle context"
above). Stacked under the opacity slider rather than beside it: the dock column is 19rem, and
splitting it would leave each track under 7rem, too narrow to address a day on a multi-year axis.
`LayerTimeSlider` draws that row's own coverage track (gaps, governed absences and thin ranges
from that layer's own capability), its own "behind its own latest" mark in words, and — for a layer with no axis at all
— still prints the bare selected date, because a mixed-time map is only readable while every row
admits its own day whether or not that day can be moved.

A governed absence is its own coverage state, not a gap and never dense. It means the source was
checked and deliberately published no observation for that date; an ordinary gap means a day was
owed but not published. Both dates remain selectable because inspecting the exact refusal is part
of the time control's job, but the track uses a separate crosshatch and spoken label for the
governed case. The Parquet census already distinguishes `gap_ranges` from
`governed_absence_ranges`; dropping the latter at the tRPC boundary used to paint those dates as
ordinary dense observations and then surprise the user with an empty map after scrubbing.

**Nothing marks the map's overall state any more, because there is no longer one state to mark.**
The old pill's "Past day" / "Beyond record" claim applied to the single shared date; under
per-layer dates that claim would have to be made once per layer, which is exactly what each row's
own mark already does. A reader who wants the whole picture reads `useViewedLayerDays`, which is
what the agent and any future cross-layer summary consume — see "Every layer draws its own day"
above.

## Picking a point to query

`SoilDetails` (then `SoilPanel`) has accepted a `queryPoint` prop since it was written, and
nothing ever passed one: `PanelManager` mounted the panel without it and no `map.on("click")`
handler anywhere in `src/` produced one. The point query — SoilGrids properties and
intervention suitability at a place — was unreachable from the UI.

The wiring is deliberately split three ways rather than put in one component:

- **`map-store` holds the point** (`queryPoint`, `isCapturingQueryPoint`), beside
  `selectedFeatureId`. It is map interaction state, and a store is what lets the click
  handler, the section and the pin layer see it without prop-drilling through `MapView`.
- **The dock's Soil section arms capture**, through `useMapQueryPoint(map, true)` in
  `DockDetails.tsx`'s `SoilDetailsBody` — mounted only while that section is expanded, so the
  hook's `active` argument is a constant and the *mounting* is the arming. (`PanelManager`
  passed `isSoilPanelOpen` for the same reason before the merge.) The section that has a point
  query to answer is the one that turns clicks into points; capture is not always on, because
  a click on the map means different things depending on what is open.
- **`LayerManager` draws the pin**, via `QueryPointLayer`. The map owns its layers, and a
  GeoJSON source rather than a `maplibregl.Marker` so the pin re-attaches on `style.load`
  and survives a basemap swap like every other layer here.

**One click, one meaning.** `MapView`'s own click handler opens the agent popup on empty
ground. Both handlers would otherwise fire on the same click and the popup would cover the
pin, so `MapView` reads `isCapturingQueryPoint` from the store and stands down. It reads
the store imperatively rather than taking a prop, because that handler is registered once
for the life of the map and must not be re-registered as panels open and close.
Right-click still reaches the agent popup, so nothing becomes unreachable.

**Three ways to cancel.** Clicking the pin again, pressing Escape, and unmounting the section —
collapsing Soil, or closing the dock (`setCapturingQueryPoint(false)` clears the point as well
as disarming). Clicking anywhere else *moves* the pin — that is a second question about a
second place, not a cancellation. `SoilDetails` also renders an explicit "Clear queried point"
button, because a pin the user cannot obviously get rid of is worse than no pin.

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
and `HeatmapLayer` are not available without adding one. Adding it would not help
anyway: those layers aggregate *on the client*, which is the thing the repo rule forbids
and the thing `geo.soil_moisture_field` exists to avoid. By the time geometry reaches the
browser it is at most nine polygons, which a MapLibre `fill` — already WebGL — draws for
free. No custom shader was needed and none was written.

The depth selector in `SoilPanel` is a **depth**, not a second clock: the day always comes
from the global time slider, as "One time control, projected per layer" above requires.

## §climate-field

**Rewritten 2026-08-10.** This section described one `climate-field` toggle drawing one of nine
signals at a time, chosen by a picker in the dock. That toggle is gone. Each NASA POWER signal
now owns a registry row, a switch, an opacity, a time slider and a render form, and
`ClimateFieldLayers` mounts one `ClimateFieldLayer` per signal.

**Why nine rows.** The old note argued for one toggle on the grounds that the nine signals are
"nine answers to *what was the weather*, only one of which can be painted over a cell at a
time." That is true of a **filled** field and is now handled by `renderForms`, not by hiding
eight signals. What the argument missed is that one toggle carries one `warehouseLayerName`,
therefore one capability, therefore **one axis** — and that axis was computed in
`getSliderCapabilities` over all eleven of the lane's `signal_name`s unioned together. The
four-cell soil-wetness pilot was published with the 397-cell air-temperature field's earliest
day, latest day and gap list. Every day its slider offered was a day *some* signal had
published, which is not a claim anybody was making. Nine rows is what makes each axis describe
its own signal.

**Render forms are what make nine rows composable.** Nine fills over the same 397 cells is one
visible field and eight buried under it, with the group header counting "3 of 9" while the map
shows one. So a row picks a form as well as a day — `field` (one square per measured cell),
`isoline` (dissolved isobands drawn as **boundaries only**, no fill), `symbol` (one point per
cell, sized and coloured by value). A wash, contours across it and points above them compose
the way a printed weather chart does. Only air temperature and the three soil-wetness pilots
default to `field`; the rest default to contours or points so a reader switching several on
gets a readable composite without touching a control.

**Two forms are withheld, and the withholding is the point.** `isoline` is absent from
`precipitation` — a contour asserts the field varies smoothly between the samples it passes
through, and daily rainfall does not; one 55 km square is wet and its neighbour dry. It is
absent from all three soil-wetness signals for the harder version of the same problem: they are
a pilot on part of the lattice, so contouring them interpolates a continuous field across ground
the lane never measured. `resolveClimateRenderForm` enforces this on **both** sides — the client
before it asks and the server before it answers — so a stale store entry or a hand-made request
degrades to the signal's default instead of drawing the one form that would lie.

**Geometry comes from the server, per form.** `getPublishedClimateField` emits cell polygons,
cell centroids or dissolved isobands off a single read. The contouring runs server-side, in
`climateFieldIsolineFeatures`, for the reason `src/lib/geo/AGENTS.md` §isobands gives for the
soil field — `ST_Contour` needs `postgis_raster`, which is not installed, and contouring in the
browser is the client-side aggregation the repo rule forbids. Unlike the soil field it needs no
SQL aggregation function: the NASA POWER cells already **are** a regular 0.5° lattice, so their
centroids feed `buildIsobands` directly. Isoband features carry `aggregated: true`; cell
features carry `false`.

**Ids are derived from the signal, never module constants.** Two rows can be on at once showing
two different days, so `climate-field` as a fixed `SOURCE_ID` would have them overwrite each
other. `layerIdsFor(signal)` owns every id one instance uses.

**A form change rebuilds; it does not repaint.** The three forms are different MapLibre layer
types over different geometry, so `renderForm` is a dependency of the mount effect rather than
something `setPaintProperty` can apply. `removeLayers` tears down **every** form's ids, not just
the current one — otherwise a switch from filled to contours leaves the old wash under the new
lines.

**The outline opacity is a scalar, not a `case`.** Every feature the `field` form serves is an
unaggregated cell, so there is no isoband contour to suppress — the `["case", …, 0, 0.25]`
expression in the two soil-field outlines has nothing to guard here, and this layer is
therefore *not* one of the three data-driven-opacity layers listed under "Opacity is a
multiplier" above. It still goes through `scaleOpacityValue`, so the multiplier rule has one
implementation rather than two. Contours and points are drawn nearer full strength than a wash:
both are thin marks that must stay legible over whatever is beneath them.

**Wind has no direction, and no barb may be invented for it.** The NASA POWER backfill requests
eight parameters — `ALLSKY_SFC_SW_DWN, PRECTOTCORR, RH2M, T2M, T2MDEW, T2M_MAX, T2M_MIN, WS2M`
— and `WD2M` is not among them, so the warehouse holds a daily mean *speed* with no bearing
anywhere. Wind speed is a scalar here and is drawn as one.

**`ClimateDetails` is a report, not a picker.** It renders one section per switched-on signal,
each with that signal's form control, its coverage note and its band table. The form control is
a **form**, and the air-temperature statistic is a **statistic** — neither is a second clock,
for the same reason the soil depth selector is not one.

**The pilot coverage note carries no numbers.** It read "Pilot coverage: 4 of the lane's 397
cells carry this signal" until 2026-08-10 — a figure measured against production on 2026-08-08
and frozen into the client bundle. The pilot then grew, and the panel rendered that sentence
directly above the live "267 measured 0.5° cells drawn for 2026-08-06" in the same card. The
constant now states the *kind* of coverage; the counted sentence is composed from `cellCount`
and `latticeCellCount`, both measured on the request that drew the cells. A denominator that is
not measured beside its numerator will always eventually lie.

## §weather

**One toggle, two style layers, one source.** `WeatherLayer` paints `weather-temperature`
(circles, coloured on the observation's `temperature`) and `weather-wind` (a `text-field`
symbol: an arrow glyph plus the measured speed) from a single GeoJSON source. The circles are
added first so the arrows draw over them. Until 2026-08-08 only the arrows existed, so a
toggle labelled "Wind & Weather" drew wind and nothing else while `temperature` and `humidity`
were already on every feature and simply never painted.

**Completeness is judged per drawn layer, not per observation.** Each layer filters on its own
`hasWind` / `hasTemperature` flag, computed once when the collection is built. That is what
keeps a null out of `windSpeedToColor` and out of the temperature `interpolate` without
coalescing it to a number the upstream never reported — the flag is the guard, not a
fallback value. `LayerManager` correspondingly admits an observation that measures wind *or*
temperature; it used to require windSpeed **and** windDirection **and** temperature **and**
humidity, which dropped stations that had everything the map draws for the sake of a field
nothing draws. `getPublishedWeatherForBbox` still applies the stricter all-four rule
server-side, so today the client rule only ever widens what an already-complete feed can
draw — it is what lets a relaxation there reach the map without a second edit here.

**The hoverable layer is the circle, not the arrow.** A `text-field` symbol's hit area is the
glyph run, and `text-allow-overlap: false` collides arrows away exactly where stations are
dense, so hovering the wind layer would be unreliable precisely where there is most to read.
`weather-temperature` is in `HOVERABLE_LAYER_IDS`; `weather-wind` is not.

**Units are the ones measured**, and the legend says so: m/s (`weather.ts` sends
`wind_speed_unit=ms`) and °C (Open-Meteo's `temperature_2m` defaults to Celsius, and nothing
converts it in between). The ramp is Moreland cool-warm — its arms separate on the blue/red
axis, which protanopia and deuteranopia both preserve, and its lightness peaks at the neutral
middle, so the ordering survives greyscale too.

## §fire-detections

**The fire layer draws aggregation CELLS, not detections.** Since the 2026-09-01 Parquet
cutover, `LayerManager` and `FireDetails` read `wildfire.getFireDetections` through
`useParquetFireDetections` (day + viewport bbox + viewport zoom) instead of `useFireData` →
`/api/fires`. Each feature is one warehouse cell at the rung the zoom resolves to, carrying
`detectionCount`, `frpSum`, `frpObservationCount`, `highConfidenceDetectionCount`,
`observedDay`, `newestObservedAt` and the `zoomTier` it was aggregated at. Nothing else. A
buffered polygon is deliberately **not** drawn: a cell is where detections were counted, not
where anything burned, and a square would assert an extent nothing measured.

**Four properties left the vocabulary and must not come back.** `PercentContained`,
`IncidentSize`, `brightness` and `confidence` were per-incident/per-detection fields that only
`/api/fires` ever produced. Every expression reading them would now paint from its `coalesce`
fallback while looking like it was reading data — the failure mode where a map is uniformly
wrong and confidently so. The containment ramp went with them, and `layer-legends.ts` legends
the FRP ramp instead.

**Three channels, three fields, no double-encoding.** Colour is `frpSum` (megawatts) with a
distinct off-ramp colour for a cell whose `frpObservationCount` is 0 — no reported power is
not zero power, and an `interpolate` over a null would have said otherwise. Dot size is
`detectionCount`. Ring colour is whether the cell holds any high-confidence detection.

**What the route could not do is why the read moved.** `/api/fires` was always dated (it
accepts `?date=` and always has); what it could not do was scope to a viewport, select a
serving rung, or distinguish a day that was never written from a day with no fires. The
procedure's four terminal states carry that distinction to the surface, and
`FireDetails` renders each one rather than a count of zero.

## §soil-survey render shapes

**The survey answers one viewport with one of three shapes, and `SoilSurveyLayer` adds a
style layer for each**: real SSURGO delineations and unioned drainage-class polygons share the
`soil-survey-fill`/`soil-survey-outline` pair, and a viewport too wide to union honestly
arrives as a lattice of counted points drawn by `soil-survey-summary` (circles). One source
carries all three; the circle layer's `["==", ["get","summary"], true]` filter is what keeps
them apart, so nothing on the client branches on which tier answered. The fill and line layers
need no matching filter because MapLibre draws neither on a `Point`.

**All three colour on `drainageClass` off the one `SOIL_SURVEY_DRAINAGE_CLASSES` table**, so
zooming changes the shape a reader sees and never what a colour means — which is why the
legend carries one class list plus a `note` about the dots, rather than a second class list
per tier. Dot *size* is the delineation count; size is not a swatch, so it is legended as
prose.

Why a lattice at all, rather than more polygons: the union path is bounded at 20,000 input
rows, and past that its `LIMIT` decides which delineations get merged — so a wider viewport
would draw a boundary assembled from an arbitrary subset, which is a shape nobody surveyed.
The service picks on measured viewport *area* (`MAX_SOIL_UNION_SQUARE_DEGREES`, derived from
the measured ~41,500 delineations per square degree) rather than on the zoom tier, because the
ceiling that forces the choice is a row budget and rows scale with area. See
`src/lib/server/services/usda-soil.ts`.

## Layer completeness is a full-stack contract

See `docs/layer-lane-standard.md` sections 9-11. The trap this directory has already hit: the slider
capability catalogue is the OUTER relation of a LEFT JOIN, so a stream the observation query emits but the
catalogue omits is dropped silently -- tiles paint, no slider mounts, and it looks like missing data.
Assert the catalogue's bound parameters against a hand-spelled list, never against the shared constant.

## §non-lane-surfaces

**Added 2026-08-25, lane C `u4` of the Parquet cutover.** Four toggles are not Parquet lanes and
cannot be made into them by that programme: `interventions` (a community feature that stays in
Postgres by design), `strategy-recommendations` (needs an ML label plane that has no labels),
`soil` (a raster with no first-party release) and `demand-heatmap` (derived at request time, with
nothing stored per day). `src/lib/map/layer-publication-standing.ts` gives the first three a
stated reason; `soil` already carried a `permanentlyUnavailableReason`, which is the fourth.

**A standing is not a `permanentlyUnavailableReason`, and the difference is the whole design.**
That field is a *governance gate*: it disables the switch, reads false in `useLayerVisibility`
whatever `activeLayers` says, and drops the row out of `DockSections`' "n of m active" count.
`layer-registry.test.ts` pins `withheld === ['soil']`, deliberately — the other three have live
renderers that would paint the instant their upstream produced a row, so withholding them would
be a lie in the opposite direction from the blank map. A standing says only *this is empty and
here is why*, and leaves the switch alone.

**A standing REPLACES the derived availability caption, it does not stack with it.**
`unavailableReason` comes from `layerAvailabilityAt`, which reads an absent-or-all-null capability
as `not_yet_observed` and captions it "has no observations this far back" — a claim about
HISTORY. For `interventions` that is simply false: the recommendations exist and are approved, and
the publish step that would put them on a tile is invoked by nothing. Two captions with one of
them wrong is worse than the silence it replaced, so `LayerRow` shows the standing instead.

**No standing may state a count or a date.** "Two recommendations exist, both approved" was true
when it was measured and is wrong the next time one lands, with nothing anywhere to reopen it —
the same stale-claim class as a hard-coded row count in a legend.
`layer-publication-standing.test.ts` fails on any digit in either sentence.

The standings are stated whether the layer is switched on or off, unlike `unavailableReason`,
which is gated on `isActive`. That gate is right for a caption about the selected DAY; a reader
who has to flip the switch to learn the layer draws nothing has already seen the empty map.

## §synced-days-track

**Added 2026-08-16.** Each `LayerTimeSlider` now draws a SECOND row above its coverage track:
which of that layer's days this BROWSER holds a fresh, persisted copy of, coalesced into runs the
same way the coverage track already is. Syncing is a byproduct of ordinary scrubbing -- browse a
day, it is fetched, it is persisted, that day lights up -- and there is deliberately no bulk-sync
affordance anywhere in this row.

**A sibling row, never a fifth `CoverageKind`.** The coverage track answers "what has the SERVER
published"; the synced-days row answers "what has THIS BROWSER saved". Those are different
claims about different systems, and blending "synced" into the same band run the way `thin` or
`undescribed` are blended would let one band assert both at once, with no way for a reader to
tell which claim it was making. The two rows share `percentOfDayOffset` and the same `domain`,
so a synced run registers pixel-exactly above the coverage band for the same days, but they are
drawn, coloured and captioned as two separate facts. `drawSyncedDayBands` sweeps the WHOLE drawn
axis (`sliderMaxOffset(domain) + 1` days), not just through today the way `buildCoverageSegments`
does -- a forecast variant can be fetched, and therefore synced, past the live edge, and the stop
at today is specific to what `coverageGaps` can describe, not a bound this question inherits.

**The run-coalescing is factored, not duplicated.** `layer-coverage-track.ts`'s
`floorAxisRunsToBands<TKind>` is the one place that floors a run of days to a visible percentage
width and coalesces two of the same kind when that flooring makes them touch -- `drawCoverageBands`
calls it with the four coverage kinds pre-filtered to non-dense, `drawSyncedDayBands` calls it
with the single `"synced"` kind. Both existed to solve the identical problem: a four-year axis is
~1,460 days, and a div per day, per track, per switched-on layer would be tens of thousands of
elements on a panel with two dozen active layers. `AxisRun<TKind>` is the shared input shape;
`buildCoverageSegments` and `buildSyncedDayRuns` each build one, and only the SEEDING differs
(coverage seeds from `coverageGaps`/`thinRanges`/`describedFromDay`; synced-days seeds from a
`ReadonlySet<string>` membership test) -- the flooring, the coalescing and the element-count bound
they produce are one implementation, not two that could quietly drift apart.

**Three states on the row, told apart by more than colour -- and, since an adversarial review of
this section, by more than a texture already spoken for.** `useSyncIndexReady() === false` (the
index has not hydrated once) draws the SAME `undescribed` hatch treatment `TRACK_REGION_APPEARANCE`
already uses for "the report does not reach this far" on the coverage track -- reused deliberately
rather than inventing a new texture, since it is the same underlying claim: not a measurement, an
absence of one. Hydrated-and-empty draws a plain baseline with no fill at all, and the two must
never look alike: a not-yet-known index reading as an empty track would tell a reader nothing is
cached when the truth is simply not known yet.

Hydrated-and-holding draws **dotted** runs, `SYNCED_DAY_APPEARANCE` (`layer-coverage-track.ts`,
kept OUT of the `TRACK_REGION_APPEARANCE` table on purpose -- folding a sixth "synced" region in
there would be the exact blending this whole feature exists to refuse). The first version of this
row reused `TRACK_REGION_APPEARANCE.dense.backgroundColor` outright -- solid `--primary`, the same
fill the coverage track's own dense base already paints a few pixels below it -- reasoning that
both meant "there is a complete answer here" and could share the token. Review caught what that
argument missed: with only a ~4px row gap and a 2px height delta actually separating the two rows,
"the same fill" reads as ONE bar with a fainter echo, not two independent claims, to anyone not
stopping to read a tooltip. Coverage's own four regions (`dense`/`thin`/`absent`/`future`/
`undescribed`) already claim every right-angle hatch direction the track uses (0/45/90/135), so
"synced" takes a THIRD visual vocabulary -- dots -- rather than a fifth angle, which keeps the two
tracks legible as two systems even in a screenshot with no caption in view.

A tick at the selected day's offset, styled like the coverage track's own today-tick, sits over a
filled run or over open space depending on whether the day on screen is held -- a second,
colour-independent signal alongside the caption. The caption (`layer-time-slider-note-${layerId}`,
the same paragraph the coverage/staleness notes already share) states the count and whether the
selected day is one of them in words, and it is **never silent once the index is ready** -- not
even when nothing is held. The first version of this caption stayed silent on "ready, hydrated,
holding nothing", reasoning by analogy to `describeDayCoverage`'s null-on-dense rule. Review named
the disanalogy: a dense day carries no visual mark of its own to be silent ABOUT -- the absence of
a hatch literally IS what "dense" looks like, so silence and the render agree. This row's
hydrated-and-empty state has its OWN distinct render (the plain baseline, as told apart from the
hatch and the dots above), so a sighted reader gets three tellable states while an aural reader
who only hears two of them is missing real information, not redundant confirmation of something
already visible. The caption now says "Nothing saved on this device yet." for that state, closing
the gap.

**The reset control lives in `LayerRow`'s controls stack, not the slider's top row.** Two
legitimate homes were considered: beside the slider's own "Latest" button (`LayerTimeSlider`'s
top row), or as a sibling of the time-slider slot in `LayerRow`. The slider's top row was
rejected specifically because of what already lives there -- "Latest" is one click, fully
reversible, and sits inches from a typed date field. A destructive control that close to it is
the single most likely misclick in this design: fat-finger "Latest" expecting "Clear" or vice
versa, and one of those two mistakes destroys locally-fetched data with no undo. `LayerRow`'s
stack physically separates the two by an entire row -- the sync-reset control is never adjacent
to "Latest" at all -- and on top of that separation it is armed by an explicit two-step confirm
(`LayerSyncResetControl`'s idle button only opens a confirm block; a second, distinctly-labelled
button inside that block is what actually calls `clearLayerSyncedDays`), with focus moving to the
safe Cancel action by default and Escape wired as a second way out, mirroring the "Three ways to
cancel" precedent the soil query-point pin already set (see "Picking a point to query" above). It
is disabled, not unmounted, when a layer holds nothing -- so a reader can tell "nothing to clear"
from "this layer has no timeline at all" (the latter unmounts the whole control, gated on the
same `mountsTimeSlider` boolean the slider itself uses, since a layer with no per-day axis has no
per-day cache entries either).

**Focus moves in ONE effect, for both directions, never inline in a click handler.** The confirm
block and the idle trigger are not two states of one element -- the root swaps between a
`<div role="group">` and a `<button>` -- so opening OR closing it unmounts one ref-bearing element
in the same commit that starts the transition. `handleCancel` and a successful `handleConfirm`
first called `triggerButtonRef.current?.focus()` inline, in the same synchronous tick as the
`setIsConfirming(false)` that schedules the unmount; by that point `triggerButtonRef.current` was
already `null` (nulled when the idle button unmounted back when the dialog OPENED), so the call
was a silent no-op and focus fell to `<body>` on every close -- caught by
`LayerRow.test.tsx`'s own "returns focus to the trigger on Cancel" case, which is exactly what a
real reviewer should be able to catch a UI lane on. The fix is one `useEffect` keyed on
`isConfirming`, mirroring the opening transition's own already-correct pattern: it focuses Cancel
when the dialog opens and the trigger when it closes, in either case reading the ref only after
React has committed the render that actually attaches it. A leading-run guard (a plain
`useRef(false)` flag) keeps this from stealing focus on the row's own initial mount, where
`isConfirming` is `false` for a reason that has nothing to do with a transition.

**A successful `clearLayerSyncedDays` call is not the same claim as a successful clear.** The
contract documents the function as never rejecting: a write that silently no-ops under quota
pressure or a version-change lock resolves exactly like one that actually deleted something, so a
`try`/`catch` around the awaited call is live code for a genuine throw (kept, as defence in depth)
but dead code for the realistic failure mode, and the dialog would have closed and reported
success on a clear that changed nothing. The real signal is observable, just not from the
`Promise<void>` itself: `LayerSyncResetControl` records `syncedDays.size` at the moment Confirm is
clicked, and once `isClearing` drops back to `false`, an effect compares that recorded count
against this hook's LATEST report. A count that dropped is treated as success and closes the
dialog (which also re-triggers the focus-return effect above); a count that did not drop sets
`clearFailed` and leaves the dialog open with "Could not clear -- nothing here changed. Try
again." so the user is never told something happened that did not.

**The confirm copy scopes the action honestly -- and hedges the one figure it cannot state
exactly.** `clearLayerSyncedDays(layerId)` empties one layer's entries from the IndexedDB
query-persister cache (`src/lib/cache/`) -- nothing else. It does not touch the tile `CacheStorage`
(`plantgeo-v2`), the outbound mutation queue (`plantgeo-offline`, `src/lib/offline/sync-queue.ts`),
or either persisted localStorage store. The confirm text says so in plain language ("Downloaded
map tiles and anything not yet synced to the server are not affected") rather than naming those
internals, but the constraint is the same either way: nothing here may read as "clear everything".
Separately, `useLayerSyncedBytes`'s own contract documents its figure as an approximate size, not
a measured one -- every rendering of it (`formatSyncedBytes`, the idle button's `aria-label`/
`title`, and the confirm paragraph) is worded "about N MB" rather than a bare parenthetical, so a
destructive control never states a number with more confidence than its source actually has.

**The pending signal is a prop, supplied at the join.** `LayerTimeSlider` has no tRPC of its own
by design (see its own top doc), so it cannot tell whether ITS layer's map data is still in
flight -- that lives outside this directory's own lane. `isFetchingCurrentDay?: boolean` is the
prop it reads instead; `LayerRow` threads an identically-shaped `isFetchingSelectedDay?: boolean`
straight through to it, since `LayerRow` mounts the slider but queries nothing itself. Both
default to `false`/`undefined`, so every existing call site keeps rendering with no pending state
until something wires a real value in.

**The exact wiring, discovered mid-build rather than guessed.** The concurrent map lane's
2026-08-16 fix ("A layer must not blank between days" above) landed
`useDrawnLayerDayStore`/`usePublishedDrawnLayerDays` in `src/stores/useMetricAtDate.ts` while this
section was being written, and it already tracks per-layer `isLoading` -- react-query's
`isFetching` on whatever feed(s) `LayerManager` reads for that toggle. `useDrawnLayerDayStore((state)
=> state.drawnDays[layerId]?.isLoading ?? false)` is therefore the recommended source, and it
already resolves the multi-metric case this section originally worried about (a climate signal or
`weather`'s wind+temperature pair): each such toggle is its own `LayerToggleId` with its own
registry row, so `drawnDays` is keyed correctly with no OR-ing left for a caller to do. Neither
`LayerTimeSlider` nor `LayerRow` imports that store directly -- the boundary between this lane and
the map lane held throughout, and this paragraph exists so the next reader does not have to
rediscover the join by hand.

**Not "the previous DAY".** The same in-flight signal also covers a pan over unchanged ground (a
new bbox for the same day), where the retained frame is an older VIEWPORT's answer, not an older
day's -- so the caption note this prop adds says "what's shown may be a moment behind" rather than
naming a day, and stays true of both causes. When true, the range thumb also pulses (CSS-only,
`prefers-reduced-motion`-aware) and the track carries `aria-busy`, but the caption is the
load-bearing channel: it is added ahead of every other note in the row, ordered first because it
is the most transient of the four and the one fact a reader needs before trusting anything else
the row is currently showing. No second debounce is introduced here; the shared `SCRUB_SETTLE_MS`
boundary in `useMetricAtDate.ts` is what the upstream signal is already timed to.

**The pending pulse animates `filter`, never `box-shadow` -- the thumb's OWN focus ring already
lives there.** The first version animated `box-shadow` directly, reasoning that `.is-pending`'s
declaration would simply lose to `:focus-visible`'s on specificity if both ever applied at once.
That reasoning does not hold: per the CSS Cascade, a RUNNING animation's value for a property sits
in the "animations" origin, which outranks every normal-importance author declaration on that same
property regardless of selector specificity -- so while `.is-pending` was animating, the focus
ring's `box-shadow` could never render, full stop, not merely lose a specificity contest it could
have won some other way. Realistic trigger: a pan puts several visible layers pending at once, and
a keyboard user tabbing among their range inputs would find every one pulsing identically with no
way to tell which has focus. The fix animates `filter: drop-shadow(...)` instead -- a property the
focus ring never touches -- so the two compose as two independent effects on the same thumb rather
than one silently overwriting the other.

**The sync-index-store contract is pinned, not owned here.** `useSyncedDays`, `useSyncIndexReady`,
`useLayerSyncedBytes` and `clearLayerSyncedDays` are imported from `@/stores/sync-index-store`,
built and owned by a different lane. This directory only consumes that exact surface -- do not
widen it, do not stub the file into existence at that path, and do not assume anything about its
internals beyond the four signatures: `useSyncedDays` returns a `ReadonlySet<string>` of
`YYYY-MM-DD` fresh entries, documented referentially stable when unchanged, which is what lets
both `LayerTimeSlider` and `LayerRow` memoize on it directly rather than on a derived size or key.

**A carve-out the map lane's "day DRAWN, not day requested" rule needs.** "A layer must not blank
between days" above states, correctly, that a surface captioning what the map is painting must
resolve through the drawn-day registry rather than a row's raw slider position. `LayerTimeSlider`'s
own date field, its `aria-valuetext`, and the tick positions on both of its rows do NOT do that --
they continue to track the REQUESTED day (the slider's own position) directly, and must keep doing
so. That is not an oversight; it is the boundary the rule was never meant to cross. The map-lane
rule is about READ-ONLY CAPTIONS describing a canvas that may lag behind the control that drives
it; it says nothing about the control itself, which has no "drawn" state of its own to lag --
during an active drag, the requested day IS the only day the slider has, and resolving it through
a registry keyed on what has already landed would make the thumb visibly stutter behind the
reader's own pointer. A future reader applying the rule here by name alone, without this line,
could "fix" the one part of the row that was never broken.
