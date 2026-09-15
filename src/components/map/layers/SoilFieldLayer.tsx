"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import type { Map as MapLibreMap, GeoJSONSource } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import {
  soilFieldColorStops,
  soilFieldMeasureDefinition,
  SOIL_FIELD_ATTRIBUTION,
  type SoilFieldMeasure,
} from "@/lib/environmental/soil-field";
import { scaleOpacityValue } from "@/lib/map/layer-opacity";
import type { ExpressionSpecification } from "@/types/map";
import { measuredValueLabelLayer } from "@/lib/map/measured-value-label";

/**
 * One ERA5-Land soil field -- volumetric water or temperature -- drawn from whatever
 * `environmental.getSoilField` served: a COMPLETE tessellation of the rung's own lattice at
 * every zoom, quarter-degree cells at z13/z9/z5 and five-degree cells at z0. One source and one
 * fill either way, because every served feature carries a `value` -- see
 * `src/components/map/AGENTS.md` §soil-field.
 *
 * Plain MapLibre `fill`, not deck.gl. The aggregation and the tessellation already happened on
 * the server, so what reaches the browser is a handful of polygons; a `ContourLayer` would
 * mean re-deriving them client-side from data the server deliberately does not ship.
 *
 * Ids are per-measure so both fields can be switched on at once without one clearing the
 * other's source.
 */
const EMPTY_COLLECTION: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

/**
 * Is there a PARSED style to add to?
 *
 * MapLibre v5's `Map.getStyle()` returns `undefined` before the Style object exists and
 * `Style.serialize()` itself returns `undefined` while `_loaded` is false -- neither throws, so a
 * bare call would in fact be safe (maplibre-gl/dist/maplibre-gl-dev.js, `getStyle()` and
 * `Style.serialize()`). It is wrapped anyway, in ONE place used by every gate in this file, because
 * serialize() walks live source/terrain objects and a map torn down under a pending effect is the
 * one case where that walk is not ours to reason about. Same helper on both gates: this file used
 * to guard the data effect and not the admission effect, which read as a disagreement about
 * whether getStyle() can throw.
 */
function hasParsedStyle(mapInstance: MapLibreMap): boolean {
  try {
    return Boolean(mapInstance.getStyle());
  } catch {
    return false;
  }
}

interface SoilFieldLayerIds {
  source: string;
  fill: string;
  outline: string;
  label: string;
}

function layerIdsFor(measure: SoilFieldMeasure): SoilFieldLayerIds {
  return {
    source: `soil-${measure}-field`,
    fill: `soil-${measure}-field-fill`,
    outline: `soil-${measure}-field-outline`,
    label: `soil-${measure}-field-value-labels`,
  };
}

/**
 * Interpolated over the measure's own band table, so a fill and the panel's legend cannot
 * disagree about what a value looks like. Derived rather than restated, which is why the
 * assertion is needed: MapLibre types an expression as a union of fixed-length tuples and a
 * spread widens it -- the same trade `NDVI_CELL_FILL_COLOR` makes in VegetationLayer.
 */
function fillColorFor(measure: SoilFieldMeasure): ExpressionSpecification {
  return [
    "interpolate",
    ["linear"],
    ["get", "value"],
    ...soilFieldColorStops(measure),
  ] as unknown as ExpressionSpecification;
}

/**
 * Outlines are drawn ONLY on unaggregated cells, and `aggregated` is now read straight off the
 * server's declared rung rather than off a null cell id (`getParquetSoilField`).
 *
 * The rule survives the move from isobands to a complete tessellation, and for a sharper reason
 * than before: every rung now fills the whole viewport, so a stroke on every cell of a coarse
 * rung draws a mesh of block seams across it -- the defect the 2026-09-01 assessment recorded as
 * "nested ERA5 soil blocks with visible seams". At the detail rung the stroke still says
 * something true, that these are discrete quarter-degree samples.
 */
const OUTLINE_OPACITY = [
  "case",
  ["==", ["get", "aggregated"], true],
  0,
  0.25,
] as unknown as ExpressionSpecification;

