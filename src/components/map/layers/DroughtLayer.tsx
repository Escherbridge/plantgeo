"use client";

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap, GeoJSONSource } from "maplibre-gl";
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

/**
 * The classes the fill expression below actually paints, ordered, reading their colours
 * from DROUGHT_COLORS so a palette edit reaches the map and the legend at once.
 *
 * Deliberately without the "None" row DROUGHT_LEGEND carries: the fill's fallback arm is
 * `transparent`, so a "None" swatch would legend a colour the map never draws.
 */
export const DROUGHT_DRAWN_CLASSES = [
  { color: DROUGHT_COLORS.D0, label: "D0 — Abnormally dry" },
  { color: DROUGHT_COLORS.D1, label: "D1 — Moderate drought" },
  { color: DROUGHT_COLORS.D2, label: "D2 — Severe drought" },
  { color: DROUGHT_COLORS.D3, label: "D3 — Extreme drought" },
  { color: DROUGHT_COLORS.D4, label: "D4 — Exceptional drought" },
] as const;

/** Ordered legend entries for UI display. */
export const DROUGHT_LEGEND = [
  { label: "None", color: "#f0f0f0" },
  { label: "D0 – Abnormally Dry", color: "#ffff00" },
  { label: "D1 – Moderate Drought", color: "#fcd37f" },
  { label: "D2 – Severe Drought", color: "#ffaa00" },
  { label: "D3 – Extreme Drought", color: "#e60000" },
  { label: "D4 – Exceptional Drought", color: "#730000" },
] as const;

/** The outline's authored strength, darker than the fill so class edges stay readable. */
const DROUGHT_OUTLINE_OPACITY = 0.8;

interface DroughtLayerProps {
  map: MapLibreMap | null;
  geojson: GeoJSON.FeatureCollection | null;
  /** The fill's authored strength. The design value, not a control. */
  opacity?: number;
  /** The reader's per-layer MULTIPLIER over both authored values. See layer-opacity.ts. */
  opacityScale?: number;
  visible?: boolean;
}

/**
 * Renders USDM drought GeoJSON as a fill-color choropleth using MapLibre GL JS.
 * The USDM GeoJSON features carry a numeric `DM` property (0=D0 ... 4=D4).
 */
export function DroughtLayer({
  map,
  geojson,
  opacity = 0.6,
  opacityScale = 1,
  visible = true,
}: DroughtLayerProps) {
  const fillOpacity = opacity * opacityScale;
  const outlineOpacity = DROUGHT_OUTLINE_OPACITY * opacityScale;
  const propsRef = useRef({ geojson, fillOpacity, outlineOpacity, visible });
  propsRef.current = { geojson, fillOpacity, outlineOpacity, visible };

  const addLayers = useCallback((m: MapLibreMap) => {
    const { geojson, fillOpacity, outlineOpacity } = propsRef.current;
    if (!geojson) return;

    const beforeId = getFirstSymbolLayer(m);

    if (!m.getSource("drought-monitor")) {
      m.addSource("drought-monitor", { type: "geojson", data: geojson });
    } else {
      (m.getSource("drought-monitor") as GeoJSONSource).setData(geojson);
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
          "fill-opacity": fillOpacity,
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
          "line-opacity": outlineOpacity,
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

  // Update opacity without rebuilding layers. Both layers, not just the fill: an outline left
  // at its authored 0.8 while the fill recedes reads as the layer never having dimmed.
  useEffect(() => {
    if (!map || !visible) return;
    try { if (!map.getStyle()) return; } catch { return; }
    if (map.getLayer("drought-fill")) {
      map.setPaintProperty("drought-fill", "fill-opacity", fillOpacity);
    }
    if (map.getLayer("drought-outline")) {
      map.setPaintProperty("drought-outline", "line-opacity", outlineOpacity);
    }
  }, [map, fillOpacity, outlineOpacity, visible]);

  return null;
}
