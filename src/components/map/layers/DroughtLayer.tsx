"use client";

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";

/** USDM drought category color mapping (DM property 0-4 plus none). */
export const DROUGHT_COLORS: Record<string, string> = {
  none: "transparent",
  D0: "#ffff00",
  D1: "#fcd37f",
  D2: "#ffaa00",
  D3: "#e60000",
  D4: "#730000",
};

/** Ordered legend entries for UI display. */
export const DROUGHT_LEGEND = [
  { label: "None", color: "#f0f0f0" },
  { label: "D0 – Abnormally Dry", color: "#ffff00" },
  { label: "D1 – Moderate Drought", color: "#fcd37f" },
  { label: "D2 – Severe Drought", color: "#ffaa00" },
  { label: "D3 – Extreme Drought", color: "#e60000" },
  { label: "D4 – Exceptional Drought", color: "#730000" },
] as const;

interface DroughtLayerProps {
  map: MapLibreMap | null;
  geojson: GeoJSON.FeatureCollection | null;
  opacity?: number;
  visible?: boolean;
}

/**
 * Renders USDM drought GeoJSON as a fill-color choropleth using MapLibre GL JS.
 * The USDM GeoJSON features carry a numeric `DM` property (0=D0 ... 4=D4).
 */
export function DroughtLayer({ map, geojson, opacity = 0.6, visible = true }: DroughtLayerProps) {
  const propsRef = useRef({ geojson, opacity, visible });
  propsRef.current = { geojson, opacity, visible };

  const addLayers = useCallback((m: MapLibreMap) => {
    const { geojson, opacity } = propsRef.current;
    if (!geojson) return;

    const beforeId = getFirstSymbolLayer(m);

    if (!m.getSource("drought-monitor")) {
      m.addSource("drought-monitor", { type: "geojson", data: geojson });
    } else {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (m.getSource("drought-monitor") as any).setData(geojson);
    }

    if (!m.getLayer("drought-fill")) {
      m.addLayer({
        id: "drought-fill",
        type: "fill",
        source: "drought-monitor",
        paint: {
          "fill-color": [
            "match",
            ["get", "DM"],
            0, DROUGHT_COLORS.D0,
            1, DROUGHT_COLORS.D1,
            2, DROUGHT_COLORS.D2,
            3, DROUGHT_COLORS.D3,
            4, DROUGHT_COLORS.D4,
            "transparent",
          ],
          "fill-opacity": opacity,
        },
      }, beforeId);
    }

    if (!m.getLayer("drought-outline")) {
      m.addLayer({
        id: "drought-outline",
        type: "line",
        source: "drought-monitor",
        paint: {
          "line-color": [
            "match",
            ["get", "DM"],
            0, "#cccc00",
            1, "#c9a000",
            2, "#cc8800",
            3, "#b30000",
            4, "#500000",
            "#888888",
          ],
          "line-width": 0.5,
          "line-opacity": 0.8,
        },
      }, beforeId);
    }
  }, []);

  const removeLayers = useCallback((m: MapLibreMap) => {
    safeRemoveLayerAndSource(m, ["drought-fill", "drought-outline"], "drought-monitor");
  }, []);

  // Main effect: add/remove and persist across style changes
  useEffect(() => {
    if (!map) return;

    if (!visible || !geojson) {
      removeLayers(map);
      return;
    }

    const onStyleLoad = () => {
      if (!propsRef.current.visible || !propsRef.current.geojson) return;
      addLayers(map);
    };

    if (map.isStyleLoaded()) {
      addLayers(map);
    } else {
      map.once("style.load", () => addLayers(map));
    }

    map.on("style.load", onStyleLoad);

    return () => {
      map.off("style.load", onStyleLoad);
      removeLayers(map);
    };
  }, [map, geojson, visible, addLayers, removeLayers]);

  // Update opacity without rebuilding layers
  useEffect(() => {
    if (!map || !visible) return;
    try { if (!map.getStyle()) return; } catch { return; }
    if (map.getLayer("drought-fill")) {
      map.setPaintProperty("drought-fill", "fill-opacity", opacity);
    }
  }, [map, opacity, visible]);

  return null;
}