interface SoilFieldLayerProps {
  map: MapLibreMap | null;
  measure: SoilFieldMeasure;
  /**
   * The served collection. Empty -- never null -- when the layer is switched off or the
   * viewport holds none, so `setData` has something to clear with.
   */
  geojson?: GeoJSON.FeatureCollection | null;
  /** The fill's authored strength. The design value, not a control. */
  opacity?: number;
  /**
   * The reader's MULTIPLIER, per measure. `soil-moisture` and `soil-temperature` are separate
   * registry toggles and each instance of this component gets its own scalar -- they shared
   * one `soil-store.opacity` until 2026-08-08, which made per-layer opacity impossible for
   * them and coupled both to the SoilGrids raster besides.
   */
  opacityScale?: number;
  visible?: boolean;
}

export function SoilFieldLayer({
  map,
  measure,
  geojson = null,
  opacity = 0.7,
  opacityScale = 1,
  visible = true,
}: SoilFieldLayerProps) {
  const ids = useMemo(() => layerIdsFor(measure), [measure]);
  const fillColor = useMemo(() => fillColorFor(measure), [measure]);
  const fillOpacity = opacity * opacityScale;
  const labelOpacity = scaleOpacityValue(1, opacityScale) as number;
  /**
   * The one place in production where the expression path is exercised.
   *
   * `OUTLINE_OPACITY` is `["case", ["==", ["get","aggregated"], true], 0, 0.25]` -- the rule
   * that stops isoband contours being stroked. A scalar write would erase it permanently for
   * the session; wrapping it as `["*", <case>, factor]` keeps every arm, and `0 * f = 0`
   * preserves the aggregated-cell zero exactly.
   */
  const outlineOpacity = useMemo(
    () => scaleOpacityValue(OUTLINE_OPACITY, opacityScale),
    [opacityScale]
  );
  /**
   * Latest props behind a ref so the style.load handler re-attaches with current values.
   *
   * `ids`, `fillColor` and `measure` are in here for a STRUCTURAL reason, not a cosmetic one:
   * they are what the callbacks below would otherwise close over, and a closed-over value is a
   * callback dependency, and a callback dependency is a dependency of the `style.load` effect --
   * which would then tear the listener down and re-register it at the BACK of the queue on every
   * measure change. MapLibre stacks later-added layers above earlier ones sharing a `beforeId`,
   * so that re-registration silently inverts this field against every other layer. Reading them
   * through the ref is what lets both callbacks be `useCallback(..., [])`.
   */
  const propsRef = useRef({
    geojson,
    fillOpacity,
    outlineOpacity,
    labelOpacity,
    visible,
    ids,
    fillColor,
    measure,
  });
  propsRef.current = {
    geojson,
    fillOpacity,
    outlineOpacity,
    labelOpacity,
    visible,
    ids,
    fillColor,
    measure,
  };

  const addLayers = useCallback((mapInstance: MapLibreMap) => {
    const {
      geojson: currentGeoJson,
      fillOpacity: currentFillOpacity,
      outlineOpacity: currentOutlineOpacity,
      labelOpacity: currentLabelOpacity,
      ids: currentIds,
      fillColor: currentFillColor,
      measure: currentMeasure,
    } = propsRef.current;
    const beforeId = getFirstSymbolLayer(mapInstance);

    if (!mapInstance.getSource(currentIds.source)) {
      mapInstance.addSource(currentIds.source, {
        type: "geojson",
        data: currentGeoJson ?? EMPTY_COLLECTION,
        attribution: SOIL_FIELD_ATTRIBUTION,
      });
    }
    if (!mapInstance.getLayer(currentIds.fill)) {
      mapInstance.addLayer(
        {
          id: currentIds.fill,
          type: "fill",
          source: currentIds.source,
          paint: { "fill-color": currentFillColor, "fill-opacity": currentFillOpacity },
        },
        beforeId
      );
    }
    if (!mapInstance.getLayer(currentIds.outline)) {
      mapInstance.addLayer(
        {
          id: currentIds.outline,
          type: "line",
          source: currentIds.source,
          paint: {
            "line-color": "#3f3f46",
            "line-width": 0.5,
            "line-opacity": currentOutlineOpacity as ExpressionSpecification,
          },
        },
        beforeId
      );
    }
    if (!mapInstance.getLayer(currentIds.label)) {
      mapInstance.addLayer(measuredValueLabelLayer({
        id: currentIds.label,
        source: currentIds.source,
        unit: soilFieldMeasureDefinition(currentMeasure).unitLabel,
        fractionDigits: currentMeasure === "moisture" ? 3 : currentMeasure === "vpd" ? 2 : 1,
        opacity: currentLabelOpacity,
      }), beforeId);
    }
  }, []);

  const removeLayers = useCallback((mapInstance: MapLibreMap) => {
    const { ids: currentIds } = propsRef.current;
    safeRemoveLayerAndSource(
      mapInstance,
      [currentIds.label, currentIds.outline, currentIds.fill],
      currentIds.source
    );
  }, []);

  // Persistent listener, never `once` alongside `on` -- see src/components/map/AGENTS.md
  // "Style.load listener order". This is what survives a basemap swap, and it registers ONCE per
  // map: `addLayers`/`removeLayers` carry EMPTY dep arrays, so the only thing that can re-run this
  // effect is the map identity itself. Registration order is load-bearing for stacking -- every
  // value that changes (visibility, the served collection, the ids, the ramp, the measure) is read
  // off propsRef at fire time rather than closed over, because closing over any of them would put
  // it in a dep array and re-register this handler behind every other layer's.
  useEffect(() => {
    if (!map) return;

    const onStyleLoad = () => {
      if (propsRef.current.visible) addLayers(map);
    };
    map.on("style.load", onStyleLoad);

    return () => {
      map.off("style.load", onStyleLoad);
      removeLayers(map);
    };
  }, [map, addLayers, removeLayers]);

  // A PARSED style admits addSource/addLayer -- it does not have to be a LOADED one. The old
  // gate here was `isStyleLoaded()`, which stays false until every unrelated source's tiles
  // land, so a late mount (after `style.load` had already fired) installed nothing and then
  // only ever saw `sourcedata`, whose handler updates sources that already exist. `getStyle()`
  // truthy is the real precondition; a genuinely unparsed style falls through to `style.load`
  // above, and `addLayers` is idempotent so the two paths cannot collide.
  //
  // `ids` is a dependency because a measure change swaps EVERY id -- nothing can be repainted
  // into place -- so the cleanup tears the previous measure's ids down (captured, not read off
  // propsRef, which by cleanup time already holds the new ones) and the body rebuilds.
  useEffect(() => {
    if (!map) return;
    if (!visible) {
      removeLayers(map);
      return;
    }
    if (hasParsedStyle(map)) addLayers(map);
    return () => {
      safeRemoveLayerAndSource(map, [ids.label, ids.outline, ids.fill], ids.source);
    };
  }, [map, visible, ids, addLayers, removeLayers]);

  useEffect(() => {
    if (!map || !visible) return;
    if (!hasParsedStyle(map)) return;
    // setData rather than a re-add, so panning or a new day swaps the field without tearing
    // the source down under the map. A missing source here is the legitimate first-pass case:
    // the style had not loaded, and addLayers creates it from propsRef with this same data.
    const source = map.getSource(ids.source) as GeoJSONSource | undefined;
    if (source) source.setData(geojson ?? EMPTY_COLLECTION);
    if (map.getLayer(ids.fill)) {
      map.setPaintProperty(ids.fill, "fill-opacity", fillOpacity);
    }
    if (map.getLayer(ids.outline)) {
      map.setPaintProperty(ids.outline, "line-opacity", outlineOpacity);
    }
    if (map.getLayer(ids.label)) {
      map.setPaintProperty(ids.label, "text-opacity", labelOpacity);
    }
  }, [map, ids, geojson, fillOpacity, outlineOpacity, labelOpacity, visible]);

  return null;
}
