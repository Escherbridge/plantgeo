"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import type { Map as MapLibreMap, GeoJSONSource } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import {
  soilFieldColorStops,
  SOIL_FIELD_ATTRIBUTION,
  type SoilFieldMeasure,
} from "@/lib/environmental/soil-field";
import { useStyleReady } from "@/components/map/layers/use-style-ready";
import { scaleOpacityValue } from "@/lib/map/layer-opacity";
import type { ExpressionSpecification } from "@/types/map";

/**
 * One ERA5-Land soil field -- volumetric water or temperature -- drawn from whatever
 * `environmental.getSoilField` served: the stored 0.25-degree cells at high zoom, or
 * dissolved isobands over a coarser aggregation lattice below it. One source and one fill
 * either way, because every served feature carries a `value` -- see
 * `src/components/map/AGENTS.md` §soil-field.
 *
 * Plain MapLibre `fill`, not deck.gl. The aggregation and the contouring already happened on
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

interface SoilFieldLayerIds {
  source: string;
  fill: string;
  outline: string;
}

function layerIdsFor(measure: SoilFieldMeasure): SoilFieldLayerIds {
  return {
    source: `soil-${measure}-field`,
    fill: `soil-${measure}-field-fill`,
    outline: `soil-${measure}-field-outline`,
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
 * Outlines are drawn ONLY on unaggregated cells, where they say something true: these are
 * discrete 0.25-degree samples. An isoband boundary is a contour through interpolated space,
 * and outlining it would draw a hard edge the data does not have.
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
  // Latest props behind a ref so the style.load handler re-attaches with current values.
  const propsRef = useRef({ geojson, fillOpacity, outlineOpacity });
  propsRef.current = { geojson, fillOpacity, outlineOpacity };
  const styleReady = useStyleReady(map);

  const addLayers = useCallback(
    (mapInstance: MapLibreMap) => {
      const {
        geojson: currentGeoJson,
        fillOpacity: currentFillOpacity,
        outlineOpacity: currentOutlineOpacity,
      } = propsRef.current;
      const beforeId = getFirstSymbolLayer(mapInstance);

      if (!mapInstance.getSource(ids.source)) {
        mapInstance.addSource(ids.source, {
          type: "geojson",
          data: currentGeoJson ?? EMPTY_COLLECTION,
          attribution: SOIL_FIELD_ATTRIBUTION,
        });
      }
      if (!mapInstance.getLayer(ids.fill)) {
        mapInstance.addLayer(
          {
            id: ids.fill,
            type: "fill",
            source: ids.source,
            paint: { "fill-color": fillColor, "fill-opacity": currentFillOpacity },
          },
          beforeId
        );
      }
      if (!mapInstance.getLayer(ids.outline)) {
        mapInstance.addLayer(
          {
            id: ids.outline,
            type: "line",
            source: ids.source,
            paint: {
              "line-color": "#3f3f46",
              "line-width": 0.5,
              "line-opacity": currentOutlineOpacity as ExpressionSpecification,
            },
          },
          beforeId
        );
      }
    },
    [ids, fillColor]
  );

  const removeLayers = useCallback(
    (mapInstance: MapLibreMap) => {
      safeRemoveLayerAndSource(mapInstance, [ids.outline, ids.fill], ids.source);
    },
    [ids]
  );

  // Persistent listener, never `once` alongside `on` -- see src/components/map/AGENTS.md
  // "Style.load listener order". This is what survives a basemap swap.
  useEffect(() => {
    if (!map) return;
    if (!visible) {
      removeLayers(map);
      return;
    }
    const onStyleLoad = () => addLayers(map);
    if (map.isStyleLoaded()) addLayers(map);
    map.on("style.load", onStyleLoad);
    return () => {
      map.off("style.load", onStyleLoad);
      removeLayers(map);
    };
  }, [map, visible, addLayers, removeLayers]);

  // The mount-time race the persistent listener above cannot catch: if the current style had
  // already finished loading when this component mounted, no further `style.load` arrives and
  // nothing retries. `styleReady` is a trigger, not a gate -- it can be a tick stale mid-render,
  // so the live `isStyleLoaded()` below is what decides. See AGENTS.md "Custom-added layers
  // need a retriggerable readiness signal". `addLayers` is idempotent, so the overlap with the
  // effect above is a no-op rather than a throw.
  useEffect(() => {
    if (!map || !visible) return;
    if (!map.isStyleLoaded()) return;
    addLayers(map);
  }, [map, visible, styleReady, addLayers]);

  useEffect(() => {
    if (!map || !visible) return;
    try {
      if (!map.getStyle()) return;
    } catch {
      return;
    }
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
  }, [map, ids, geojson, fillOpacity, outlineOpacity, visible]);

  return null;
}
