"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import type { Map as MapLibreMap, GeoJSONSource } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import {
  climateFieldColorStops,
  CLIMATE_FIELD_ATTRIBUTION,
  type ClimateFieldSignalId,
} from "@/lib/environmental/climate-field";
import { useStyleReady } from "@/components/map/layers/use-style-ready";
import { scaleOpacityValue } from "@/lib/map/layer-opacity";
import type { ExpressionSpecification } from "@/types/map";

/**
 * The NASA POWER climate field, drawn from whatever `environmental.getClimateField` served:
 * the stored 0.5-degree cells, always, because that lane has one serving tier -- see
 * `src/components/map/AGENTS.md` §climate-field.
 *
 * Plain MapLibre `fill`, not deck.gl, for the same reason `SoilFieldLayer` is: what reaches
 * the browser is at most 397 polygons the server already resolved, and the aggregation layers
 * a `ContourLayer` would need are not a dependency.
 *
 * ONE instance with a `signal` prop rather than nine mounted layers: the nine signals are one
 * question asked nine ways, only one is drawn at a time, and nine sources would each hold a
 * stale copy of the same lattice.
 */
const EMPTY_COLLECTION: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

const SOURCE_ID = "climate-field";
const FILL_LAYER_ID = "climate-field-fill";
const OUTLINE_LAYER_ID = "climate-field-outline";

/**
 * Interpolated over the selected signal's own band table, so a fill and the panel's legend
 * cannot disagree about what a value looks like. Derived rather than restated, which is why
 * the assertion is needed: MapLibre types an expression as a union of fixed-length tuples and
 * a spread widens it -- the same trade `SoilFieldLayer` makes.
 */
function fillColorFor(signal: ClimateFieldSignalId): ExpressionSpecification {
  return [
    "interpolate",
    ["linear"],
    ["get", "value"],
    ...climateFieldColorStops(signal),
  ] as unknown as ExpressionSpecification;
}

/**
 * A scalar, where `SoilFieldLayer`'s is a `case` expression: every feature this lane serves is
 * an unaggregated 0.5-degree cell, so the outline always says something true and there is no
 * isoband contour to suppress. Low, because a 397-cell lattice outlined at full strength reads
 * as a grid rather than as a field.
 */
const OUTLINE_OPACITY = 0.2;

interface ClimateFieldLayerProps {
  map: MapLibreMap | null;
  /** Which quantity is drawn. Switching it repaints in place; the source is not torn down. */
  signal: ClimateFieldSignalId;
  /**
   * The served collection. Empty -- never null -- when the layer is switched off or the
   * viewport holds none, so `setData` has something to clear with.
   */
  geojson?: GeoJSON.FeatureCollection | null;
  /** The fill's authored strength. The design value, not a control. */
  opacity?: number;
  /** The reader's MULTIPLIER for `climate-field`, from `layer-store.layerOpacity`. */
  opacityScale?: number;
  visible?: boolean;
}

export function ClimateFieldLayer({
  map,
  signal,
  geojson = null,
  opacity = 0.7,
  opacityScale = 1,
  visible = true,
}: ClimateFieldLayerProps) {
  const fillColor = useMemo(() => fillColorFor(signal), [signal]);
  // Both opacities go through the shared helper, even though both bases are plain numbers:
  // the multiplier rule then has ONE implementation rather than an inline product here and
  // the real rule in `layer-opacity.ts`. For a number the helper is exactly `base * factor`,
  // so this costs nothing and cannot drift from what the style-baked layers get.
  const fillOpacity = scaleOpacityValue(opacity, opacityScale) as number;
  const outlineOpacity = scaleOpacityValue(OUTLINE_OPACITY, opacityScale) as number;
  // Latest props behind a ref so the style.load handler re-attaches with current values.
  const propsRef = useRef({ geojson, fillColor, fillOpacity, outlineOpacity });
  propsRef.current = { geojson, fillColor, fillOpacity, outlineOpacity };
  const styleReady = useStyleReady(map);

  const addLayers = useCallback((mapInstance: MapLibreMap) => {
    const {
      geojson: currentGeoJson,
      fillColor: currentFillColor,
      fillOpacity: currentFillOpacity,
      outlineOpacity: currentOutlineOpacity,
    } = propsRef.current;
    const beforeId = getFirstSymbolLayer(mapInstance);

    if (!mapInstance.getSource(SOURCE_ID)) {
      mapInstance.addSource(SOURCE_ID, {
        type: "geojson",
        data: currentGeoJson ?? EMPTY_COLLECTION,
        attribution: CLIMATE_FIELD_ATTRIBUTION,
      });
    }
    if (!mapInstance.getLayer(FILL_LAYER_ID)) {
      mapInstance.addLayer(
        {
          id: FILL_LAYER_ID,
          type: "fill",
          source: SOURCE_ID,
          paint: { "fill-color": currentFillColor, "fill-opacity": currentFillOpacity },
        },
        beforeId
      );
    }
    if (!mapInstance.getLayer(OUTLINE_LAYER_ID)) {
      mapInstance.addLayer(
        {
          id: OUTLINE_LAYER_ID,
          type: "line",
          source: SOURCE_ID,
          paint: {
            "line-color": "#3f3f46",
            "line-width": 0.5,
            "line-opacity": currentOutlineOpacity,
          },
        },
        beforeId
      );
    }
  }, []);

  const removeLayers = useCallback((mapInstance: MapLibreMap) => {
    safeRemoveLayerAndSource(mapInstance, [OUTLINE_LAYER_ID, FILL_LAYER_ID], SOURCE_ID);
  }, []);

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
  // nothing retries. `styleReady` is a trigger, not a gate -- it can be a tick stale
  // mid-render, so the live `isStyleLoaded()` below is what decides. `addLayers` is
  // idempotent, so the overlap with the effect above is a no-op rather than a throw.
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
    // setData rather than a re-add, so panning, a new day or a new signal swaps the field
    // without tearing the source down under the map. A missing source here is the legitimate
    // first-pass case: the style had not loaded, and addLayers creates it from propsRef with
    // this same data.
    const source = map.getSource(SOURCE_ID) as GeoJSONSource | undefined;
    if (source) source.setData(geojson ?? EMPTY_COLLECTION);
    if (map.getLayer(FILL_LAYER_ID)) {
      // The ramp is per SIGNAL and this component switches between nine of them in place, so
      // the fill colour is written here as well as at add time -- `addLayers` short-circuits
      // on an existing layer and would otherwise leave the previous signal's ramp painted.
      map.setPaintProperty(FILL_LAYER_ID, "fill-color", fillColor);
      map.setPaintProperty(FILL_LAYER_ID, "fill-opacity", fillOpacity);
    }
    if (map.getLayer(OUTLINE_LAYER_ID)) {
      map.setPaintProperty(OUTLINE_LAYER_ID, "line-opacity", outlineOpacity);
    }
  }, [map, geojson, fillColor, fillOpacity, outlineOpacity, visible]);

  return null;
}
