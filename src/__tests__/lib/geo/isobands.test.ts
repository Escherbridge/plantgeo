import { describe, expect, it } from 'vitest'
import { buildIsobands, type FieldSample } from '@/lib/geo/isobands'

/**
 * The dissolve is the load-bearing part: without it the "smoothed field" is thousands of
 * clipped triangles, which is more geometry than the discrete cells it replaces. These
 * cases pin the two properties that make it worth doing -- the output is a small number of
 * closed rings, and it covers exactly the ground the samples do.
 */

/** A `columns x rows` lattice whose value is produced by `valueAt(column, row)`. */
function lattice(
  columns: number,
  rows: number,
  step: number,
  valueAt: (column: number, row: number) => number
): FieldSample[] {
  const samples: FieldSample[] = []
  for (let column = 0; column < columns; column += 1) {
    for (let row = 0; row < rows; row += 1) {
      samples.push({ lon: column * step, lat: row * step, value: valueAt(column, row) })
    }
  }
  return samples
}

/** Shoelace area of one closed ring. */
function ringArea(ring: number[][]): number {
  let total = 0
  for (let position = 0; position < ring.length - 1; position += 1) {
    const [currentLon, currentLat] = ring[position]
    const [nextLon, nextLat] = ring[position + 1]
    total += currentLon * nextLat - nextLon * currentLat
  }
  return total / 2
}

/** Net area of a polygon: exterior ring minus its holes. */
function polygonArea(polygon: number[][][]): number {
  return polygon.reduce(
    (total, ring, index) => (index === 0 ? ringArea(ring) : total + ringArea(ring)),
    0
  )
}

function totalArea(polygons: number[][][][]): number {
  return polygons.reduce((total, polygon) => total + polygonArea(polygon), 0)
}

function isClosed(ring: number[][]): boolean {
  const first = ring[0]
  const last = ring[ring.length - 1]
  return first[0] === last[0] && first[1] === last[1]
}

