/**
 * Per-layer opacity: what it means, which paint property carries it, and how to apply it
 * without destroying what the layer already paints.
 *
 * **Opacity here is a MULTIPLIER over each layer's authored paint, never an absolute value.**
 * `1` means "exactly as designed" and cannot regress anything. The code forces this: the
 * watersheds toggle paints its fill at 0.05 (a deliberate boundary wash) and its outline at
 * 0.8, so one absolute slider would need a different neutral position per style layer and
 * dragging it to 1 would drop an opaque blue sheet over the map. `published-fire-outlines` is
 * the sharper case -- its `circle-opacity` is deliberately 0 with the visible ring carried on
 * `circle-stroke-opacity`, so an absolute write would fill every fire circle with a second
 * opaque disc. A multiplier preserves an intentional zero (`0 * f = 0`) and composes with a
 * data-driven expression without ever having to understand it.
 *
 * No opacity BASE value is written in this module. Every base is read off the exported
 * `LayerSpecification` objects in `layers.ts`, the same "read it from the module that paints
 * it" rule `layer-legends.ts` enforces -- see src/components/map/AGENTS.md.
 */

import type { LayerSpecification } from "@maplibre/maplibre-gl-style-spec";
import { getLayers } from "@/lib/map/layers";
import { styleBackedLayerEntries, type LayerToggleId } from "@/lib/map/layer-registry";

/** "As authored". The only value that is guaranteed to change nothing. */
export const DEFAULT_LAYER_OPACITY = 1;

/**
 * The floor, and it is not 0 on purpose.
 *
 * `visibility: "none"` -- what the eye/switch sets -- excludes a layer from
 * `queryRenderedFeatures`. An opacity-0 layer is NOT excluded: it would still swallow the
 * "did the user click empty ground" check in MapView and still fire the fire/gauge/well
 * popups for features nobody can see. The switch is how a layer is turned off; the slider is
 * how it recedes.
 */
export const MIN_LAYER_OPACITY = 0.05;

/** Twenty stops across the range, which is finer than the eye reads on a basemap wash. */
export const LAYER_OPACITY_STEP = 0.05;

/** Clamps to the reachable range. Applied on write AND on persist-rehydrate. */
export function clampLayerOpacity(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_LAYER_OPACITY;
  if (value < MIN_LAYER_OPACITY) return MIN_LAYER_OPACITY;
  if (value > DEFAULT_LAYER_OPACITY) return DEFAULT_LAYER_OPACITY;
  return value;
}

/**
 * Which paint properties carry opacity, per MapLibre layer type.
 *
 * `hillshade` is absent deliberately: the MapLibre style spec defines no `hillshade-opacity`
 * (only exaggeration and the shadow/highlight colours), and MapView's `error` handler
 * hard-codes a PMTiles-expiry explanation, so a rejected paint property would surface as a
 * misleading basemap diagnosis. Omitting the type is what makes that unreachable.
 *
 * `symbol` lists both glyph properties: `weather-wind` is text-only (it sets no
 * `icon-image`), so `icon-opacity` is a silent no-op there rather than a mistake.
 */
export const OPACITY_PROPERTIES: Partial<Record<LayerSpecification["type"], readonly string[]>> =
  {
    fill: ["fill-opacity"],
    line: ["line-opacity"],
    circle: ["circle-opacity", "circle-stroke-opacity"],
    symbol: ["icon-opacity", "text-opacity"],
    raster: ["raster-opacity"],
    "fill-extrusion": ["fill-extrusion-opacity"],
    heatmap: ["heatmap-opacity"],
    background: ["background-opacity"],
  };

/**
 * Scale a paint value by a factor, preserving data-driven expressions.
 *
 * A number is multiplied in JS -- cheapest, and it sidesteps any question about literal
 * expressions in a non-data-driven context such as `raster-opacity`. Anything else is an
 * expression and gets wrapped in `["*"]`, which is legal for every property above:
 * fill/line/circle/text opacity are data-driven, and raster/heatmap/fill-extrusion opacity
 * are zoom-dependent -- a zoom `interpolate` wrapped in `"*"` stays zoom-dependent.
 *
 * Never branch on the expression's shape. The three production expressions this has to
 * survive are the two soil-field outlines' `["case", ["==", ["get","aggregated"], true], 0,
 * 0.25]` (the rule that stops isoband contours being stroked) and `buildings-3d`'s zoom
 * `interpolate` fade-in. A scalar write erases any of them permanently for the session,
 * because nothing re-adds a baked style layer except a basemap swap.
 */
export function scaleOpacityValue(base: unknown, factor: number): unknown {
  if (factor === DEFAULT_LAYER_OPACITY) return base;
  if (typeof base === "number") return base * factor;
  return ["*", base, factor];
}

/**
 * The value the style authored for a paint property, or 1 where it authored none.
 *
 * `LayerSpecification` is a union whose members each declare their own `paint` shape, so this
 * is the one place the widening cast lives rather than at every call site. Reading it here --
 * instead of restating a base -- is what keeps the multiplier and the paint from drifting.
 */
export function authoredPaintValue(spec: LayerSpecification, property: string): unknown {
  const paint = spec.paint as unknown as Record<string, unknown> | undefined;
  return paint?.[property] ?? DEFAULT_LAYER_OPACITY;
}

/** One style layer's opacity property, with the value the style authored for it. */
export interface StyleLayerOpacityTarget {
  toggleId: LayerToggleId;
  layerId: string;
  property: string;
  /**
   * The authored paint value. A property the style leaves unset resolves to the spec default
   * of 1 rather than `undefined`, so an outline the author never captioned still fades with
   * its fill instead of staying at full strength while the fill disappears.
   */
  base: unknown;
}

/**
 * Every (style layer, paint property) pair a registry toggle owns and no React component
 * writes -- "Lane A", the layers `LayerManager.applyOpacity` is the single writer for.
 *
 * Derived from `styleBackedLayerEntries()` cross-referenced against `getLayers()`, exactly as
 * `STYLE_LAYER_TOGGLE_MAP` is, so a new style-backed registry entry needs no edit here. That
 * derivation is also what keeps the applier away from every layer no toggle owns: the
 * basemap's `buildings-3d`/`hillshade`/roads/labels, ServiceAreaLayer's dimming mask and
 * QueryPointLayer's pin are chrome, not data, and none of them is in `styleLayerIds`.
 *
 * It equally cannot reach a component-added layer: `published-fire-outlines` (whose
 * `circle-opacity` is a deliberate 0), the soil-survey pair and the soil-field pair are never
 * baked into `getLayers()`, so the component that adds them stays their only writer.
 */
export function styleLayerOpacityTargets(): StyleLayerOpacityTarget[] {
  const specById = new Map(getLayers().map((layer) => [layer.id, layer]));
  const targets: StyleLayerOpacityTarget[] = [];

  for (const entry of styleBackedLayerEntries()) {
    for (const layerId of entry.styleLayerIds) {
      const spec = specById.get(layerId);
      if (spec === undefined) continue;
      const properties = OPACITY_PROPERTIES[spec.type];
      if (properties === undefined) continue;
      for (const property of properties) {
        targets.push({
          toggleId: entry.toggleId,
          layerId,
          property,
          base: authoredPaintValue(spec, property),
        });
      }
    }
  }

  return targets;
}
