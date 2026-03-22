import { describe, it, expect } from 'vitest'
import {
  haversineDistance,
  polygonArea,
  formatDistance,
  formatArea,
  bearing,
  lineStringDistance,
} from '@/lib/map/measurement'

describe('haversineDistance', () => {
  it('returns ~111320m for 1 degree of latitude at the equator', () => {
    const distance = haversineDistance([0, 0], [0, 1])
    expect(distance).toBeGreaterThan(111_000)
    expect(distance).toBeLessThan(112_000)
  })

  it('returns ~111320m for 1 degree of longitude at the equator', () => {
    const distance = haversineDistance([0, 0], [1, 0])
    expect(distance).toBeGreaterThan(111_000)
    expect(distance).toBeLessThan(112_000)
  })

  it('returns 0 for identical points', () => {
    expect(haversineDistance([10, 20], [10, 20])).toBe(0)
  })

  it('is within 1% of 111320m for [0,0] to [0,1]', () => {
    const distance = haversineDistance([0, 0], [0, 1])
    expect(Math.abs(distance - 111_320) / 111_320).toBeLessThan(0.01)
  })
})

describe('polygonArea', () => {
  it('returns 0 for fewer than 3 points', () => {
    expect(polygonArea([[0, 0], [1, 0]])).toBe(0)
    expect(polygonArea([[0, 0]])).toBe(0)
  })

  it('returns a positive value for a 1-degree square near the equator', () => {
    const squareCoords: [number, number][] = [
      [0, 0],
      [1, 0],
      [1, 1],
      [0, 1],
    ]
    const area = polygonArea(squareCoords)
    // A 1-degree square near equator is roughly 12,300 km²
    expect(area).toBeGreaterThan(1e10) // at least 10,000 km²
    expect(area).toBeLessThan(1.5e13) // less than 15,000,000 km²
  })
})

describe('formatDistance', () => {
  it('formats 1000m as "1.00 km" in metric', () => {
    expect(formatDistance(1000, 'metric')).toBe('1.00 km')
  })

  it('formats 500m as "500 m" in metric', () => {
    expect(formatDistance(500, 'metric')).toBe('500 m')
  })

  it('formats 1609m to contain "mi" in imperial', () => {
    const result = formatDistance(1609, 'imperial')
    expect(result).toContain('mi')
  })

  it('formats short imperial distance in feet', () => {
    const result = formatDistance(100, 'imperial')
    expect(result).toContain('ft')
  })

  it('formats 0m as "0 m" in metric', () => {
    expect(formatDistance(0, 'metric')).toBe('0 m')
  })
})

describe('formatArea', () => {
  it('formats large metric area in hectares', () => {
    const result = formatArea(20000, 'metric')
    expect(result).toContain('ha')
  })

  it('formats small metric area in m²', () => {
    const result = formatArea(500, 'metric')
    expect(result).toContain('m²')
  })

  it('formats large imperial area in acres', () => {
    const result = formatArea(50000, 'imperial')
    expect(result).toContain('ac')
  })

  it('formats small imperial area in ft²', () => {
    const result = formatArea(10, 'imperial')
    expect(result).toContain('ft²')
  })
})

describe('bearing', () => {
  it('returns ~0 degrees bearing due north', () => {
    const b = bearing([0, 0], [0, 1])
    expect(b).toBeGreaterThanOrEqual(0)
    expect(b).toBeLessThan(5)
  })

  it('returns ~90 degrees bearing due east', () => {
    const b = bearing([0, 0], [1, 0])
    expect(b).toBeGreaterThan(85)
    expect(b).toBeLessThan(95)
  })

  it('returns a value between 0 and 360', () => {
    const b = bearing([10, 20], [30, 40])
    expect(b).toBeGreaterThanOrEqual(0)
    expect(b).toBeLessThan(360)
  })
})

describe('lineStringDistance', () => {
  it('returns 0 for a single point', () => {
    expect(lineStringDistance([[0, 0]])).toBe(0)
  })

  it('returns sum of segment distances', () => {
    const coords: [number, number][] = [[0, 0], [0, 1], [0, 2]]
    const total = lineStringDistance(coords)
    const seg1 = haversineDistance([0, 0], [0, 1])
    const seg2 = haversineDistance([0, 1], [0, 2])
    expect(total).toBeCloseTo(seg1 + seg2, 1)
  })
})
