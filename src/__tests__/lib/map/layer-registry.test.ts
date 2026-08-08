import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { URL as NodeURL, fileURLToPath } from 'node:url'
import { describe, it, expect } from 'vitest'
import {
  LAYER_REGISTRY,
  LAYER_TOGGLE_IDS,
  TOOLBAR_OWNED_LAYER_TOGGLE_IDS,
  isLayerToggleId,
  panelIdForLayerToggle,
  panelIdsOwningLayers,
  styleBackedLayerEntries,
  toggleIdForWarehouseLayerName,
  unreachableLayerToggleIds,
} from '@/lib/map/layer-registry'
import { STYLE_LAYER_TOGGLE_MAP, getLayers } from '@/lib/map/layers'
import { getLayersForPanel } from '@/stores/panel-store'
import { useMapStore } from '@/stores/map-store'

// Explicit node:url URL, not the ambient global: this file runs under vitest's jsdom
// environment, which shadows globalThis.URL with a browser polyfill that mis-resolves
// multi-level ".." against a Windows file:// base (silently falls back to
// http://localhost:3000/<tail> instead of the real path).
const COMPONENTS_DIR = fileURLToPath(new NodeURL('../../../components', import.meta.url))

/** Every .tsx file under a directory, recursively. */
function componentSourceFiles(directory: string): string[] {
  const found: string[] = []
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const fullPath = join(directory, entry.name)
    if (entry.isDirectory()) found.push(...componentSourceFiles(fullPath))
    else if (entry.name.endsWith('.tsx')) found.push(fullPath)
  }
  return found
}

/**
 * Every layer id a `<LayerToggle>` under src/components actually renders, read out of the
 * sources. Nothing importable can answer this: a component's JSX is not reachable from the
 * registry, and mounting all seven panels to look for switches would mean standing up the
 * map, tRPC and every store they read.
 */
