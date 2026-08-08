"use client";

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap, Popup } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import { useStyleReady } from "@/components/map/layers/use-style-ready";
import {
  formatAbsoluteDateTime,
  formatTimestampWithRelative,
  resolveObservationIso,
  toIsoTimestamp,
} from "@/lib/map/time-format";
import type { ExpressionSpecification } from "@/types/map";

const EMPTY_FIRE_DATA: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

/** One interpolation stop of a fire circle's colour ramp. */
export interface FireColorStop {
  value: number;
  color: string;
  label: string;
}

/**
 * NIFC incidents: colour by reported containment.
 *
 * Red-green endpoints on one ramp are the worst colorblind case; blue keeps "resolved"
 * visually distinct from "danger".
 */
export const FIRE_CONTAINMENT_COLOR_STOPS: readonly FireColorStop[] = [
  { value: 0, color: "#dc2626", label: "0% contained" },
  { value: 50, color: "#f97316", label: "50%" },
  { value: 100, color: "#2563eb", label: "100% contained" },
];

/**
 * Warehouse FIRMS detections: colour by brightness temperature, not by containment -- a
 * satellite detection reports no containment at all, so the two feeds share one circle
 * layer but never one ramp.
 */
export const FIRE_BRIGHTNESS_COLOR_STOPS: readonly FireColorStop[] = [
  { value: 300, color: "#fbbf24", label: "300 K" },
  { value: 400, color: "#f97316", label: "400 K" },
  { value: 500, color: "#dc2626", label: "500 K" },
];

/**
 * Derived from the exported stops rather than restated, so the map and the legend cannot
 * drift. The assertion is forced by the same typing MapLibre imposes on VegetationLayer's
 * NDVI fill: spreading a mapped array widens the fixed-length expression tuple.
 */
const FIRE_CIRCLE_COLOR = [
  "case",
  // NIFC data: color by containment
  ["has", "PercentContained"],
  [
    "interpolate", ["linear"], ["coalesce", ["get", "PercentContained"], 0],
    ...FIRE_CONTAINMENT_COLOR_STOPS.flatMap((stop) => [stop.value, stop.color]),
  ],
  // Warehouse FIRMS detections: color by brightness.
  [
    "interpolate", ["linear"], ["coalesce", ["get", "brightness"], 350],
    ...FIRE_BRIGHTNESS_COLOR_STOPS.flatMap((stop) => [stop.value, stop.color]),
  ],
] as unknown as ExpressionSpecification;

const FIRE_SOURCE = "published-fire-source";
const FIRE_CIRCLES = "published-fire-circles";
const FIRE_OUTLINES = "published-fire-outlines";

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
  /** Verified fire GeoJSON. Missing data renders an empty layer. */
  geojson?: GeoJSON.FeatureCollection;
}

