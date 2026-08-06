"use client";

import { useCallback, useEffect, useRef } from "react";
import type { Map as MapLibreMap, GeoJSONSource } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import { soilMoistureColorStops } from "@/lib/environmental/soil-moisture";
import type { ExpressionSpecification } from "@/types/map";

/**
 * ERA5-Land soil moisture, drawn from whatever `environmental.getSoilMoisture` served: the
 * stored 0.25-degree cells at high zoom, or dissolved isobands over a coarser aggregation
 * lattice below it. One source and one fill either way, because every served feature carries
 * a `value` -- see `src/components/map/AGENTS.md` §soil-moisture.
 *
 * Plain MapLibre `fill`, not deck.gl. The aggregation and the contouring already happened on
 * the server, so what reaches the browser is a handful of polygons; a `ContourLayer` would
 * mean re-deriving them client-side from data the server deliberately does not ship.
 */
const SOIL_MOISTURE_SOURCE_ID = "soil-moisture-field";
const SOIL_MOISTURE_FILL_LAYER_ID = "soil-moisture-field-fill";
const SOIL_MOISTURE_OUTLINE_LAYER_ID = "soil-moisture-field-outline";

const EMPTY_COLLECTION: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

/**
 * Interpolated over the shared band table, so a fill and the panel's legend cannot disagree
 * about what a value looks like. Derived rather than restated, which is why the assertion is
 * needed: MapLibre types an expression as a union of fixed-length tuples and a spread widens
 * it -- the same trade `NDVI_CELL_FILL_COLOR` makes in VegetationLayer.
 */
const SOIL_MOISTURE_FILL_COLOR = [
  "interpolate",
  ["linear"],
  ["get", "value"],
  ...soilMoistureColorStops(),
] as unknown as ExpressionSpecification;

/**
 * Outlines are drawn ONLY on unaggregated cells, where they say something true: these are
 * discrete 0.25-degree samples. An isoband boundary is a contour through interpolated space,
 * and outlining it would draw a hard edge the data does not have.
 */
const SOIL_MOISTURE_OUTLINE_OPACITY = [
  "case",
  ["==", ["get", "aggregated"], true],
  0,
  0.25,
] as unknown as ExpressionSpecification;

interface SoilMoistureLayerProps {
  map: MapLibreMap | null;
  /**
   * The served collection. Empty -- never null -- when the layer is switched off or the
   * viewport holds none, so `setData` has something to clear with.
   */
  geojson?: GeoJSON.FeatureCollection | null;
  opacity?: number;
  visible?: boolean;
}

export function SoilMoistureLayer({
  map,
  geojson = null,
  opacity = 0.7,
  visible = true,
}: SoilMoistureLayerProps) {
  // Latest props behind a ref so the style.load handler re-attaches with current values.
  const propsRef = useRef({ geojson, opacity });
  propsRef.current = { geojson, opacity };

  const addLayers = useCallback((mapInstance: MapLibreMap) => {
    const { geojson: currentGeoJson, opacity: currentOpacity } = propsRef.current;
    const beforeId = getFirstSymbolLayer(mapInstance);

    if (!mapInstance.getSource(SOIL_MOISTURE_SOURCE_ID)) {
      mapInstance.addSource(SOIL_MOISTURE_SOURCE_ID, {
        type: "geojson",
        data: currentGeoJson ?? EMPTY_COLLECTION,
        attribution: "ERA5-Land (Copernicus/ECMWF) via Open-Meteo, CC-BY 4.0",
      });
    }
    if (!mapInstance.getLayer(SOIL_MOISTURE_FILL_LAYER_ID)) {
      mapInstance.addLayer(
        {
          id: SOIL_MOISTURE_FILL_LAYER_ID,
          type: "fill",
          source: SOIL_MOISTURE_SOURCE_ID,
          paint: {
            "fill-color": SOIL_MOISTURE_FILL_COLOR,
            "fill-opacity": currentOpacity,
          },
        },
        beforeId
      );
    }
    if (!mapInstance.getLayer(SOIL_MOISTURE_OUTLINE_LAYER_ID)) {
      mapInstance.addLayer(
        {
          id: SOIL_MOISTURE_OUTLINE_LAYER_ID,
          type: "line",
          source: SOIL_MOISTURE_SOURCE_ID,
          paint: {
            "line-color": "#3f3f46",
            "line-width": 0.5,
            "line-opacity": SOIL_MOISTURE_OUTLINE_OPACITY,
          },
        },
        beforeId
      );
    }
  }, []);

  const removeLayers = useCallback((mapInstance: MapLibreMap) => {
    safeRemoveLayerAndSource(
      mapInstance,
      [SOIL_MOISTURE_OUTLINE_LAYER_ID, SOIL_MOISTURE_FILL_LAYER_ID],
      SOIL_MOISTURE_SOURCE_ID
    );
  }, []);

  useEffect(() => {
    if (!map) return;
    if (!visible) {
      removeLayers(map);
      return;
    }
    // Persistent listener, never `once` alongside `on` -- see src/components/map/AGENTS.md
    // "Style.load listener order".
    const onStyleLoad = () => addLayers(map);
    if (map.isStyleLoaded()) addLayers(map);
    map.on("style.load", onStyleLoad);
    return () => {
      map.off("style.load", onStyleLoad);
      removeLayers(map);
    };
  }, [map, visible, addLayers, removeLayers]);

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
    const source = map.getSource(SOIL_MOISTURE_SOURCE_ID) as GeoJSONSource | undefined;
    if (source) source.setData(geojson ?? EMPTY_COLLECTION);
    if (map.getLayer(SOIL_MOISTURE_FILL_LAYER_ID)) {
      map.setPaintProperty(SOIL_MOISTURE_FILL_LAYER_ID, "fill-opacity", opacity);
    }
  }, [map, geojson, opacity, visible]);

  return null;
}