function renderedLayerToggleIds(): string[] {
  const rendered: string[] = []
  for (const file of componentSourceFiles(COMPONENTS_DIR)) {
    const source = readFileSync(file, 'utf8')
    const usageCount = source.match(/<LayerToggle\b/g)?.length ?? 0
    const withLayerId = [...source.matchAll(/<LayerToggle\b[^>]*\blayerId="([\w-]+)"/g)]
    // A usage this regex cannot read would look exactly like an absent switch, which is the
    // failure the tests below exist to catch -- so it fails here instead of passing quietly.
    expect(withLayerId.length, `unreadable <LayerToggle> layerId in ${file}`).toBe(usageCount)
    rendered.push(...withLayerId.map((match) => match[1]))
  }
  return rendered
}

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
    ])
    expect(getLayersForPanel('community')).toEqual(['demand-heatmap', 'interventions'])
    expect(getLayersForPanel('team')).toEqual([])
    expect(getLayersForPanel('analytics')).toEqual([])
  })

  it('keeps "building-footprints" toolbar-owned rather than panel-owned', () => {
    expect(panelIdForLayerToggle('building-footprints')).toBeNull()
  })

  // PanelManager builds its rail from this order, so a layer-owning panel that is missing
  // here has no button and everything it governs becomes unreachable from the sidebar.
  it('orders the rail-bearing panels the way the registry declares them', () => {
    expect(panelIdsOwningLayers()).toEqual(['fire', 'water', 'vegetation', 'soil', 'community'])
  })

  // The rail is meant to be comprehensive: every toggle reaches a panel unless it is on the
  // toolbar allowlist. A new entry that forgets its panelId shows up here. Necessary but not
  // sufficient -- an entry that names a panel rendering no switch for it passes this and is
  // caught by the source-reading test below instead.
  it('leaves no layer unreachable from either the rail or the toolbar', () => {
    expect(TOOLBAR_OWNED_LAYER_TOGGLE_IDS).toEqual(['building-footprints'])
    expect(unreachableLayerToggleIds()).toEqual([])
  })

  // The bug this pins: `sensors` and `evacuation-zones` named their panels, served real
  // tiles, and had no LayerToggle in any panel, so the only way to switch them on did not
  // exist. The registry-only check above reported zero unreachable layers throughout.
  // PanelManager's rail tooltip is built from the same ownership, so this is also what keeps
  // each rail button from naming a control its panel never renders.
  it('renders a switch somewhere for every layer the registry gives a panel', () => {
    expect(unreachableLayerToggleIds(renderedLayerToggleIds())).toEqual([])
  })

  // The same drift the other way round: a panel keeping a switch for a layer whose registry
  // entry lost its panelId would flip a toggle the rail and the panel store no longer track.
  it('renders no switch for a layer the registry gives no panel', () => {
    const orphaned = [...new Set(renderedLayerToggleIds())].filter(
      (layerId) => panelIdForLayerToggle(layerId) === null
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
      // Read off SOIL_FIELD_MEASURES rather than restated, which is why SoilPanel could drop
      // its `label={definition.layerLabel}` without changing a single caption.
      'soil-moisture': 'Soil Moisture (ERA5-Land)',
      'soil-temperature': 'Soil Temperature (ERA5-Land)',
      'demand-heatmap': 'Demand Heatmap',
      interventions: 'Interventions',
      'evacuation-zones': 'Evacuation Zones',
      'burn-severity': 'Burn History (MTBS)',
      // The one label with no <LayerToggle> predecessor: this layer is switched from the
      // MapControls toolbar, never from a panel.
      'building-footprints': '3D Building Footprints',
    })
  })

  // The registry is now the default, so a call site that still passes one is overriding it --
  // which is legitimate but must be deliberate. None does today.
  it('leaves every panel switch reading its name from the registry', () => {
    const overrides: string[] = []
    for (const file of componentSourceFiles(COMPONENTS_DIR)) {
      const source = readFileSync(file, 'utf8')
      for (const match of source.matchAll(/<LayerToggle\b[^>]*/g)) {
        if (/\blabel=/.test(match[0])) overrides.push(`${file}: ${match[0]}`)
      }
    }
    expect(overrides).toEqual([])
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
  // of geo.features, so it has no geo.layers row to name and no slider capability to look
  // up -- the same shape drought and the proxied collections have. It still draws the
  // slider's day; it simply makes no claim about which days the axis should offer.
  it.each(['soil-moisture', 'soil-temperature'] as const)(
    'gives the agri-plane %s field a panel switch and no warehouse feed',
    (toggleId) => {
      const entry = LAYER_REGISTRY[toggleId]
      expect(entry.renderKind).toBe('component')
      expect(entry.styleLayerIds).toEqual([])
      expect(entry.warehouseLayerName).toBeNull()
      expect(entry.permanentlyUnavailableReason).toBeNull()
      expect(panelIdForLayerToggle(toggleId)).toBe('soil')
      expect(STYLE_LAYER_TOGGLE_MAP).not.toHaveProperty(toggleId)
    }
  )

  // Both soil fields must be off by default. The mount-time race documented in
  // src/components/map/AGENTS.md means a default-on dynamically-imported layer registers its
  // style.load listener after the single rAF-scheduled initial load, so it reads as enabled
  // and never draws -- worse than being off, because the switch then lies.
  it('leaves both agri-plane soil fields out of the default active layers', () => {
    expect(useMapStore.getState().activeLayers).not.toContain('soil-moisture')
    expect(useMapStore.getState().activeLayers).not.toContain('soil-temperature')
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
  it('withholds building-footprints at every date, and withholds nothing else', () => {
    const withheld = LAYER_TOGGLE_IDS.filter(
      (toggleId) => LAYER_REGISTRY[toggleId].permanentlyUnavailableReason !== null
    )
    expect(withheld).toEqual(['building-footprints'])
  })
})
