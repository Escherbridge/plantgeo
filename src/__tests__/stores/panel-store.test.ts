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
      const occurrences = managedIds.filter((managedId) => managedId === id).length
      expect(occurrences).toBe(1)
      expect(getPanelForLayer(id)).not.toBeNull()
    }
  })

  // The only null `getPanelForLayer` can produce now that `panelId` is total over the
  // registry: an id that is not a registry layer at all, which is what an upload's is.
  it('claims no panel for a layer id outside the registry', () => {
    expect(getPanelForLayer('3f6c1e2a-0000-4000-8000-000000000000')).toBeNull()
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

  // What a shortcut outside the manager calls. Three facts in one action, because a shortcut
  // that expanded a section without docking the panel would point at something nobody can see.
  it('docks, expands and queues a scroll when a section is focused', () => {
    act(() => {
      usePanelStore.getState().focusDockSection('offline')
    })

    const state = usePanelStore.getState()
    expect(state.layerPanelOpen).toBe(true)
    expect(state.expandedDetails).toContain('offline')
    expect(state.pendingScrollSection).toBe('offline')
  })

  // Focusing an already-expanded section must not toggle it shut, and must not list it twice.
  it('re-focuses an open section without closing or duplicating it', () => {
    act(() => {
      usePanelStore.getState().toggleDetails('offline')
      usePanelStore.getState().focusDockSection('offline')
    })

    expect(usePanelStore.getState().expandedDetails).toEqual(['offline'])
  })

  /**
   * Rewritten from "docks, expands and queues a scroll for the Time section too", which pinned
   * `focusDockSection('time')` on behalf of the top-bar date pill. Both are gone as of
   * 2026-08-09: every layer scrubs its own day on its own row, so there is no map-wide date for
   * a pill to state or for a section to hold, and "time" left `DockSectionId` with them.
   *
   * The replacement is the same claim stated as an absence, and it is worth an assertion rather
   * than nothing: a re-added "time" member would compile and quietly reintroduce a second,
   * map-wide control over dates that are now per layer.
   */
  it('offers no map-wide time section to focus', () => {
    expect(INITIALLY_EXPANDED_SECTIONS).not.toContain('time')
    expect(usePanelStore.getState().expandedDetails).not.toContain('time')
  })

  /**
   * Only CONTROL sections are seeded, and the exception proves the rule rather than weakening
   * it: expanding a REPORT mounts it and fires its warehouse queries, while this body issues
   * nothing -- the Search field's geocode is silent below two characters.
   *
   * "view" is deliberately not seeded -- a basemap is picked once a session -- which is what
   * keeps this an assertion about cost rather than about which union a section belongs to.
   * "time" was seeded on the same grounds until its section was replaced by a slider per layer
   * row; the layer groups those rows sit in are expanded by default, so the controls are no
   * less reachable for it.
   */
  it('seeds only sections that cost no query', () => {
    expect(INITIALLY_EXPANDED_SECTIONS).toEqual(['search'])
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
