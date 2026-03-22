"use client";

import { useEffect } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";

/** USDM drought category color mapping (DM property 0–4 plus none). */
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
}

/**
 * Renders USDM drought GeoJSON as a fill-color choropleth using MapLibre GL JS.
 * The USDM GeoJSON features carry a numeric `DM` property (0=D0 … 4=D4).
 */
export function DroughtLayer({ map, geojson, opacity = 0.6 }: DroughtLayerProps) {
  useEffect(() => {
    if (!map || !geojson) return;

    function addLayers() {
      if (!map || !geojson) return;

      if (map.getSource("drought-monitor")) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (map.getSource("drought-monitor") as any).setData(geojson);
        return;
      }

      map.addSource("drought-monitor", { type: "geojson", data: geojson });

      // Fill layer — DM 0–4 mapped to USDM colors
      map.addLayer({
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
      });

      // Stroke layer
      map.addLayer({
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
      });
    }

    if (map.isStyleLoaded()) {
      addLayers();
    } else {
      map.once("styledata", addLayers);
    }

    return () => {
      if (!map || !map.isStyleLoaded()) return;
      if (map.getLayer("drought-fill")) map.removeLayer("drought-fill");
      if (map.getLayer("drought-outline")) map.removeLayer("drought-outline");
      if (map.getSource("drought-monitor")) map.removeSource("drought-monitor");
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, geojson]);

  // Update opacity without rebuilding layers
  useEffect(() => {
    if (!map || !map.isStyleLoaded()) return;
    if (map.getLayer("drought-fill")) {
      map.setPaintProperty("drought-fill", "fill-opacity", opacity);
    }
  }, [map, opacity]);

  return null;
}
