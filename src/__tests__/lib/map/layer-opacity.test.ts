import { describe, it, expect } from 'vitest'
import {
  DEFAULT_LAYER_OPACITY,
  LAYER_OPACITY_STEP,
  MIN_LAYER_OPACITY,
  OPACITY_PROPERTIES,
  clampLayerOpacity,
  scaleOpacityValue,
  styleLayerOpacityTargets,
} from '@/lib/map/layer-opacity'
import { getLayers } from '@/lib/map/layers'
import { LAYER_REGISTRY, styleBackedLayerEntries } from '@/lib/map/layer-registry'

/**
 * The one decision everything else follows from: opacity is a MULTIPLIER over each layer's
 * authored paint, so 1 is a no-op that cannot regress anything and an intentional zero
 * survives. `watersheds-fill` is authored at 0.05 (a boundary wash) while its own outline is
 * at 0.8, so an absolute slider would need a different neutral position per style layer --
 * these cases pin the multiplier semantics rather than any particular value.
 */
describe('scaleOpacityValue', () => {
  it('is the identity at a factor of 1, for numbers and expressions alike', () => {
    const expression = ['case', ['==', ['get', 'aggregated'], true], 0, 0.25]
    expect(scaleOpacityValue(0.05, DEFAULT_LAYER_OPACITY)).toBe(0.05)
    // The SAME object, not an equal one: a factor of 1 must not rewrite an expression at all.
    expect(scaleOpacityValue(expression, DEFAULT_LAYER_OPACITY)).toBe(expression)
  })

  it('multiplies a scalar in JS rather than emitting an expression', () => {
    expect(scaleOpacityValue(0.5, 0.5)).toBe(0.25)
    expect(scaleOpacityValue(1, 0.4)).toBe(0.4)
  })

  /**
   * The critical case. `soil-moisture-field-outline` and `soil-temperature-field-outline`
   * carry `["case", ["==", ["get","aggregated"], true], 0, 0.25]`, the rule that stops
   * isoband contours being stroked. A scalar write would erase it permanently for the
   * session, because nothing re-adds a layer except a basemap swap.
   */
  it('wraps a data-driven expression instead of replacing it', () => {
    const outlineOpacity = ['case', ['==', ['get', 'aggregated'], true], 0, 0.25]

    const scaled = scaleOpacityValue(outlineOpacity, 0.5)

    expect(scaled).toEqual(['*', outlineOpacity, 0.5])
    // Every arm of the original survives verbatim, including the 0 that suppresses the
    // stroke: 0 * f is still 0, so the aggregated-cell rule is preserved exactly.
    expect((scaled as unknown[])[1]).toBe(outlineOpacity)
  })

  it('keeps a zoom interpolate zoom-dependent', () => {
    const fade = ['interpolate', ['linear'], ['zoom'], 14, 0, 16, 0.85]

    expect(scaleOpacityValue(fade, 0.6)).toEqual(['*', fade, 0.6])
  })
})

describe('clampLayerOpacity', () => {
  // An opacity-0 layer is NOT excluded from queryRenderedFeatures the way a
  // visibility:"none" layer is, so it would still swallow clicks meant for the ground beneath
  // it and still fire the fire/gauge/well popups for features nobody can see. The switch is
  // how a layer is turned off; the slider only makes it recede.
  it('never lets a layer reach zero', () => {
    expect(clampLayerOpacity(0)).toBe(MIN_LAYER_OPACITY)
    expect(clampLayerOpacity(-3)).toBe(MIN_LAYER_OPACITY)
    expect(MIN_LAYER_OPACITY).toBeGreaterThan(0)
  })

  it('caps at "as authored" and rejects a value MapLibre would refuse', () => {
    expect(clampLayerOpacity(50)).toBe(DEFAULT_LAYER_OPACITY)
    expect(clampLayerOpacity(Number.NaN)).toBe(DEFAULT_LAYER_OPACITY)
    expect(clampLayerOpacity(0.6)).toBe(0.6)
  })

  // The range has to land ON the floor and ON 1, or the slider's last step is unreachable and
  // the thumb cannot be driven to either end with the arrow keys.
  it('offers a step the whole range divides into a whole number of times', () => {
    const steps = (DEFAULT_LAYER_OPACITY - MIN_LAYER_OPACITY) / LAYER_OPACITY_STEP
    expect(steps).toBeCloseTo(Math.round(steps), 6)
    expect(MIN_LAYER_OPACITY).toBeLessThan(DEFAULT_LAYER_OPACITY)
  })
})

/**
 * The property list is per layer TYPE, and getting it wrong is not a silent no-op: MapView
 * registers an `error` handler whose message hard-codes a PMTiles-expiry explanation, so a
 * paint property MapLibre rejects surfaces as a misleading basemap diagnosis.
 */