describe('buildIsobands', () => {
  it('returns nothing for an empty or single-node lattice', () => {
    expect(buildIsobands([], 1, [0.5])).toEqual([])
    expect(buildIsobands([{ lon: 0, lat: 0, value: 1 }], 1, [0.5])).toEqual([])
  })

  it('puts a uniform field in exactly one band, as one dissolved rectangle', () => {
    const samples = lattice(4, 3, 1, () => 0.22)
    const bands = buildIsobands(samples, 1, [0.05, 0.1, 0.15, 0.2, 0.25, 0.3])

    expect(bands).toHaveLength(1)
    // 0.22 falls in [0.20, 0.25), which is band index 4 over these six breaks.
    expect(bands[0].bandIndex).toBe(4)
    expect(bands[0].minimum).toBe(0.2)
    expect(bands[0].maximum).toBe(0.25)
    // The 24 clipped triangles of a 3x2 square grid dissolve to ONE ring. Anything more
    // means the edge cancellation failed and the payload carries the pieces.
    expect(bands[0].polygons).toHaveLength(1)
    expect(bands[0].polygons[0]).toHaveLength(1)
    expect(bands[0].polygons[0][0]).toHaveLength(5)
    expect(totalArea(bands[0].polygons)).toBeCloseTo(3 * 2, 9)
  })

  it('splits a linear ramp into contiguous bands that tile the lattice exactly', () => {
    // Value rises 0 -> 1 west to east, so every band is a vertical stripe.
    const columns = 6
    const samples = lattice(columns, 4, 1, (column) => column / (columns - 1))
    const breaks = [0.25, 0.5, 0.75]
    const bands = buildIsobands(samples, 1, breaks)

    expect(bands.map((band) => band.bandIndex)).toEqual([0, 1, 2, 3])
    for (const band of bands) {
      for (const polygon of band.polygons) {
        for (const ring of polygon) expect(isClosed(ring)).toBe(true)
      }
      // A stripe of a monotonic ramp is convex and simply connected: one ring, no holes.
      expect(band.polygons).toHaveLength(1)
      expect(band.polygons[0]).toHaveLength(1)
    }
    // The bands partition the lattice: no gap, no double cover.
    const covered = bands.reduce((total, band) => total + totalArea(band.polygons), 0)
    expect(covered).toBeCloseTo((columns - 1) * 3, 9)
  })

  it('cuts a hole when one band encloses another, rather than filling over it', () => {
    // A wet bullseye in a dry field: the dry band must come back with a hole in it.
    const samples = lattice(5, 5, 1, (column, row) =>
      column === 2 && row === 2 ? 0.9 : 0.1
    )
    const bands = buildIsobands(samples, 1, [0.5])

    expect(bands.map((band) => band.bandIndex)).toEqual([0, 1])
    const lowBand = bands[0]
    const highBand = bands[1]
    // One exterior ring plus one interior ring is the whole assertion: a hole that failed
    // to nest would show up as two separate polygons instead.
    expect(lowBand.polygons).toHaveLength(1)
    expect(lowBand.polygons[0]).toHaveLength(2)
    expect(ringArea(lowBand.polygons[0][0])).toBeGreaterThan(0)
    expect(ringArea(lowBand.polygons[0][1])).toBeLessThan(0)
    // The hole is exactly the high band's footprint, so the two tile the lattice.
    expect(totalArea(lowBand.polygons) + totalArea(highBand.polygons)).toBeCloseTo(16, 9)
  })

  it('skips a lattice square with a missing corner instead of interpolating across it', () => {
    const samples = lattice(3, 3, 1, () => 0.3).filter(
      (sample) => !(sample.lon === 2 && sample.lat === 2)
    )
    const bands = buildIsobands(samples, 1, [0.1])

    expect(bands).toHaveLength(1)
    // Three of the four unit squares survive; the fourth has no fourth corner to close it.
    expect(totalArea(bands[0].polygons)).toBeCloseTo(3, 9)
  })

  it('classifies into the open tails rather than dropping out-of-range samples', () => {
    const samples = lattice(3, 2, 0.5, () => 9)
    const bands = buildIsobands(samples, 0.5, [0.1, 0.2])

    expect(bands).toHaveLength(1)
    expect(bands[0].bandIndex).toBe(2)
    expect(bands[0].minimum).toBe(0.2)
    expect(bands[0].maximum).toBeNull()
  })

  it('sorts unsorted breaks rather than emitting reversed bands', () => {
    const samples = lattice(3, 3, 1, (column) => column * 0.4)
    const ascending = buildIsobands(samples, 1, [0.2, 0.6])
    const shuffled = buildIsobands(samples, 1, [0.6, 0.2])
    expect(shuffled).toEqual(ascending)
  })

  /**
   * THE DISSOLVE, stated as the property a renderer depends on: no segment of the output is
   * traversed twice.
   *
   * A surviving interior edge is what draws a seam. The clipped triangles are vertex-conforming,
   * so every edge two of them share is walked once in each direction and cancels exactly; a
   * duplicate in the output means the cancellation missed a pair, and the band is drawn as its
   * pieces with hairlines between them -- which on the map is indistinguishable from the
   * batch-boundary seams this track exists to remove.
   *
   * Compared UNORDERED (`a|b` against `b|a`) rather than as directed edges: two adjacent bands
   * legitimately share a boundary walked in opposite directions, and only a repeat WITHIN one
   * band's own rings is a failed dissolve.
   */
  it('leaves no boundary segment traversed twice within a dissolved band', () => {
    // A bullseye: one band encloses another, so the low band comes back with a hole and the two
    // share a boundary -- the case with the most chances for an uncancelled interior edge.
    const samples = lattice(6, 6, 1, (column, row) =>
      column >= 2 && column <= 3 && row >= 2 && row <= 3 ? 0.9 : 0.1
    )
    const bands = buildIsobands(samples, 1, [0.5])

    expect(bands).toHaveLength(2)
    for (const band of bands) {
      const seen = new Set<string>()
      for (const polygon of band.polygons) {
        for (const ring of polygon) {
          for (let position = 0; position < ring.length - 1; position += 1) {
            const [fromLon, fromLat] = ring[position]
            const [toLon, toLat] = ring[position + 1]
            const from = `${fromLon},${fromLat}`
            const to = `${toLon},${toLat}`
            const key = from < to ? `${from}|${to}` : `${to}|${from}`
            expect(seen.has(key)).toBe(false)
            seen.add(key)
          }
        }
      }
    }
  })

  /**
   * The step is the SERVED rung's pitch, and handing in the wrong one is silent. `buildIsobands`
   * derives grid indices by dividing by the step, so a lattice read at the wrong pitch has no
   * complete squares at all: every one fails its four-corner test and the band comes back empty.
   * That is precisely the failure the presentation avoids by reading the pitch off the tier table
   * rather than pinning the detail lattice's.
   */
  it('finds no square at all when handed a pitch the lattice is not on', () => {
    const samples = lattice(4, 4, 1, () => 0.3)

    expect(buildIsobands(samples, 1, [0.1])).toHaveLength(1)
    expect(buildIsobands(samples, 0.25, [0.1])).toEqual([])
  })
})
