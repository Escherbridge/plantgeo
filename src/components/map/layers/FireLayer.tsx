"use client";

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap, Popup } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import { DEMO_FIRE_POINTS } from "@/lib/map/demo-data";

// --- Source & layer IDs ---
const FIRE_DEMO_SOURCE = "fire-demo-source";
const FIRE_DEMO_CIRCLES = "fire-demo-circles";
const FIRE_DEMO_OUTLINES = "fire-demo-outlines";

function escapeHtml(val: unknown): string {
  return String(val ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

interface FireLayerProps {
  map: MapLibreMap | null;
  visible?: boolean;
  opacity?: number;
  /** Fire GeoJSON data — real NIFC data or demo fallback. */
  geojson?: GeoJSON.FeatureCollection;
}

export function FireLayer({
  map,
  visible = true,
  opacity = 0.85,
  geojson,
}: FireLayerProps) {
  const popupRef = useRef<Popup | null>(null);

  // Build fallback demo data when no geojson prop is provided
  const fireData: GeoJSON.FeatureCollection = geojson ?? {
    type: "FeatureCollection",
    features: DEMO_FIRE_POINTS.map((pt) => ({
      type: "Feature" as const,
      geometry: { type: "Point" as const, coordinates: [pt.lon, pt.lat] },
      properties: {
        IncidentName: `Demo Fire (${pt.satellite})`,
        IncidentSize: null,
        brightness: pt.brightness,
        confidence: pt.confidence,
        satellite: pt.satellite,
      },
    })),
  };

  // Keep latest props in refs so style.load handler uses current values
  const propsRef = useRef({ visible, opacity, fireData });
  propsRef.current = { visible, opacity, fireData };

  const addAllLayers = useCallback((m: MapLibreMap) => {
    const { opacity, fireData } = propsRef.current;
    const beforeId = getFirstSymbolLayer(m);

    // --- Fire circles ---
    if (!m.getSource(FIRE_DEMO_SOURCE)) {
      m.addSource(FIRE_DEMO_SOURCE, { type: "geojson", data: fireData });
    }
    if (!m.getLayer(FIRE_DEMO_CIRCLES)) {
      m.addLayer(
        {
          id: FIRE_DEMO_CIRCLES,
          type: "circle",
          source: FIRE_DEMO_SOURCE,
          paint: {
            "circle-color": [
              "case",
              // NIFC data: color by containment
              ["has", "PercentContained"],
              [
                "interpolate", ["linear"], ["coalesce", ["get", "PercentContained"], 0],
                0, "#dc2626",    // red = 0% contained
                50, "#f97316",   // orange = 50%
                100, "#22c55e",  // green = 100%
              ],
              // Demo data: color by brightness
              [
                "interpolate", ["linear"], ["coalesce", ["get", "brightness"], 350],
                300, "#fbbf24",
                400, "#f97316",
                500, "#dc2626",
              ],
            ],
            "circle-radius": [
              "case",
              // NIFC data: size by acreage
              ["has", "IncidentSize"],
              [
                "interpolate", ["linear"],
                ["coalesce", ["get", "IncidentSize"], 10],
                0, 4,
                100, 7,
                1000, 10,
                10000, 14,
                100000, 20,
              ],
              // Demo data: size by confidence
              [
                "interpolate", ["linear"],
                ["coalesce", ["get", "confidence"], 50],
                50, 5,
                100, 12,
              ],
            ],
            "circle-opacity": opacity,
            "circle-stroke-width": 1.5,
            "circle-stroke-color": "#ffffff",
          },
        },
        beforeId,
      );
    }
    if (!m.getLayer(FIRE_DEMO_OUTLINES)) {
      m.addLayer(
        {
          id: FIRE_DEMO_OUTLINES,
          type: "circle",
          source: FIRE_DEMO_SOURCE,
          paint: {
            "circle-radius": [
              "case",
              ["has", "IncidentSize"],
              [
                "interpolate", ["linear"],
                ["coalesce", ["get", "IncidentSize"], 10],
                0, 4,
                100, 7,
                1000, 10,
                10000, 14,
                100000, 20,
              ],
              [
                "interpolate", ["linear"],
                ["coalesce", ["get", "confidence"], 50],
                50, 5,
                100, 12,
              ],
            ],
            "circle-color": "transparent",
            "circle-stroke-width": 1.5,
            "circle-stroke-color": "#ffffff",
            "circle-opacity": 0,
            "circle-stroke-opacity": opacity,
          },
        },
        beforeId,
      );
    }
  }, []);

  const removeAllLayers = useCallback((m: MapLibreMap) => {
    safeRemoveLayerAndSource(
      m,
      [FIRE_DEMO_CIRCLES, FIRE_DEMO_OUTLINES],
      FIRE_DEMO_SOURCE,
    );
  }, []);

  // Main effect: add/remove layers and persist across style changes
  useEffect(() => {
    if (!map) return;

    if (!visible) {
      removeAllLayers(map);
      return;
    }

    const onStyleLoad = () => {
      if (!propsRef.current.visible) return;
      addAllLayers(map);
    };

    if (map.isStyleLoaded()) {
      addAllLayers(map);
    } else {
      map.once("style.load", () => addAllLayers(map));
    }

    // Persist layers across future style changes
    map.on("style.load", onStyleLoad);

    return () => {
      map.off("style.load", onStyleLoad);
      removeAllLayers(map);
    };
  }, [map, visible, addAllLayers, removeAllLayers]);

  // Update fire data when new geojson arrives
  useEffect(() => {
    if (!map || !visible) return;
    try {
      if (!map.getStyle()) return;
    } catch {
      return;
    }

    const source = map.getSource(FIRE_DEMO_SOURCE);
    if (source && "setData" in source) {
      (source as maplibregl.GeoJSONSource).setData(fireData);
    }
  }, [map, visible, fireData]);

  // Update opacity when it changes
  useEffect(() => {
    if (!map || !visible) return;
    try {
      if (!map.getStyle()) return;
    } catch {
      return;
    }

    if (map.getLayer(FIRE_DEMO_CIRCLES)) {
      map.setPaintProperty(FIRE_DEMO_CIRCLES, "circle-opacity", opacity);
    }
    if (map.getLayer(FIRE_DEMO_OUTLINES)) {
      map.setPaintProperty(FIRE_DEMO_OUTLINES, "circle-stroke-opacity", opacity);
    }
  }, [map, opacity, visible]);

  // Click popup for fire circles
  useEffect(() => {
    if (!map || !visible) return;

    function handleFireClick(
      e: maplibregl.MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] },
    ) {
      if (!map || !e.features?.length) return;
      const props = e.features[0].properties as Record<string, unknown>;

      import("maplibre-gl").then(({ Popup }) => {
        if (popupRef.current) popupRef.current.remove();

        const name = escapeHtml(props.IncidentName ?? "Unknown Fire");
        const size = props.IncidentSize ? `${Number(props.IncidentSize).toLocaleString()} acres` : "N/A";
        const contained = props.PercentContained != null ? `${Number(props.PercentContained).toFixed(0)}%` : "N/A";
        const state = escapeHtml(props.POOState ?? props.satellite ?? "Unknown");
        const discovered = props.FireDiscoveryDateTime
          ? new Date(Number(props.FireDiscoveryDateTime)).toLocaleDateString()
          : escapeHtml(props.detectedAt ?? "Unknown");

        const html = `
          <div style="font-size:12px;min-width:180px">
            <strong style="display:block;margin-bottom:4px;color:#dc2626">${name}</strong>
            <div>Size: <strong>${escapeHtml(size)}</strong></div>
            <div>Contained: <strong>${escapeHtml(contained)}</strong></div>
            <div>State: <strong>${state}</strong></div>
            <div style="color:#888;font-size:10px;margin-top:4px">Discovered: ${discovered}</div>
          </div>
        `;

        popupRef.current = new Popup({ closeButton: true, maxWidth: "240px" })
          .setLngLat(e.lngLat)
          .setHTML(html)
          .addTo(map);
      });
    }

    map.on("click", FIRE_DEMO_CIRCLES, handleFireClick);
    return () => {
      map.off("click", FIRE_DEMO_CIRCLES, handleFireClick);
    };
  }, [map, visible]);

  // Cleanup popup on unmount
  useEffect(() => {
    return () => {
      if (popupRef.current) {
        popupRef.current.remove();
        popupRef.current = null;
      }
    };
  }, []);

  return null;
}
