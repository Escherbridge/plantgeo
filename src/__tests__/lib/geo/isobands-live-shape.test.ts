import { describe, expect, it } from 'vitest'
import { buildIsobands, type FieldSample } from '@/lib/geo/isobands'
import { soilFieldMeasureDefinition } from '@/lib/environmental/soil-field'

const SOIL_MOISTURE_BAND_BREAKS = soilFieldMeasureDefinition('moisture').bandBreaks

/**
 * The coarse tier over real data, not a synthetic ramp.
 *
 * These are the 42 nodes `geo.soil_field` actually returned for the PNW viewport
 * (-125,41,-110,50) on 2026-04-30 at the 1-degree lattice with sigma 1.0 and a 2-cell
 * kernel, read from production on 2026-08-06. They carry the real spread the layer has to
 * survive -- a wet Coast Range at 0.34, the arid Snake River Plain at 0.09, and a gradient
 * between them that crosses five band edges -- which a monotonic fixture never exercises.
 *
 * The assertion that matters is the last one: the payload has to be smaller than the cells
 * it replaces, or the whole aggregation is pointless.
 */
const PNW_COARSE_NODES: FieldSample[] = (
  [
    [-124.5, 42.5, 0.3438], [-123.5, 42.5, 0.3516], [-122.5, 42.5, 0.3215],
    [-121.5, 42.5, 0.2425], [-120.5, 42.5, 0.1632], [-119.5, 42.5, 0.112],
    [-118.5, 42.5, 0.0894], [-117.5, 42.5, 0.0912], [-116.5, 42.5, 0.1197],
    [-115.5, 42.5, 0.1508], [-114.5, 42.5, 0.1685], [-113.5, 42.5, 0.1889],
    [-112.5, 42.5, 0.2368], [-111.5, 42.5, 0.2951],
    [-124.5, 43.5, 0.3372], [-123.5, 43.5, 0.341], [-122.5, 43.5, 0.3091],
    [-121.5, 43.5, 0.2336], [-120.5, 43.5, 0.1681], [-119.5, 43.5, 0.1405],
    [-118.5, 43.5, 0.1311], [-117.5, 43.5, 0.1364], [-116.5, 43.5, 0.1751],
    [-115.5, 43.5, 0.218], [-114.5, 43.5, 0.2315], [-113.5, 43.5, 0.2301],
    [-112.5, 43.5, 0.252], [-111.5, 43.5, 0.2964],
    [-124.5, 44.5, 0.3293], [-123.5, 44.5, 0.3293], [-122.5, 44.5, 0.2997],
    [-121.5, 44.5, 0.2367], [-120.5, 44.5, 0.1935], [-119.5, 44.5, 0.19],
    [-118.5, 44.5, 0.1914], [-117.5, 44.5, 0.1952], [-116.5, 44.5, 0.2361],
    [-115.5, 44.5, 0.2845], [-114.5, 44.5, 0.297], [-113.5, 44.5, 0.2839],
    [-112.5, 44.5, 0.2846], [-111.5, 44.5, 0.3114],
  ] as const
).map(([lon, lat, value]) => ({ lon, lat, value }))

/** Native 0.25-degree cells the detail tier would have shipped for the same viewport. */
const NATIVE_CELL_COUNT = 1_568
const VERTICES_PER_NATIVE_CELL = 5

describe('buildIsobands over the real PNW coarse lattice', () => {
  const bands = buildIsobands(PNW_COARSE_NODES, 1, [...SOIL_MOISTURE_BAND_BREAKS])

  it('produces at most one feature per band, all closed', () => {
    expect(bands.length).toBeGreaterThan(1)
    expect(bands.length).toBeLessThanOrEqual(SOIL_MOISTURE_BAND_BREAKS.length + 1)
    expect(new Set(bands.map((band) => band.bandIndex)).size).toBe(bands.length)
    for (const band of bands) {
      for (const polygon of band.polygons) {
        for (const ring of polygon) {
          expect(ring.length).toBeGreaterThanOrEqual(4)
          expect(ring[0]).toEqual(ring[ring.length - 1])
        }
      }
    }
  })

  it('classifies the arid basin and the wet coast into different bands', () => {
    const bandIndices = bands.map((band) => band.bandIndex)
    // 0.089 (Snake River Plain) is band 1 over these breaks; 0.352 (Coast Range) is band 7.
    expect(bandIndices).toContain(1)
    expect(bandIndices).toContain(7)
  })

  it('ships far less geometry than the native cells it replaces', () => {
    const vertexCount = bands.reduce(
      (total, band) =>
        total +
        band.polygons.reduce(
          (points, polygon) => points + polygon.reduce((n, ring) => n + ring.length, 0),
          0
        ),
      0
    )
    expect(vertexCount).toBeLessThan((NATIVE_CELL_COUNT * VERTICES_PER_NATIVE_CELL) / 10)
  })
})
