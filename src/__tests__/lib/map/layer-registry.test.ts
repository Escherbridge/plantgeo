import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { URL as NodeURL, fileURLToPath } from 'node:url'
import { describe, it, expect } from 'vitest'
import {
  DOCK_LAYER_GROUPS,
  DOCK_PIVOT_SECTIONS,
  dockReachableLayerToggleIds,
} from '@/components/map/layer-panel/dock-sections'
import {
  LAYER_REGISTRY,
  LAYER_TOGGLE_IDS,
  UNCATEGORISED_LAYER_TOGGLE_IDS,
  isLayerToggleId,
  panelIdForLayerToggle,
  panelIdsOwningLayers,
  styleBackedLayerEntries,
  toggleIdForWarehouseLayerName,
  unreachableLayerToggleIds,
} from '@/lib/map/layer-registry'
import { STYLE_LAYER_TOGGLE_MAP, getLayers } from '@/lib/map/layers'
import {
  CLIMATE_FIELD_SIGNALS,
  CLIMATE_FIELD_SIGNAL_IDS,
  type ClimateFieldSignalId,
} from '@/lib/environmental/climate-field'
import { SLIDER_STREAM_LAYER_NAMES } from '@/types/time-slider'
import { getLayersForPanel } from '@/stores/panel-store'
import { useMapStore } from '@/stores/map-store'

// Explicit node:url URL, not the ambient global: this file runs under vitest's jsdom
// environment, which shadows globalThis.URL with a browser polyfill that mis-resolves
// multi-level ".." against a Windows file:// base (silently falls back to
// http://localhost:3000/<tail> instead of the real path).
const COMPONENTS_DIR = fileURLToPath(new NodeURL('../../../components', import.meta.url))

/*
 * Which layers have a switch is answered by import since 2026-08-08:
 * `dockReachableLayerToggleIds()` below. Before that it took a regex scan for `<LayerToggle>`
 * across every .tsx under src/components, because a layer's only switch lived in one of seven
 * right-hand sheets and a sheet's JSX is not reachable from the registry -- mounting all seven
 * to look for switches would have meant standing up the map, tRPC and every store they read.
 * The dock derives its rows from the registry in a React-free module, so the question is now a
 * pure function call whose answer cannot drift from what renders.
 */

