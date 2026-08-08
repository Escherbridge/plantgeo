import { describe, it, expect, beforeEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useMapStore, DEFAULT_VIEWPORT } from '@/stores/map-store'
import {
  usePanelHasActiveLayers,
  getPanelForLayer,
  getAllManagedLayerIds,
  usePanelStore,
} from '@/stores/panel-store'
import { STYLE_LAYER_TOGGLE_MAP } from '@/lib/map/layers'

describe('usePanelHasActiveLayers', () => {
  beforeEach(() => {
    // Reset map-store to a clean, layer-free baseline before each test.
    useMapStore.setState({
      viewport: { ...DEFAULT_VIEWPORT },
      activeLayers: [],
      selectedFeatureId: null,
      is3DEnabled: false,
      isGlobeView: false,
      terrainExaggeration: 1.5,
      currentStyle: 'dark',
      isTerrainEnabled: false,
    })
  })

  it('reports false for a panel with no active governed layers', () => {
    const { result } = renderHook(() => usePanelHasActiveLayers('fire'))
    expect(result.current).toBe(false)
  })

  it('reports the fire panel active when only "fire-perimeters" is on', () => {
    act(() => {
      useMapStore.setState({ activeLayers: ['fire-perimeters'] })
    })
    const { result } = renderHook(() => usePanelHasActiveLayers('fire'))
    expect(result.current).toBe(true)
  })

  it('reports the community panel active when only "interventions" is on', () => {
    act(() => {
      useMapStore.setState({ activeLayers: ['interventions'] })
    })
    const { result } = renderHook(() => usePanelHasActiveLayers('community'))
    expect(result.current).toBe(true)
  })

  it('reports the water panel active when only "weather" is on', () => {
    act(() => {
      useMapStore.setState({ activeLayers: ['weather'] })
    })
    const { result } = renderHook(() => usePanelHasActiveLayers('water'))
    expect(result.current).toBe(true)
  })
})

describe('PANEL_LAYER_MAP exhaustiveness', () => {
  // Regression guard for the panel-aggregation bug: every static style layer
  // toggle that is governed by a sidebar panel must be reachable from exactly
  // one PANEL_LAYER_MAP entry, or usePanelHasActiveLayers silently under-reports.
  it('every panel-governed STYLE_LAYER_TOGGLE_MAP id appears in exactly one panel list', () => {
    const managedIds = getAllManagedLayerIds()

    for (const id of Object.keys(STYLE_LAYER_TOGGLE_MAP)) {
      // "building-footprints" is toggled from the MapControls toolbar, not
      // from any sidebar panel — it has no owning PanelId and is intentionally
      // excluded from PANEL_LAYER_MAP. Assert that exclusion explicitly below
      // instead of silently skipping it.
      if (id === 'building-footprints') continue

      const occurrences = managedIds.filter((managedId) => managedId === id).length
      expect(occurrences).toBe(1)
      expect(getPanelForLayer(id)).not.toBeNull()
    }
  })

  it('"building-footprints" is deliberately not panel-governed (MapControls toolbar toggle)', () => {
    expect(getPanelForLayer('building-footprints')).toBeNull()
  })
})

/**
 * The left-edge layer tree's dock state.
 *
 * It is deliberately independent of `openPanel`: the tree governs every layer at once while
 * the right-hand sheets report on one category each, so both may be open and closing either
 * must leave the other alone. It is also NOT layer visibility -- `map-store.activeLayers`
 * stays the single source of that, and the tree's eyes write it exactly as the sheets do.
 */
describe('layer panel dock state', () => {
  beforeEach(() => {
    usePanelStore.setState({ openPanel: null, layerPanelOpen: false })
  })

  // The map opens with every layer off, so a 19rem dock over the canvas is not what a
  // first-time visitor is owed before they have asked for anything.
  it('starts undocked', () => {
    expect(usePanelStore.getState().layerPanelOpen).toBe(false)
  })

  it('docks and undocks without touching the open sheet', () => {
    act(() => {
      usePanelStore.getState().togglePanel('water')
      usePanelStore.getState().toggleLayerPanel()
    })
    expect(usePanelStore.getState().layerPanelOpen).toBe(true)
    expect(usePanelStore.getState().openPanel).toBe('water')

    act(() => {
      usePanelStore.getState().closeLayerPanel()
    })
    expect(usePanelStore.getState().layerPanelOpen).toBe(false)
    expect(usePanelStore.getState().openPanel).toBe('water')
  })

  it('leaves the dock alone when a sheet closes', () => {
    act(() => {
      usePanelStore.getState().toggleLayerPanel()
      usePanelStore.getState().togglePanel('fire')
      usePanelStore.getState().closePanel()
    })

    expect(usePanelStore.getState().openPanel).toBeNull()
    expect(usePanelStore.getState().layerPanelOpen).toBe(true)
  })

  it('never changes what is drawn', () => {
    act(() => {
      useMapStore.setState({ activeLayers: ['sensors'] })
      usePanelStore.getState().toggleLayerPanel()
      usePanelStore.getState().closeLayerPanel()
    })

    expect(useMapStore.getState().activeLayers).toEqual(['sensors'])
  })
})
