# Map interaction boundary

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

Two invariants keep it honest, both enforced in `layer-legends.ts`. First, **no colour is written in the legend**: every swatch, class row and ramp stop is imported from the module whose paint expression uses it, and where a ramp was inline in a renderer the renderer now reads an exported constant (`FIRE_CONTAINMENT_COLOR_STOPS`, `DEMAND_DENSITY_COLOR_STOPS`, `BURN_SEVERITY_ACRES_STOPS`, the `StyleClass` tables in `layers.ts`), so the two cannot drift. Second, **a toggle earns a spec only if switching it on paints something**: `soil` has none because `getEnvironmentalTileTemplate` returns `""` and `SoilLayer` adds no source at all; `vegetation` legends NDVI only, because `getNDWITileUrl` returns `""` unconditionally and NBR is unpublished for the same reason. `LEGENDLESS_TOGGLE_REASONS` records each. Legending a colour the map never draws is the failure this module exists to prevent, so an entry that "looks missing" is a claim to check against the renderer, not a gap to fill.

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

`MapDateSummary` replaced the pill, and is a marker rather than a shortcut — it speaks no `focusDockSection` and docks nothing. It mounts unconditionally in `MapView` as a sibling of `LayerPanel`, reading only `useViewedLayerDays()` and `capabilities.serverCurrentDate`, and that independence is the whole point: every other statement of a layer's day now lives on its row, which requires the dock to be docked, the group expanded and the layer switched on. With the dock closed, three layers on three different months would otherwise render as one image carrying no date information at all — a composite anyone would read as a single moment. It states the shared day when the visible layers agree, and when they do not it says so with the span and the layer count. It must never grow controls; the controls are the rows.

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
`LayerTimeSlider` draws that row's own coverage track (gaps and thin ranges from that layer's own
capability), its own "behind its own latest" mark in words, and — for a layer with no axis at all
— still prints the bare selected date, because a mixed-time map is only readable while every row
admits its own day whether or not that day can be moved.

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