// The registry replaced three hand-written tables that could silently disagree. These
// assertions pin the derived shapes to what the map actually consumed before the merge,
// so a registry edit that drops a style layer or a legend mapping fails loudly here
// instead of quietly rendering a toggle that controls nothing.
describe('layer registry derivations', () => {
  it('derives exactly the style-backed toggles, with their style layer ids', () => {
    expect(STYLE_LAYER_TOGGLE_MAP).toEqual({
      'fire-perimeters': ['fire-perimeters', 'fire-perimeters-outline'],
      sensors: ['sensors'],
      watersheds: ['watersheds-fill', 'watersheds-outline'],
      'evacuation-zones': ['evacuation-zones', 'evacuation-zones-outline'],
      interventions: ['interventions', 'interventions-outline', 'interventions-points'],
      'burn-severity': ['burn-severity', 'burn-severity-outline'],
      'building-footprints': ['building-footprints'],
    })
  })

  it('only style-backed entries carry style layer ids', () => {
    const styleBackedIds = styleBackedLayerEntries().map((entry) => entry.toggleId)
    expect(styleBackedIds).toEqual([
      'fire-perimeters',
      'sensors',
      'watersheds',
      'interventions',
      'evacuation-zones',
      'burn-severity',
      'building-footprints',
    ])
    for (const entry of styleBackedLayerEntries()) {
      expect(entry.renderKind).toBe('style')
    }
  })

  // A style-backed toggle whose ids are not baked flips a layer that does not exist and
  // draws nothing, reporting no error; an id baked twice throws a MapLibre duplicate-layer
  // error at style build. "Exactly once" is the only safe count, and it is what the move of
  // watersheds off the component-added path had to preserve.
  it('bakes every style-backed layer id into the style exactly once', () => {
    const bakedIds = getLayers().map((layer) => layer.id)
    expect(new Set(bakedIds).size, 'a duplicate id in getLayers()').toBe(bakedIds.length)
    for (const entry of styleBackedLayerEntries()) {
      for (const layerId of entry.styleLayerIds) {
        expect(bakedIds, `${entry.toggleId} -> ${layerId}`).toContain(layerId)
      }
    }
  })

  it('translates every geo.layers name that has a renderer, and no others', () => {
    expect(toggleIdForWarehouseLayerName('fire-detections')).toBe('fire')
    expect(toggleIdForWarehouseLayerName('fire-perimeters')).toBe('fire-perimeters')
    expect(toggleIdForWarehouseLayerName('water-gauges')).toBe('water')
    expect(toggleIdForWarehouseLayerName('weather-observations')).toBe('weather')
    expect(toggleIdForWarehouseLayerName('vegetation')).toBe('vegetation')
    expect(toggleIdForWarehouseLayerName('interventions')).toBe('interventions')
    // Both now have renderers: their published rows were previously toggleable by
    // nothing. Row counts stay out of here deliberately -- ingestion moves them.
    expect(toggleIdForWarehouseLayerName('evacuation-zones')).toBe('evacuation-zones')
    expect(toggleIdForWarehouseLayerName('sensors')).toBe('sensors')
    // Claimed only once the render path moved off the tRPC proxy and onto
    // geo.watershed_tiles(): the name is a claim about what the toggle DRAWS.
    expect(toggleIdForWarehouseLayerName('watersheds')).toBe('watersheds')
  })

  it('gives each warehouse-backed toggle a distinct geo.layers name', () => {
    const names = LAYER_TOGGLE_IDS.map(
      (toggleId) => LAYER_REGISTRY[toggleId].warehouseLayerName
    ).filter((name): name is string => name !== null)
    expect(new Set(names).size).toBe(names.length)
  })

  it('inverts panel ownership into the same lists the panels governed before', () => {
    expect(getLayersForPanel('fire')).toEqual([
      'fire',
      'fire-perimeters',
      'evacuation-zones',
      'burn-severity',
    ])
    expect(getLayersForPanel('water')).toEqual([
      'water',
      'drought',
      'weather',
      'sensors',
      'watersheds',
    ])
    expect(getLayersForPanel('vegetation')).toEqual(['vegetation'])
    expect(getLayersForPanel('soil')).toEqual([
      'soil',
      'soil-survey',
      'soil-moisture',
      'soil-temperature',
      'soil-vpd',
    ])
    // Climate owns one layer per NASA POWER signal. It owned exactly one for all nine until
    // 2026-08-10, on the grounds that only one can be painted over a cell at a time -- which
    // is true of a FILLED field and is now handled by `renderForms`, not by hiding eight
    // signals behind a picker with no axis of their own.
    expect(getLayersForPanel('climate')).toEqual(
      CLIMATE_FIELD_SIGNAL_IDS.map((signal) => CLIMATE_FIELD_SIGNALS[signal].toggleId)
    )
    expect(getLayersForPanel('community')).toEqual(['demand-heatmap', 'interventions'])
    expect(getLayersForPanel('team')).toEqual([])
    expect(getLayersForPanel('analytics')).toEqual([])
  })

  it('keeps "building-footprints" uncategorised rather than panel-owned', () => {
    expect(panelIdForLayerToggle('building-footprints')).toBeNull()
  })

  // The dock orders its layer groups from this, so a layer-owning category missing here has
  // no group and everything it governs becomes unreachable from the sidebar.
  it('orders the layer-owning categories the way the registry declares them', () => {
    expect(panelIdsOwningLayers()).toEqual([
      'fire',
      'water',
      'vegetation',
      'soil',
      'climate',
      'community',
    ])
  })

  // The manager is meant to be comprehensive: every toggle reaches a category unless it is on
  // the uncategorised allowlist. A new entry that forgets its panelId shows up here. Necessary
  // but not sufficient -- an entry naming a category the manager renders no row for passes this
  // and is caught by the reachability test below instead.
  it('leaves no layer unreachable from the manager', () => {
    expect(UNCATEGORISED_LAYER_TOGGLE_IDS).toEqual(['building-footprints'])
    expect(unreachableLayerToggleIds()).toEqual([])
  })

  // The bug this pins: `sensors` and `evacuation-zones` named their panels, served real
  // tiles, and had no switch in any panel, so the only way to turn them on did not exist. The
  // registry-only check above reported zero unreachable layers throughout. The dock derives
  // its rows from the registry, so this now also pins that the derivation drops nothing.
  it('renders a row somewhere in the dock for every layer the registry gives a category', () => {
    expect(unreachableLayerToggleIds(dockReachableLayerToggleIds())).toEqual([])
  })

  // The comprehensiveness claim in the other direction: the manager's own bucket for layers no
  // category governs is what keeps `building-footprints` in the one complete list of layers
  // rather than missing from it. Since the bottom toolbar went on 2026-08-09 that bucket's row
  // is also its ONLY switch, so a regression here would make the layer unreachable outright.
  it('files the layer no category governs under the dock’s own bucket', () => {
    const basemap = DOCK_LAYER_GROUPS.find((group) => group.key === 'Basemap')
    expect(basemap?.layerIds).toEqual(['building-footprints'])
    // No report under it: a bucket of ungoverned layers has no panel body to disclose.
    expect(basemap?.detailsId).toBeNull()
    // Every registry layer, exactly once. Sorted, because grouping deliberately reorders:
    // the dock lists a category's layers together, and the registry declares them apart.
    expect([...dockReachableLayerToggleIds()].sort()).toEqual([...LAYER_TOGGLE_IDS].sort())
  })

  // Teams and Analytics own no layer, so the registry cannot order them and they get no
  // group; Alerts is not a registry concept at all. All three are still reachable, as the
  // dock's three layerless sections -- which is what stopped the rail's removal from
  // orphaning them.
  it('keeps the layerless reports reachable as their own dock sections', () => {
    expect(DOCK_PIVOT_SECTIONS).toEqual(['alerts', 'team', 'analytics'])
    for (const panelId of ['team', 'analytics'] as const) {
      expect(getLayersForPanel(panelId)).toEqual([])
      expect(DOCK_LAYER_GROUPS.some((group) => group.key === panelId)).toBe(false)
    }
  })

  // The same drift the other way round: a dock row for a layer whose registry entry lost its
  // panelId would flip a toggle the panel store no longer tracks. `building-footprints` is
  // the deliberate exception, filed under the Basemap bucket above.
  it('renders no category row for a layer the registry gives no category', () => {
    const orphaned = [...new Set(dockReachableLayerToggleIds())].filter(
      (layerId) =>
        panelIdForLayerToggle(layerId) === null &&
        !UNCATEGORISED_LAYER_TOGGLE_IDS.includes(layerId as (typeof LAYER_TOGGLE_IDS)[number])
    )
    expect(orphaned).toEqual([])
  })

  /**
   * Labels moved into the registry on 2026-08-08. Before that every name a user read was a
   * hand-typed `label` prop at one of sixteen `<LayerToggle>` call sites, so a tree grouped by
   * category had nowhere to read a layer's name from without importing five panels.
   *
   * The exact strings are pinned, not merely their presence: the migration's whole claim is
   * that no visible caption changed, and a silent rewording here is the one way that claim
   * fails without anything else breaking.
   */
  it('names every layer, with no blanks and no duplicates', () => {
    for (const toggleId of LAYER_TOGGLE_IDS) {
      const entry = LAYER_REGISTRY[toggleId]
      expect(entry.label.trim(), toggleId).not.toBe('')
      // A row shows an icon beside the name; the resolver in
      // src/components/map/layer-panel/layer-icons.tsx is exhaustive over the union.
      expect(entry.icon, toggleId).toBeTypeOf('string')
    }
    const labels = LAYER_TOGGLE_IDS.map((toggleId) => LAYER_REGISTRY[toggleId].label)
    expect(new Set(labels).size, 'two layers sharing one name').toBe(labels.length)
  })

  it('carries the exact captions the panel switches used to hand-type', () => {
    const labels = Object.fromEntries(
      LAYER_TOGGLE_IDS.map((toggleId) => [toggleId, LAYER_REGISTRY[toggleId].label])
    )
    expect(labels).toEqual({
      fire: 'Fire Detections',
      'fire-perimeters': 'Active Fire Perimeters',
      water: 'Water Gauges',
      drought: 'Drought Monitor',
      weather: 'Wind & Weather',
      sensors: 'Sensor Stations',
      watersheds: 'Watershed Boundaries',
      vegetation: 'Vegetation (NDVI)',
      soil: 'Soil Properties',
      'soil-survey': 'Soil Survey (SSURGO)',
      // Read off SOIL_FIELD_MEASURES rather than restated, which is why the soil section
      // could drop its `label={definition.layerLabel}` without changing a single caption.
      'soil-moisture': 'Soil Moisture (ERA5-Land)',
      'soil-temperature': 'Soil Temperature (ERA5-Land)',
      'soil-vpd': 'Vapor Pressure Deficit (ERA5-Land)',
      // The nine climate captions have no <LayerToggle> predecessor either: the Climate
      // section was added after the sheets were gone, and these are read off
      // CLIMATE_FIELD_SIGNALS rather than restated -- exactly as the three soil-field rows
      // are read off SOIL_FIELD_MEASURES -- so a signal's caption has one definition.
      'climate-air-temperature': 'Air temperature',
      'climate-dew-point': 'Dew point',
      'climate-precipitation': 'Precipitation',
      'climate-relative-humidity': 'Relative humidity',
      'climate-shortwave-radiation': 'Solar radiation',
      'climate-wind-speed': 'Wind speed',
      'climate-soil-wetness-surface': 'Soil wetness (surface)',
      'climate-soil-wetness-root-zone': 'Soil wetness (root zone)',
      'climate-soil-wetness-profile': 'Soil wetness (profile)',
      'demand-heatmap': 'Demand Heatmap',
      interventions: 'Interventions',
      'evacuation-zones': 'Evacuation Zones',
      'burn-severity': 'Burn History (MTBS)',
      // The one label with no <LayerToggle> predecessor: this layer is switched from the
      // manager's own Basemap bucket, never from a category's report.
      'building-footprints': '3D Building Footprints',
    })
  })

  /**
   * The registry is the ONLY place a layer is named, and the last surface that could have
   * held a second copy is gone: `<LayerToggle>` -- whose optional `label` prop this used to
   * scan every component source for overrides of -- was deleted with the seven sheets on
   * 2026-08-08. The dock's rows read `LAYER_REGISTRY[toggleId].label` through `layerLabel()`
   * and have no override to pass.
   *
   * Asserting the file's absence rather than re-scanning the sources, because with the
   * component gone a `<LayerToggle>` anywhere is an unresolved import and fails at `tsc`
   * before it can reach a test -- while a regex over source text cannot tell a real usage
   * from the several comments in `*Details.tsx` that name the switches they lost.
   */
  it('leaves the registry as the only place a layer is named', () => {
    expect(existsSync(join(COMPONENTS_DIR, 'ui', 'layer-toggle.tsx'))).toBe(false)
  })

  /**
   * The same claim for the 2026-08-09 wave, and the same reason for asserting absence rather
   * than scanning: each of these files held a control that WROTE state the manager owns --
   * `MapControls` a second `building-footprints` switch beside four render-mode buttons,
   * `CommandPalette` a `toggleLayer('fire-perimeters')` command plus the basemap/terrain/globe/
   * 3D commands, `SearchBar` the field that overlapped the manager's own header. Deleting them
   * is what makes the manager authoritative; hiding them would have left every one of those
   * writers live.
   */
  it.each([
    ['map', 'MapControls.tsx'],
    ['map', 'TerrainControl.tsx'],
    ['map', 'GlobeToggle.tsx'],
    ['map', 'Legend.tsx'],
    ['search', 'SearchBar.tsx'],
    ['search', 'SearchResults.tsx'],
    ['search', 'RecentSearches.tsx'],
    ['search', 'CommandPalette.tsx'],
  ] as const)('leaves no floating %s/%s surface writing map state', (directory, file) => {
    expect(existsSync(join(COMPONENTS_DIR, directory, file))).toBe(false)
  })

  it('treats a user-uploaded layer id as outside the registry', () => {
    // User-uploaded layers toggle database ids that cannot be a static union; they must not
    // resolve to a registry entry or claim a panel.
    expect(isLayerToggleId('3f6c1e2a-0000-4000-8000-000000000000')).toBe(false)
    expect(panelIdForLayerToggle('3f6c1e2a-0000-4000-8000-000000000000')).toBeNull()
  })

  // soil-survey is proxied live from the USDA Soil Data Mart through
  // environmental.getSoilSurvey. Nothing publishes it into geo.layers, so claiming a
  // warehouse layer name would make useLayerRenderState look up a slider capability that
  // can never exist and caption the layer with a history nobody measured. Its governance
  // stub is lifted, so it may not carry a withheld reason either.
  // soil-moisture is served out of the agri MODEL plane (agri.signal_observation), not out
  // of geo.features, so it has no geo.layers ROW -- but it does have a published stream
  // capability, which is a different thing and the one the slider reads.
  it.each(['soil-moisture', 'soil-temperature', 'soil-vpd'] as const)(
    'gives the agri-plane %s field a panel switch and a component render path',
    (toggleId) => {
      const entry = LAYER_REGISTRY[toggleId]
      expect(entry.renderKind).toBe('component')
      expect(entry.styleLayerIds).toEqual([])
      expect(entry.permanentlyUnavailableReason).toBeNull()
      expect(panelIdForLayerToggle(toggleId)).toBe('soil')
      expect(STYLE_LAYER_TOGGLE_MAP).not.toHaveProperty(toggleId)
    }
  )

  // The same shape as the three ERA5-Land fields above, one row per NASA POWER signal. The
  // lane shared a single `climate-field` toggle until 2026-08-10; the nine rows are what give
  // each signal its own axis, because one toggle can only carry one `warehouseLayerName` and
  // so only one capability -- see CLIMATE_FIELD_ENTRIES in the registry.
  it.each(CLIMATE_FIELD_SIGNAL_IDS as readonly ClimateFieldSignalId[])(
    'gives the %s climate signal a panel switch and a component render path',
    (signal: ClimateFieldSignalId) => {
      const { toggleId } = CLIMATE_FIELD_SIGNALS[signal]
      const entry = LAYER_REGISTRY[toggleId]
      expect(entry.renderKind).toBe('component')
      expect(entry.styleLayerIds).toEqual([])
      expect(entry.permanentlyUnavailableReason).toBeNull()
      expect(panelIdForLayerToggle(toggleId)).toBe('climate')
      expect(STYLE_LAYER_TOGGLE_MAP).not.toHaveProperty(toggleId)
    }
  )

  /**
   * Nine signals, nine distinct streams -- the assertion that pins the axis fix.
   *
   * The lane published ONE stream for all nine until 2026-08-10, computed over every
   * `signal_name` unioned, so the four-cell soil-wetness pilot was handed the full-lattice air
   * temperature field's earliest day, latest day and gap list. Distinctness is what makes each
   * capability describe its own signal; the round trip is what proves a server naming a stream
   * still finds the row that draws it.
   */
  it('gives every climate signal its own stream, and none of them share one', () => {
    const streamNames = CLIMATE_FIELD_SIGNAL_IDS.map(
      (signal) => CLIMATE_FIELD_SIGNALS[signal].streamName
    )
    expect(new Set(streamNames).size).toBe(CLIMATE_FIELD_SIGNAL_IDS.length)
    for (const signal of CLIMATE_FIELD_SIGNAL_IDS) {
      const { toggleId, streamName } = CLIMATE_FIELD_SIGNALS[signal]
      expect(LAYER_REGISTRY[toggleId].warehouseLayerName).toBe(streamName)
      expect(toggleIdForWarehouseLayerName(streamName)).toBe(toggleId)
    }
  })

  /**
   * The five toggles the slider had no axis for, and the names that give them one.
   *
   * All five carried `warehouseLayerName: null` until 2026-08-09, and the cost was the whole
   * feature for half the dated layers: no name meant no capability lookup, so `sliderDomain`
   * returned null, the row drew no track, and `resolveLayerDate` fell through to the server's
   * today on every render -- five layers pinned to the live edge with no way back, even though
   * every one of their readers takes a `date`.
   *
   * Read out of `SLIDER_STREAM_LAYER_NAMES` rather than spelled here, so this case cannot pass
   * against a hand-typed name no capability answers to -- which is the one way to reintroduce
   * exactly the same symptom while looking wired.
   */
  it.each([
    ['drought', SLIDER_STREAM_LAYER_NAMES.drought],
    ['soil-moisture', SLIDER_STREAM_LAYER_NAMES.soilMoisture],
    ['soil-temperature', SLIDER_STREAM_LAYER_NAMES.soilTemperature],
    ['soil-vpd', SLIDER_STREAM_LAYER_NAMES.soilVapourPressureDeficit],
  ] as const)('names the %s toggle a slider stream, so its row gets an axis again', (toggleId, streamName) => {
    expect(LAYER_REGISTRY[toggleId].warehouseLayerName).toBe(streamName)
    // And the inverse resolves, so a server or an agent naming the stream finds the row.
    expect(toggleIdForWarehouseLayerName(streamName)).toBe(toggleId)
  })

  // Every stream name must stay distinct from every geo.layers name: one capability list is
  // keyed by layerName, so a collision publishes two rows a lookup cannot tell apart. The
  // distinctness case above covers the registry's own names; this covers the constant itself.
  it('keeps every slider stream name out of the geo.layers namespace', () => {
    const geoLayerNames = LAYER_TOGGLE_IDS.map(
      (toggleId) => LAYER_REGISTRY[toggleId].warehouseLayerName
    ).filter(
      (name): name is string =>
        name !== null &&
        !Object.values(SLIDER_STREAM_LAYER_NAMES).includes(
          name as (typeof SLIDER_STREAM_LAYER_NAMES)[keyof typeof SLIDER_STREAM_LAYER_NAMES]
        )
    )
    for (const streamName of Object.values(SLIDER_STREAM_LAYER_NAMES)) {
      expect(geoLayerNames).not.toContain(streamName)
    }
  })

  // Every warehouse-backed field must be off by default. The mount-time race documented in
  // src/components/map/AGENTS.md means a default-on dynamically-imported layer registers its
  // style.load listener after the single rAF-scheduled initial load, so it reads as enabled
  // and never draws -- worse than being off, because the switch then lies.
  it('leaves the agri-plane fields out of the default active layers', () => {
    expect(useMapStore.getState().activeLayers).not.toContain('soil-moisture')
    expect(useMapStore.getState().activeLayers).not.toContain('soil-temperature')
    expect(useMapStore.getState().activeLayers).not.toContain('soil-vpd')
    expect(useMapStore.getState().activeLayers).not.toContain('climate-field')
  })

  it('treats the upstream-proxied collection as a component layer with no warehouse feed', () => {
    const entry = LAYER_REGISTRY['soil-survey']
    expect(entry.renderKind).toBe('component')
    expect(entry.styleLayerIds).toEqual([])
    expect(entry.warehouseLayerName).toBeNull()
    expect(entry.permanentlyUnavailableReason).toBeNull()
    expect(panelIdForLayerToggle('soil-survey')).toBe('soil')
    // Its style layer ids stay out of the setLayoutProperty path: a component-added
    // layer is toggled by presence, and LayerManager would flip a layer nobody added.
    expect(STYLE_LAYER_TOGGLE_MAP).not.toHaveProperty('soil-survey')
  })

  // The bug this pins: watersheds rendered nothing at any ordinary zoom while it was
  // proxied, because environmental.getWatersheds caps a request at 1 square degree and the
  // viewport bbox is ~767 at the default zoom -- every request 400d and the layer fell back
  // to an empty collection. The tile path has no bbox ceiling, so the entry must stay
  // style-backed with a warehouse name, and its style layer ids must be the ones layers.ts
  // bakes into every style -- a mismatch flips a layer that does not exist and draws nothing
  // while reporting no error.
  it('draws watersheds from the warehouse tile function, not from the tRPC proxy', () => {
    const entry = LAYER_REGISTRY.watersheds
    expect(entry.renderKind).toBe('style')
    expect(entry.styleLayerIds).toEqual(['watersheds-fill', 'watersheds-outline'])
    expect(entry.warehouseLayerName).toBe('watersheds')
    expect(entry.permanentlyUnavailableReason).toBeNull()
    expect(panelIdForLayerToggle('watersheds')).toBe('water')
    expect(STYLE_LAYER_TOGGLE_MAP.watersheds).toEqual(entry.styleLayerIds)
  })

  // The bug this pins: InterventionSubmitModal submits Point geometry, and the toggle's only
  // style layers were a fill and a line -- neither of which renders a Point. Approving a
  // recommendation therefore put it in the tile and painted nothing, with no error anywhere.
  // A circle layer over the same source-layer is the only thing that draws it, and it has to
  // be in styleLayerIds too or applyVisibility leaves it hidden when the toggle goes on.
  it('draws submitted point interventions with a circle layer the toggle controls', () => {
    const entry = LAYER_REGISTRY.interventions
    expect(entry.styleLayerIds).toContain('interventions-points')
    expect(STYLE_LAYER_TOGGLE_MAP.interventions).toEqual(entry.styleLayerIds)

    const pointLayer = getLayers().find((layer) => layer.id === 'interventions-points')
    expect(pointLayer?.type).toBe('circle')
    expect(pointLayer && 'source-layer' in pointLayer ? pointLayer['source-layer'] : null).toBe(
      'interventions'
    )
  })

  // demand-heatmap's stub was lifted 2026-08-03: /api/v1/action-network's k-anonymity
  // floor already satisfies the "reviewed, access-controlled publication" condition it
  // was withheld pending. building-footprints is withheld instead: its Martin function
  // is live but geo.osm_buildings has 0 rows, so the toggle would control nothing.
  it('withholds the two layers with no tiles to draw, and withholds nothing else', () => {
    const withheld = LAYER_TOGGLE_IDS.filter(
      (toggleId) => LAYER_REGISTRY[toggleId].permanentlyUnavailableReason !== null
    )
    expect(withheld).toEqual(['soil', 'building-footprints'])
  })
})
