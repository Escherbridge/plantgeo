import { describe, it, expect } from 'vitest'
import {
  LAYER_REGISTRY,
  LAYER_TOGGLE_IDS,
  isLayerToggleId,
  panelIdForLayerToggle,
  styleBackedLayerEntries,
  toggleIdForWarehouseLayerName,
} from '@/lib/map/layer-registry'
import { STYLE_LAYER_TOGGLE_MAP } from '@/lib/map/layers'
import { getLayersForPanel } from '@/stores/panel-store'

// The registry replaced three hand-written tables that could silently disagree. These
// assertions pin the derived shapes to what the map actually consumed before the merge,
// so a registry edit that drops a style layer or a legend mapping fails loudly here
// instead of quietly rendering a toggle that controls nothing.
describe('layer registry derivations', () => {
  it('derives exactly the style-backed toggles, with their style layer ids', () => {
    expect(STYLE_LAYER_TOGGLE_MAP).toEqual({
      'fire-perimeters': ['fire-perimeters', 'fire-perimeters-outline'],
      interventions: ['interventions', 'interventions-outline'],
      'building-footprints': ['building-footprints'],
    })
  })

  it('only style-backed entries carry style layer ids', () => {
    const styleBackedIds = styleBackedLayerEntries().map((entry) => entry.toggleId)
    expect(styleBackedIds).toEqual([
      'fire-perimeters',
      'interventions',
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
    // geo.layers rows with no renderer must stay untoggleable rather than pushing an
    // inert string into activeLayers.
    expect(toggleIdForWarehouseLayerName('evacuation-zones')).toBeNull()
    expect(toggleIdForWarehouseLayerName('sensors')).toBeNull()
  })

  it('gives each warehouse-backed toggle a distinct geo.layers name', () => {
    const names = LAYER_TOGGLE_IDS.map(
      (toggleId) => LAYER_REGISTRY[toggleId].warehouseLayerName
    ).filter((name): name is string => name !== null)
    expect(new Set(names).size).toBe(names.length)
  })

  it('inverts panel ownership into the same lists the panels governed before', () => {
    expect(getLayersForPanel('fire')).toEqual(['fire', 'fire-perimeters'])
    expect(getLayersForPanel('water')).toEqual(['water', 'drought', 'weather'])
    expect(getLayersForPanel('vegetation')).toEqual(['vegetation'])
    expect(getLayersForPanel('soil')).toEqual(['soil'])
    expect(getLayersForPanel('community')).toEqual(['demand-heatmap', 'interventions'])
    expect(getLayersForPanel('strategy')).toEqual([])
    expect(getLayersForPanel('team')).toEqual([])
    expect(getLayersForPanel('analytics')).toEqual([])
  })

  it('keeps "building-footprints" toolbar-owned rather than panel-owned', () => {
    expect(panelIdForLayerToggle('building-footprints')).toBeNull()
  })

  it('treats a user-uploaded layer id as outside the registry', () => {
    // LayerItem toggles database ids that cannot be a static union; they must not
    // resolve to a registry entry or claim a panel.
    expect(isLayerToggleId('3f6c1e2a-0000-4000-8000-000000000000')).toBe(false)
    expect(panelIdForLayerToggle('3f6c1e2a-0000-4000-8000-000000000000')).toBeNull()
  })

  it('withholds demand-heatmap at every date, and withholds nothing else', () => {
    const withheld = LAYER_TOGGLE_IDS.filter(
      (toggleId) => LAYER_REGISTRY[toggleId].permanentlyUnavailableReason !== null
    )
    expect(withheld).toEqual(['demand-heatmap'])
  })
})
