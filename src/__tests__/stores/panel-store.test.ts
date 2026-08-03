import { describe, it, expect, beforeEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useMapStore, DEFAULT_VIEWPORT } from '@/stores/map-store'
import { usePanelHasActiveLayers, getPanelForLayer, getAllManagedLayerIds } from '@/stores/panel-store'
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
