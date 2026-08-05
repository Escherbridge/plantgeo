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
import { STYLE_LAYER_TOGGLE_MAP } from '@/lib/map/layers'
import { getLayersForPanel } from '@/stores/panel-store'

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
      'evacuation-zones': ['evacuation-zones', 'evacuation-zones-outline'],
      interventions: ['interventions', 'interventions-outline'],
      'burn-severity': ['burn-severity', 'burn-severity-outline'],
      'building-footprints': ['building-footprints'],
    })
  })

  it('only style-backed entries carry style layer ids', () => {
    const styleBackedIds = styleBackedLayerEntries().map((entry) => entry.toggleId)
    expect(styleBackedIds).toEqual([
      'fire-perimeters',
      'sensors',
      'interventions',
      'evacuation-zones',
      'burn-severity',
      'building-footprints',
    ])
    for (const entry of styleBackedLayerEntries()) {
      expect(entry.renderKind).toBe('style')
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
    expect(getLayersForPanel('soil')).toEqual(['soil', 'soil-survey'])
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

  it('treats a user-uploaded layer id as outside the registry', () => {
    // LayerItem toggles database ids that cannot be a static union; they must not
    // resolve to a registry entry or claim a panel.
    expect(isLayerToggleId('3f6c1e2a-0000-4000-8000-000000000000')).toBe(false)
    expect(panelIdForLayerToggle('3f6c1e2a-0000-4000-8000-000000000000')).toBeNull()
  })

  // watersheds and soil-survey are proxied live from USGS NHD+ HR and the USDA Soil Data
  // Mart through environmental.getWatersheds/getSoilSurvey. Nothing publishes them into
  // geo.layers, so claiming a warehouse layer name would make useLayerRenderState look up
  // a slider capability that can never exist and caption the layer with a history nobody
  // measured. Their governance stubs are lifted, so neither may carry a withheld reason.
  it('treats the upstream-proxied collections as component layers with no warehouse feed', () => {
    for (const toggleId of ['watersheds', 'soil-survey'] as const) {
      const entry = LAYER_REGISTRY[toggleId]
      expect(entry.renderKind).toBe('component')
      expect(entry.styleLayerIds).toEqual([])
      expect(entry.warehouseLayerName).toBeNull()
      expect(entry.permanentlyUnavailableReason).toBeNull()
    }
    expect(panelIdForLayerToggle('watersheds')).toBe('water')
    expect(panelIdForLayerToggle('soil-survey')).toBe('soil')
    // Their style layer ids stay out of the setLayoutProperty path: a component-added
    // layer is toggled by presence, and LayerManager would flip a layer nobody added.
    expect(STYLE_LAYER_TOGGLE_MAP).not.toHaveProperty('watersheds')
    expect(STYLE_LAYER_TOGGLE_MAP).not.toHaveProperty('soil-survey')
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
