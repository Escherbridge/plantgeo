import { describe, it, expect, beforeEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useMapStore, DEFAULT_VIEWPORT } from '@/stores/map-store'
import {
  INITIALLY_EXPANDED_SECTIONS,
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
 * The dock's state: whether it is docked, and which of its report sections are expanded.
 *
 * The `openPanel` field these cases used to exercise is gone with the seven right-hand sheets
 * it addressed (2026-08-08). What replaced it is not a rename: `openPanel` was one-at-a-time
 * and cosmetic, while `expandedDetails` is a list and is load-bearing -- a details region is
 * MOUNTED only while its id is in it, so these booleans decide which queries exist.
 */
describe('map dock state', () => {
  beforeEach(() => {
    usePanelStore.setState({
      layerPanelOpen: false,
      expandedDetails: [],
      pendingScrollSection: null,
    })
  })

  // The map opens with every layer off, so a 19rem dock over the canvas is not what a
  // first-time visitor is owed before they have asked for anything.
  it('starts undocked with every report collapsed', () => {
    expect(usePanelStore.getState().layerPanelOpen).toBe(false)
    expect(usePanelStore.getState().expandedDetails).toEqual([])
  })

  // Opening the dock must stay cheap: it shows seventeen layer rows, and every report under
  // them stays collapsed -- and so unmounted, and so unfetched -- until asked for.
  it('expands no report when the dock opens', () => {
    act(() => {
      usePanelStore.getState().toggleLayerPanel()
    })

    expect(usePanelStore.getState().layerPanelOpen).toBe(true)
    expect(usePanelStore.getState().expandedDetails).toEqual([])
  })

  it('expands and collapses one report at a time, leaving the others alone', () => {
    act(() => {
      usePanelStore.getState().toggleDetails('water')
      usePanelStore.getState().toggleDetails('soil')
    })
    expect(usePanelStore.getState().expandedDetails).toEqual(['water', 'soil'])

    act(() => {
      usePanelStore.getState().toggleDetails('water')
    })
    expect(usePanelStore.getState().expandedDetails).toEqual(['soil'])
  })

  // What the toolbar's alert bell calls. Three facts in one action, because a shortcut that
  // expanded a section without docking the panel would point at something nobody can see.
  it('docks, expands and queues a scroll when a section is focused', () => {
    act(() => {
      usePanelStore.getState().focusDockSection('alerts')
    })

    const state = usePanelStore.getState()
    expect(state.layerPanelOpen).toBe(true)
    expect(state.expandedDetails).toContain('alerts')
    expect(state.pendingScrollSection).toBe('alerts')
  })

  // Focusing an already-expanded section must not toggle it shut, and must not list it twice.
  it('re-focuses an open section without closing or duplicating it', () => {
    act(() => {
      usePanelStore.getState().toggleDetails('alerts')
      usePanelStore.getState().focusDockSection('alerts')
    })

    expect(usePanelStore.getState().expandedDetails).toEqual(['alerts'])
  })

  // What the top-bar date pill calls (2026-08-08). The scrubber lives in the dock now, so the
  // pill is a shortcut to a section rather than a disclosure over a card of its own.
  it('docks, expands and queues a scroll for the Time section too', () => {
    act(() => {
      usePanelStore.getState().focusDockSection('time')
    })

    const state = usePanelStore.getState()
    expect(state.layerPanelOpen).toBe(true)
    expect(state.expandedDetails).toContain('time')
    expect(state.pendingScrollSection).toBe('time')
  })

  /**
   * "time" is the only seeded section, and the exception proves the rule rather than weakening
   * it: expanding a REPORT mounts it and fires its warehouse queries, while the Time section's
   * body is the scrubber card, whose capabilities arrive from the always-mounted
   * TimeSliderCapabilitiesLoader whether the dock is open or not.
   */
  it('seeds only the section that costs no query', () => {
    expect(INITIALLY_EXPANDED_SECTIONS).toEqual(['time'])
  })

  // The section clears its own request on arrival, so a later expansion by hand does not
  // re-scroll the dock out from under the reader.
  it('lets the arriving section consume the scroll request', () => {
    act(() => {
      usePanelStore.getState().focusDockSection('team')
      usePanelStore.getState().clearPendingScroll()
    })

    expect(usePanelStore.getState().pendingScrollSection).toBeNull()
    expect(usePanelStore.getState().expandedDetails).toEqual(['team'])
  })

  it('never changes what is drawn', () => {
    act(() => {
      useMapStore.setState({ activeLayers: ['sensors'] })
      usePanelStore.getState().toggleLayerPanel()
      usePanelStore.getState().toggleDetails('water')
      usePanelStore.getState().closeLayerPanel()
    })

    expect(useMapStore.getState().activeLayers).toEqual(['sensors'])
  })
})