export function FireLayer({
  map,
  visible = true,
  opacity = 0.85,
  geojson,
}: FireLayerProps) {
  const popupRef = useRef<Popup | null>(null);

  const fireData = geojson ?? EMPTY_FIRE_DATA;

  // Keep latest props in refs so style.load handler uses current values
  const propsRef = useRef({ visible, opacity, fireData });
  useEffect(() => {
    propsRef.current = { visible, opacity, fireData };
  }, [visible, opacity, fireData]);

  const addAllLayers = useCallback((m: MapLibreMap) => {
    const { opacity, fireData } = propsRef.current;
    const beforeId = getFirstSymbolLayer(m);

    if (!m.getSource(FIRE_SOURCE)) {
      m.addSource(FIRE_SOURCE, { type: "geojson", data: fireData });
    }
    if (!m.getLayer(FIRE_CIRCLES)) {
      m.addLayer(
        {
          id: FIRE_CIRCLES,
          type: "circle",
          source: FIRE_SOURCE,
          paint: {
            "circle-color": FIRE_CIRCLE_COLOR,
            "circle-radius": [
              "interpolate", ["linear"], ["zoom"],
              4, ["*", 0.45, [
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
                // Warehouse FIRMS detections: size by confidence.
                [
                  "interpolate", ["linear"],
                  ["coalesce", ["get", "confidence"], 50],
                  50, 5,
                  100, 12,
                ],
              ]],
              8, ["*", 0.7, [
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
              ]],
              12, [
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
            ],
            "circle-opacity": opacity,
            "circle-stroke-width": 1.5,
            "circle-stroke-color": "#ffffff",
          },
        },
        beforeId,
      );
    }
    if (!m.getLayer(FIRE_OUTLINES)) {
      m.addLayer(
        {
          id: FIRE_OUTLINES,
          type: "circle",
          source: FIRE_SOURCE,
          paint: {
            "circle-radius": [
              "interpolate", ["linear"], ["zoom"],
              4, ["*", 0.45, [
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
              ]],
              8, ["*", 0.7, [
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
              ]],
              12, [
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
      [FIRE_CIRCLES, FIRE_OUTLINES],
      FIRE_SOURCE,
    );
  }, []);

  // Persist layers across every future style change (basemap swap included).
  // addLayer/addSource work as soon as "style.load" fires -- see
  // src/components/map/AGENTS.md -- and addAllLayers is idempotent (guards
  // on getLayer/getSource), so calling it unconditionally here is safe even
  // if it races with the styleReady effect below.
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
    map.on("style.load", onStyleLoad);

    return () => {
      map.off("style.load", onStyleLoad);
      removeAllLayers(map);
    };
  }, [map, visible, addAllLayers, removeAllLayers]);

  // Add (or retry adding) once the style is actually ready. This is what
  // covers the bug this hook exists for: a mount (or a swap) where
  // isStyleLoaded() reads false at the moment "style.load" fires, and no
  // further "style.load" arrives to retry -- only "styledata" events do, as
  // tiles land. styleReady is only used to force this effect to re-run;
  // the actual gate re-reads the live map so it can never act on a stale
  // value. See use-style-ready.ts and AGENTS.md.
  const styleReady = useStyleReady(map);
  useEffect(() => {
    if (!map || !visible || !map.isStyleLoaded()) return;
    addAllLayers(map);
  }, [map, visible, addAllLayers, styleReady]);

  // Update fire data when new geojson arrives
  useEffect(() => {
    if (!map || !visible) return;
    try {
      if (!map.getStyle()) return;
    } catch {
      return;
    }

    const source = map.getSource(FIRE_SOURCE);
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

    if (map.getLayer(FIRE_CIRCLES)) {
      map.setPaintProperty(FIRE_CIRCLES, "circle-opacity", opacity);
    }
    if (map.getLayer(FIRE_OUTLINES)) {
      map.setPaintProperty(FIRE_OUTLINES, "circle-stroke-opacity", opacity);
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
        // NIFC incidents carry a discovery date; FIRMS detections only carry an
        // observation time. Both are the event's own date, not an ingestion date.
        const discovered = formatAbsoluteDateTime(
          toIsoTimestamp(props.FireDiscoveryDateTime ?? props.discoveredAt),
        );
        const detected = formatTimestampWithRelative(
          resolveObservationIso(props) ?? toIsoTimestamp(props.detectedAt),
        );

        const html = `
          <div style="font-size:12px;min-width:180px">
            <strong style="display:block;margin-bottom:4px;color:#dc2626">${name}</strong>
            <div>Size: <strong>${escapeHtml(size)}</strong></div>
            <div>Contained: <strong>${escapeHtml(contained)}</strong></div>
            <div>State: <strong>${state}</strong></div>
            ${discovered ? `<div class="map-popup-meta">Discovered: ${escapeHtml(discovered)}</div>` : ""}
            ${detected ? `<div class="map-popup-meta">Detected: ${escapeHtml(detected)}</div>` : ""}
          </div>
        `;

        popupRef.current = new Popup({ closeButton: true, maxWidth: "240px" })
          .setLngLat(e.lngLat)
          .setHTML(html)
          .addTo(map);
      });
    }

    map.on("click", FIRE_CIRCLES, handleFireClick);
    return () => {
      map.off("click", FIRE_CIRCLES, handleFireClick);
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
