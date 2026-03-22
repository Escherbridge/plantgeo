import { describe, it, expect, beforeEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useDrawingStore } from '@/stores/drawing-store'
import type { DrawingMode } from '@/stores/drawing-store'

const emptyCollection = (): GeoJSON.FeatureCollection => ({
  type: 'FeatureCollection',
  features: [],
})

const makePointFeature = (id: string): GeoJSON.Feature => ({
  type: 'Feature',
  geometry: { type: 'Point', coordinates: [0, 0] },
  properties: { id },
})

describe('useDrawingStore', () => {
  beforeEach(() => {
    useDrawingStore.setState({
      drawingMode: null,
      features: emptyCollection(),
      selectedFeatureIndex: null,
      undoStack: [],
      redoStack: [],
      isDrawing: false,
    })
  })

  it('has null initial mode', () => {
    const { result } = renderHook(() => useDrawingStore())
    expect(result.current.drawingMode).toBeNull()
  })

  it('has empty initial features collection', () => {
    const { result } = renderHook(() => useDrawingStore())
    expect(result.current.features.type).toBe('FeatureCollection')
    expect(result.current.features.features).toHaveLength(0)
  })

  it('setMode changes drawingMode', () => {
    const { result } = renderHook(() => useDrawingStore())

    act(() => {
      result.current.setMode('line')
    })

    expect(result.current.drawingMode).toBe('line')
  })

  it('setMode accepts all valid modes', () => {
    const { result } = renderHook(() => useDrawingStore())
    const modes: DrawingMode[] = ['point', 'line', 'polygon', 'rectangle', 'circle', 'freehand', 'text', 'select', null]

    for (const mode of modes) {
      act(() => {
        result.current.setMode(mode)
      })
      expect(result.current.drawingMode).toBe(mode)
    }
  })

  it('addFeature adds to features.features array', () => {
    const { result } = renderHook(() => useDrawingStore())
    const feature = makePointFeature('f1')

    act(() => {
      result.current.addFeature(feature)
    })

    expect(result.current.features.features).toHaveLength(1)
    expect(result.current.features.features[0]).toEqual(feature)
  })

  it('addFeature pushes to undoStack', () => {
    const { result } = renderHook(() => useDrawingStore())

    act(() => {
      result.current.addFeature(makePointFeature('f1'))
    })

    expect(result.current.undoStack).toHaveLength(1)
  })

  it('addFeature clears redoStack', () => {
    const { result } = renderHook(() => useDrawingStore())

    // Add then undo to populate redoStack
    act(() => {
      result.current.addFeature(makePointFeature('f1'))
    })
    act(() => {
      result.current.undo()
    })
    expect(result.current.redoStack).toHaveLength(1)

    // Add again should clear redoStack
    act(() => {
      result.current.addFeature(makePointFeature('f2'))
    })
    expect(result.current.redoStack).toHaveLength(0)
  })

  it('undo pops undoStack and restores previous state', () => {
    const { result } = renderHook(() => useDrawingStore())

    act(() => {
      result.current.addFeature(makePointFeature('f1'))
    })

    expect(result.current.features.features).toHaveLength(1)

    act(() => {
      result.current.undo()
    })

    expect(result.current.features.features).toHaveLength(0)
    expect(result.current.undoStack).toHaveLength(0)
  })

  it('undo does nothing when undoStack is empty', () => {
    const { result } = renderHook(() => useDrawingStore())

    act(() => {
      result.current.undo()
    })

    expect(result.current.features.features).toHaveLength(0)
  })

  it('redo restores after undo', () => {
    const { result } = renderHook(() => useDrawingStore())
    const feature = makePointFeature('f1')

    act(() => {
      result.current.addFeature(feature)
    })
    act(() => {
      result.current.undo()
    })
    expect(result.current.features.features).toHaveLength(0)

    act(() => {
      result.current.redo()
    })

    expect(result.current.features.features).toHaveLength(1)
    expect(result.current.features.features[0]).toEqual(feature)
  })

  it('clear resets to empty FeatureCollection', () => {
    const { result } = renderHook(() => useDrawingStore())

    act(() => {
      result.current.addFeature(makePointFeature('f1'))
      result.current.addFeature(makePointFeature('f2'))
    })

    act(() => {
      result.current.clear()
    })

    expect(result.current.features.features).toHaveLength(0)
    expect(result.current.features.type).toBe('FeatureCollection')
    expect(result.current.drawingMode).toBeNull()
    expect(result.current.isDrawing).toBe(false)
  })
})
