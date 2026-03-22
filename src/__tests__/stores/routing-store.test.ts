import { describe, it, expect, beforeEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useRoutingStore } from '@/stores/routing-store'
import type { TransportMode, Waypoint } from '@/stores/routing-store'

describe('useRoutingStore', () => {
  beforeEach(() => {
    useRoutingStore.setState({
      origin: null,
      destination: null,
      waypoints: [],
      activeRoute: null,
      alternatives: [],
      transportMode: 'car',
      isCalculating: false,
    })
  })

  it('has correct initial state', () => {
    const { result } = renderHook(() => useRoutingStore())
    expect(result.current.transportMode).toBe('car')
    expect(result.current.origin).toBeNull()
    expect(result.current.destination).toBeNull()
    expect(result.current.waypoints).toHaveLength(0)
    expect(result.current.activeRoute).toBeNull()
    expect(result.current.isCalculating).toBe(false)
  })

  it('setOrigin updates origin', () => {
    const { result } = renderHook(() => useRoutingStore())
    const origin: Waypoint = { lat: 37.7749, lon: -122.4194, label: 'San Francisco' }

    act(() => {
      result.current.setOrigin(origin)
    })

    expect(result.current.origin).toEqual(origin)
  })

  it('setOrigin can clear origin with null', () => {
    const { result } = renderHook(() => useRoutingStore())

    act(() => {
      result.current.setOrigin({ lat: 37.7749, lon: -122.4194 })
    })
    act(() => {
      result.current.setOrigin(null)
    })

    expect(result.current.origin).toBeNull()
  })

  it('setDestination updates destination', () => {
    const { result } = renderHook(() => useRoutingStore())
    const dest: Waypoint = { lat: 34.0522, lon: -118.2437, label: 'Los Angeles' }

    act(() => {
      result.current.setDestination(dest)
    })

    expect(result.current.destination).toEqual(dest)
  })

  it('setTransportMode updates transportMode to bike', () => {
    const { result } = renderHook(() => useRoutingStore())

    act(() => {
      result.current.setTransportMode('bike')
    })

    expect(result.current.transportMode).toBe('bike')
  })

  it('setTransportMode updates transportMode to pedestrian', () => {
    const { result } = renderHook(() => useRoutingStore())

    act(() => {
      result.current.setTransportMode('pedestrian')
    })

    expect(result.current.transportMode).toBe('pedestrian')
  })

  it('setTransportMode updates transportMode to truck', () => {
    const { result } = renderHook(() => useRoutingStore())

    act(() => {
      result.current.setTransportMode('truck')
    })

    expect(result.current.transportMode).toBe('truck')
  })

  it('addWaypoint appends to waypoints array', () => {
    const { result } = renderHook(() => useRoutingStore())
    const wp: Waypoint = { lat: 36.7783, lon: -119.4179, label: 'Fresno' }

    act(() => {
      result.current.addWaypoint(wp)
    })

    expect(result.current.waypoints).toHaveLength(1)
    expect(result.current.waypoints[0]).toEqual(wp)
  })

  it('removeWaypoint removes by index', () => {
    const { result } = renderHook(() => useRoutingStore())

    act(() => {
      result.current.addWaypoint({ lat: 36, lon: -120 })
      result.current.addWaypoint({ lat: 37, lon: -121 })
    })

    act(() => {
      result.current.removeWaypoint(0)
    })

    expect(result.current.waypoints).toHaveLength(1)
    expect(result.current.waypoints[0]).toEqual({ lat: 37, lon: -121 })
  })

  it('reset clears all routing state', () => {
    const { result } = renderHook(() => useRoutingStore())

    act(() => {
      result.current.setOrigin({ lat: 37, lon: -122 })
      result.current.setDestination({ lat: 34, lon: -118 })
      result.current.setTransportMode('bike')
    })

    act(() => {
      result.current.reset()
    })

    expect(result.current.origin).toBeNull()
    expect(result.current.destination).toBeNull()
    expect(result.current.waypoints).toHaveLength(0)
    expect(result.current.activeRoute).toBeNull()
    expect(result.current.isCalculating).toBe(false)
  })

  it('setIsCalculating updates isCalculating flag', () => {
    const { result } = renderHook(() => useRoutingStore())

    act(() => {
      result.current.setIsCalculating(true)
    })

    expect(result.current.isCalculating).toBe(true)
  })
})
