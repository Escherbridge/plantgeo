import { describe, it, expect, beforeEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useMapStore } from '@/stores/map-store'

describe('useMapStore', () => {
  beforeEach(() => {
    // Reset store to initial state before each test
    useMapStore.setState({
      viewport: {
        longitude: -119.4179,
        latitude: 36.7783,
        zoom: 6,
        bearing: 0,
        pitch: 0,
      },
      activeLayers: ['fire-perimeters', 'sensors'],
      selectedFeatureId: null,
      is3DEnabled: true,
      isGlobeView: false,
      terrainExaggeration: 1.5,
      currentStyle: 'dark',
      isTerrainEnabled: true,
    })
  })

  it('has correct initial state', () => {
    const { result } = renderHook(() => useMapStore())
    expect(result.current.viewport.longitude).toBe(-119.4179)
    expect(result.current.viewport.latitude).toBe(36.7783)
    expect(result.current.viewport.zoom).toBe(6)
    expect(result.current.is3DEnabled).toBe(true)
    expect(result.current.isGlobeView).toBe(false)
    expect(result.current.terrainExaggeration).toBe(1.5)
    expect(result.current.currentStyle).toBe('dark')
    expect(result.current.activeLayers).toEqual(['fire-perimeters', 'sensors'])
  })

  it('setViewport updates longitude, latitude, and zoom', () => {
    const { result } = renderHook(() => useMapStore())

    act(() => {
      result.current.setViewport({ longitude: -73.935, latitude: 40.73, zoom: 12 })
    })

    expect(result.current.viewport.longitude).toBe(-73.935)
    expect(result.current.viewport.latitude).toBe(40.73)
    expect(result.current.viewport.zoom).toBe(12)
  })

  it('setViewport merges partial updates without clobbering other fields', () => {
    const { result } = renderHook(() => useMapStore())

    act(() => {
      result.current.setViewport({ zoom: 10 })
    })

    expect(result.current.viewport.zoom).toBe(10)
    expect(result.current.viewport.longitude).toBe(-119.4179)
    expect(result.current.viewport.latitude).toBe(36.7783)
  })

  it('toggle3D changes is3DEnabled', () => {
    const { result } = renderHook(() => useMapStore())
    expect(result.current.is3DEnabled).toBe(true)

    act(() => {
      result.current.toggle3D()
    })

    expect(result.current.is3DEnabled).toBe(false)

    act(() => {
      result.current.toggle3D()
    })

    expect(result.current.is3DEnabled).toBe(true)
  })

  it('toggle3D adjusts pitch to 0 when disabling 3D', () => {
    const { result } = renderHook(() => useMapStore())

    act(() => {
      result.current.toggle3D()
    })

    expect(result.current.viewport.pitch).toBe(0)
  })

  it('toggle3D adjusts pitch to 60 when enabling 3D', () => {
    const { result } = renderHook(() => useMapStore())

    // Start disabled
    act(() => {
      result.current.toggle3D()
    })
    expect(result.current.is3DEnabled).toBe(false)

    // Re-enable
    act(() => {
      result.current.toggle3D()
    })
    expect(result.current.is3DEnabled).toBe(true)
    expect(result.current.viewport.pitch).toBe(60)
  })

  it('toggleGlobe changes isGlobeView', () => {
    const { result } = renderHook(() => useMapStore())
    expect(result.current.isGlobeView).toBe(false)

    act(() => {
      result.current.toggleGlobe()
    })

    expect(result.current.isGlobeView).toBe(true)

    act(() => {
      result.current.toggleGlobe()
    })

    expect(result.current.isGlobeView).toBe(false)
  })

  it('setTerrainExaggeration updates terrainExaggeration', () => {
    const { result } = renderHook(() => useMapStore())

    act(() => {
      result.current.setTerrainExaggeration(2.5)
    })

    expect(result.current.terrainExaggeration).toBe(2.5)
  })

  it('resetView restores default viewport and flags', () => {
    const { result } = renderHook(() => useMapStore())

    act(() => {
      result.current.setViewport({ longitude: 0, latitude: 0, zoom: 1 })
      result.current.toggleGlobe()
    })

    act(() => {
      result.current.resetView()
    })

    expect(result.current.viewport.longitude).toBe(-119.4179)
    expect(result.current.viewport.latitude).toBe(36.7783)
    expect(result.current.viewport.zoom).toBe(6)
    expect(result.current.isGlobeView).toBe(false)
    expect(result.current.is3DEnabled).toBe(true)
  })

  it('toggleLayer adds a layer when not active', () => {
    const { result } = renderHook(() => useMapStore())

    act(() => {
      result.current.toggleLayer('new-layer')
    })

    expect(result.current.activeLayers).toContain('new-layer')
  })

  it('toggleLayer removes a layer when already active', () => {
    const { result } = renderHook(() => useMapStore())

    act(() => {
      result.current.toggleLayer('fire-perimeters')
    })

    expect(result.current.activeLayers).not.toContain('fire-perimeters')
  })
})
