import { vi } from 'vitest'

// Mock maplibre-gl
vi.mock('maplibre-gl', () => ({
  default: {
    Map: vi.fn(),
    NavigationControl: vi.fn(),
    GeolocateControl: vi.fn(),
    ScaleControl: vi.fn(),
    AttributionControl: vi.fn(),
    Popup: vi.fn(),
    Marker: vi.fn(),
    LngLatBounds: vi.fn(),
    LngLat: vi.fn(),
    supported: vi.fn(() => true),
  },
}))

// Mock ResizeObserver
globalThis.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

// Mock IntersectionObserver
globalThis.IntersectionObserver = class IntersectionObserver {
  readonly root: Element | null = null
  readonly rootMargin: string = ''
  readonly thresholds: ReadonlyArray<number> = []
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords(): IntersectionObserverEntry[] {
    return []
  }
}

// Mock fetch
globalThis.fetch = vi.fn()