describe('OPACITY_PROPERTIES', () => {
  it('maps each drawable type to the properties that carry its opacity', () => {
    expect(OPACITY_PROPERTIES.fill).toEqual(['fill-opacity'])
    expect(OPACITY_PROPERTIES.line).toEqual(['line-opacity'])
    expect(OPACITY_PROPERTIES.circle).toEqual(['circle-opacity', 'circle-stroke-opacity'])
    expect(OPACITY_PROPERTIES.symbol).toEqual(['icon-opacity', 'text-opacity'])
    expect(OPACITY_PROPERTIES.raster).toEqual(['raster-opacity'])
    expect(OPACITY_PROPERTIES['fill-extrusion']).toEqual(['fill-extrusion-opacity'])
    expect(OPACITY_PROPERTIES.heatmap).toEqual(['heatmap-opacity'])
  })

  // The MapLibre style spec defines no hillshade-opacity -- only exaggeration and the
  // shadow/highlight colours. Omitting the type is what makes the always-on hillshade
  // unreachable by any applier rather than merely un-targeted by today's registry.
  it('has no entry for hillshade, which the style spec gives no opacity property', () => {
    expect(OPACITY_PROPERTIES).not.toHaveProperty('hillshade')
  })
})

describe('styleLayerOpacityTargets', () => {
  const targets = styleLayerOpacityTargets()

  it('names a real style layer and a property valid for that layer type, every time', () => {
    const specById = new Map(getLayers().map((layer) => [layer.id, layer]))

    expect(targets.length).toBeGreaterThan(0)
    for (const target of targets) {
      const spec = specById.get(target.layerId)
      expect(spec, `${target.toggleId} -> ${target.layerId}`).toBeDefined()
      expect(OPACITY_PROPERTIES[spec!.type]).toContain(target.property)
    }
  })

  it('covers every style layer id the registry gives a style-backed toggle', () => {
    const covered = new Set(targets.map((target) => target.layerId))
    for (const entry of styleBackedLayerEntries()) {
      for (const layerId of entry.styleLayerIds) {
        expect(covered, `${entry.toggleId} -> ${layerId}`).toContain(layerId)
      }
    }
  })

  it('attributes each target to the toggle that owns it', () => {
    for (const target of targets) {
      expect(LAYER_REGISTRY[target.toggleId].styleLayerIds).toContain(target.layerId)
    }
  })

  /**
   * Lane separation, asserted structurally rather than by a hand-kept denylist.
   *
   * `published-fire-outlines` paints `circle-opacity: 0` deliberately and carries its visible
   * ring on `circle-stroke-opacity`; the two soil-field outlines carry the isoband expression.
   * None of them is baked into getLayers(), so the component that adds them stays their only
   * writer and this applier cannot reach them however it is called.
   */
  it('cannot reach a component-added layer', () => {
    const reachable = new Set(targets.map((target) => target.layerId))
    for (const layerId of [
      'published-fire-circles',
      'published-fire-outlines',
      'soil-moisture-field-outline',
      'soil-temperature-field-outline',
      'soil-vpd-field-outline',
      'soil-survey-fill',
      'soil-survey-summary',
      'ndvi-overlay-layer',
      'soilgrids-layer',
      'demand-heatmap-layer',
      'weather-wind',
      'weather-temperature',
    ]) {
      expect(reachable, layerId).not.toContain(layerId)
    }
  })

  // Basemap decoration and map chrome belong to no toggle. buildings-3d in particular carries
  // a zoom-interpolate fade-in a scalar write would erase, and the service-area mask sits
  // under the data pins by design.
  it('cannot reach basemap decoration or map chrome', () => {
    const reachable = new Set(targets.map((target) => target.layerId))
    for (const layerId of [
      'buildings-3d',
      'hillshade',
      'osm-roads',
      'osm-waterways',
      'ingestion-coverage-mask',
      'map-query-point-halo',
    ]) {
      expect(reachable, layerId).not.toContain(layerId)
    }
  })

  it('reads each base off the authored paint rather than restating one', () => {
    const baseOf = (layerId: string, property: string) =>
      targets.find((target) => target.layerId === layerId && target.property === property)?.base

    // The watershed pair is the argument for multiplier semantics in one toggle: a 0.05
    // boundary wash and a 0.8 outline, both correct, with no single absolute neutral. The
    // gap widened on 2026-08-08 when the outline was strengthened and the fill was not.
    expect(baseOf('watersheds-fill', 'fill-opacity')).toBe(0.05)
    expect(baseOf('watersheds-outline', 'line-opacity')).toBe(0.8)
    expect(baseOf('fire-perimeters', 'fill-opacity')).toBe(0.5)
    expect(baseOf('evacuation-zones', 'fill-opacity')).toBe(0.45)
    expect(baseOf('burn-severity-outline', 'line-opacity')).toBe(0.8)
    expect(baseOf('interventions-points', 'circle-opacity')).toBe(0.9)
    expect(baseOf('building-footprints', 'fill-extrusion-opacity')).toBe(0.75)
  })

  // An outline the style leaves unset draws at 1. Defaulting to `undefined` instead would
  // either skip it -- leaving a hard border at full strength over a fill that has faded to
  // nothing -- or feed `["*", undefined, f]` to MapLibre.
  it('treats an unset property as the spec default of 1 so it still fades', () => {
    const unset = targets.find(
      (target) =>
        target.layerId === 'fire-perimeters-outline' && target.property === 'line-opacity'
    )
    expect(unset?.base).toBe(DEFAULT_LAYER_OPACITY)
    expect(scaleOpacityValue(unset?.base, 0.5)).toBe(0.5)
  })
})
