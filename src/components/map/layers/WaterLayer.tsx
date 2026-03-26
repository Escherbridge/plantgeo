"use client";

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap, Popup, GeoJSONSource } from "maplibre-gl";
import type { WaterGauge, GroundwaterWell } from "@/lib/server/services/usgs-water";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";

function escapeHtml(val: unknown): string {
  return String(val ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

const CONDITION_COLORS: Record<string, string> = {
  above_normal: "#2196f3",
  normal: "#009688",
  below_normal: "#ffeb3b",
  low: "#ff9800",
  critically_low: "#f44336",
  unknown: "#9e9e9e",
};

const TREND_ARROW: Record<string, string> = {
  rising: "\u2191",
  stable: "\u2192",
  declining: "\u2193",
  critical: "\u2193\u2193",
};

interface WaterLayerProps {
  map: MapLibreMap | null;
  gauges?: WaterGauge[];
  wells?: GroundwaterWell[];
  watershedsGeoJSON?: GeoJSON.FeatureCollection | null;
  onGaugeClick?: (gauge: WaterGauge) => void;
  onWellClick?: (well: GroundwaterWell) => void;
  visible?: boolean;
}

function buildGaugeGeoJSON(gauges: WaterGauge[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: gauges.map((g) => ({
      type: "Feature" as const,
      geometry: { type: "Point" as const, coordinates: [g.lon, g.lat] },
      properties: {
        siteNo: g.siteNo,
        siteName: g.siteName,
        flowCfs: g.flowCfs,
        percentile: g.percentile,
        condition: g.condition,
        trend: g.trend ?? "stable",
        color: CONDITION_COLORS[g.condition] ?? CONDITION_COLORS.unknown,
        updatedAt: g.updatedAt,
      },
    })),
  };
}

function buildWellGeoJSON(wells: GroundwaterWell[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: wells.map((w) => ({
      type: "Feature" as const,
      geometry: { type: "Point" as const, coordinates: [w.lon, w.lat] },
      properties: {
        siteNo: w.siteNo,
        siteName: w.siteName,
        depthFt: w.depthFt,
        trend: w.trend,
        color:
          w.trend === "rising"
            ? "#2196f3"
            : w.trend === "stable"
              ? "#4caf50"
              : w.trend === "declining"
                ? "#ff9800"
                : "#f44336",
        updatedAt: w.updatedAt,
      },
    })),
  };
}

export function WaterLayer({
  map,
  gauges = [],
  wells = [],
  watershedsGeoJSON,
  onGaugeClick,
  onWellClick,
  visible = true,
}: WaterLayerProps) {
  const popupRef = useRef<Popup | null>(null);

  // Keep latest data in refs for use inside style.load handler
  const dataRef = useRef({ gauges, wells, watershedsGeoJSON, visible });
  dataRef.current = { gauges, wells, watershedsGeoJSON, visible };

  const addAllLayers = useCallback((m: MapLibreMap) => {
    const { gauges, wells, watershedsGeoJSON } = dataRef.current;
    const beforeId = getFirstSymbolLayer(m);

    // --- Gauge circles ---
    const gaugeData = buildGaugeGeoJSON(gauges);
    if (!m.getSource("water-gauges")) {
      m.addSource("water-gauges", { type: "geojson", data: gaugeData });
    } else {
      (m.getSource("water-gauges") as GeoJSONSource).setData(gaugeData);
    }
    if (!m.getLayer("water-gauges-circle")) {
      m.addLayer({
        id: "water-gauges-circle",
        type: "circle",
        source: "water-gauges",
        paint: {
          "circle-radius": 8,
          "circle-color": ["get", "color"],
          "circle-stroke-width": 1.5,
          "circle-stroke-color": "#ffffff",
          "circle-opacity": 0.9,
        },
      }, beforeId);
    }

    // --- Well circles ---
    const wellData = buildWellGeoJSON(wells);
    if (!m.getSource("groundwater-wells")) {
      m.addSource("groundwater-wells", { type: "geojson", data: wellData });
    } else {
      (m.getSource("groundwater-wells") as GeoJSONSource).setData(wellData);
    }
    if (!m.getLayer("groundwater-wells-circle")) {
      m.addLayer({
        id: "groundwater-wells-circle",
        type: "circle",
        source: "groundwater-wells",
        paint: {
          "circle-radius": 5,
          "circle-color": ["get", "color"],
          "circle-stroke-width": 1,
          "circle-stroke-color": "#ffffff",
          "circle-opacity": 0.85,
        },
      }, beforeId);
    }

    // --- Watershed polygons ---
    if (watershedsGeoJSON) {
      if (!m.getSource("watersheds")) {
        m.addSource("watersheds", { type: "geojson", data: watershedsGeoJSON });
      } else {
        (m.getSource("watersheds") as GeoJSONSource).setData(watershedsGeoJSON);
      }
      if (!m.getLayer("watersheds-fill")) {
        m.addLayer({
          id: "watersheds-fill",
          type: "fill",
          source: "watersheds",
          paint: { "fill-color": "#1565c0", "fill-opacity": 0.05 },
        }, beforeId);
      }
      if (!m.getLayer("watersheds-outline")) {
        m.addLayer({
          id: "watersheds-outline",
          type: "line",
          source: "watersheds",
          paint: { "line-color": "#1565c0", "line-width": 1, "line-opacity": 0.6 },
        }, beforeId);
      }
    }
  }, []);

  const removeAllLayers = useCallback((m: MapLibreMap) => {
    safeRemoveLayerAndSource(m, ["water-gauges-circle"], "water-gauges");
    safeRemoveLayerAndSource(m, ["groundwater-wells-circle"], "groundwater-wells");
    safeRemoveLayerAndSource(m, ["watersheds-fill", "watersheds-outline"], "watersheds");
  }, []);

  // Main effect: add/remove layers and persist across style changes
  useEffect(() => {
    if (!map) return;

    if (!visible) {
      removeAllLayers(map);
      return;
    }

    const onStyleLoad = () => {
      if (!dataRef.current.visible) return;
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

  // Update gauge data when gauges prop changes
  useEffect(() => {
    if (!map || !visible) return;
    try { if (!map.getStyle()) return; } catch { return; }
    const source = map.getSource("water-gauges") as
      | { setData: (d: GeoJSON.FeatureCollection) => void }
      | undefined;
    if (!source) return;
    source.setData(buildGaugeGeoJSON(gauges));
  }, [map, gauges, visible]);

  // Update well data when wells prop changes
  useEffect(() => {
    if (!map || !visible) return;
    try { if (!map.getStyle()) return; } catch { return; }
    const source = map.getSource("groundwater-wells") as
      | { setData: (d: GeoJSON.FeatureCollection) => void }
      | undefined;
    if (!source) return;
    source.setData(buildWellGeoJSON(wells));
  }, [map, wells, visible]);

  // Update watershed data when prop changes
  useEffect(() => {
    if (!map || !visible || !watershedsGeoJSON) return;
    try { if (!map.getStyle()) return; } catch { return; }
    const source = map.getSource("watersheds") as
      | { setData: (d: GeoJSON.FeatureCollection) => void }
      | undefined;
    if (!source) return;
    source.setData(watershedsGeoJSON);
  }, [map, watershedsGeoJSON, visible]);

  // Gauge click popup
  useEffect(() => {
    if (!map || !visible) return;

    function handleGaugeClick(
      e: maplibregl.MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] }
    ) {
      if (!map || !e.features?.length) return;
      const props = e.features[0].properties as Record<string, unknown>;
      const gauge = gauges.find((g) => g.siteNo === props.siteNo);

      if (gauge && onGaugeClick) {
        onGaugeClick(gauge);
        return;
      }

      // Fallback inline popup
      import("maplibre-gl").then(({ Popup }) => {
        if (popupRef.current) popupRef.current.remove();
        const trendSymbol = TREND_ARROW[props.trend as string] ?? "\u2192";
        const condColor = CONDITION_COLORS[props.condition as string] ?? "#9e9e9e";
        const html = `
          <div style="font-size:12px;min-width:180px">
            <strong style="display:block;margin-bottom:4px">${escapeHtml(props.siteName ?? "Unknown")}</strong>
            <span style="display:inline-block;padding:1px 6px;border-radius:3px;background:${escapeHtml(condColor)};color:#fff;font-size:10px;margin-bottom:4px">
              ${escapeHtml(String(props.condition ?? "unknown").replace(/_/g, " "))}
            </span>
            <div>Flow: <strong>${props.flowCfs !== null ? `${escapeHtml(Number(props.flowCfs).toFixed(1))} cfs` : "N/A"}</strong> ${escapeHtml(trendSymbol)}</div>
            ${props.percentile !== null ? `<div>Percentile: ${escapeHtml(props.percentile)}th</div>` : ""}
            <div style="color:#888;font-size:10px;margin-top:4px">USGS #${escapeHtml(props.siteNo)}</div>
          </div>
        `;
        popupRef.current = new Popup({ closeButton: true, maxWidth: "240px" })
          .setLngLat(e.lngLat)
          .setHTML(html)
          .addTo(map);
      });
    }

    map.on("click", "water-gauges-circle", handleGaugeClick);
    return () => {
      map.off("click", "water-gauges-circle", handleGaugeClick);
    };
  }, [map, gauges, onGaugeClick, visible]);

  // Well click popup
  useEffect(() => {
    if (!map || !visible) return;

    function handleWellClick(
      e: maplibregl.MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] }
    ) {
      if (!map || !e.features?.length) return;
      const props = e.features[0].properties as Record<string, unknown>;
      const well = wells.find((w) => w.siteNo === props.siteNo);

      if (well && onWellClick) {
        onWellClick(well);
        return;
      }

      import("maplibre-gl").then(({ Popup }) => {
        if (popupRef.current) popupRef.current.remove();
        const trendSymbol = TREND_ARROW[props.trend as string] ?? "\u2192";
        const html = `
          <div style="font-size:12px;min-width:160px">
            <strong style="display:block;margin-bottom:4px">${escapeHtml(props.siteName ?? "Groundwater Well")}</strong>
            <div>Depth: <strong>${props.depthFt !== null ? `${escapeHtml(Number(props.depthFt).toFixed(1))} ft` : "N/A"}</strong></div>
            <div>Trend: <strong>${escapeHtml(String(props.trend ?? "stable"))} ${escapeHtml(trendSymbol)}</strong></div>
            <div style="color:#888;font-size:10px;margin-top:4px">USGS #${escapeHtml(props.siteNo)}</div>
          </div>
        `;
        popupRef.current = new Popup({ closeButton: true, maxWidth: "220px" })
          .setLngLat(e.lngLat)
          .setHTML(html)
          .addTo(map);
      });
    }

    map.on("click", "groundwater-wells-circle", handleWellClick);
    return () => {
      map.off("click", "groundwater-wells-circle", handleWellClick);
    };
  }, [map, wells, onWellClick, visible]);

  return null;
}
