"use client";

import { useEffect, useRef } from "react";
import type { Map as MapLibreMap, Popup } from "maplibre-gl";
import type { WaterGauge, GroundwaterWell } from "@/lib/server/services/usgs-water";

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
  rising: "↑",
  stable: "→",
  declining: "↓",
  critical: "↓↓",
};

interface WaterLayerProps {
  map: MapLibreMap | null;
  gauges?: WaterGauge[];
  wells?: GroundwaterWell[];
  watershedsGeoJSON?: GeoJSON.FeatureCollection | null;
  onGaugeClick?: (gauge: WaterGauge) => void;
  onWellClick?: (well: GroundwaterWell) => void;
}

export function WaterLayer({
  map,
  gauges = [],
  wells = [],
  watershedsGeoJSON,
  onGaugeClick,
  onWellClick,
}: WaterLayerProps) {
  const popupRef = useRef<Popup | null>(null);

  // Streamflow gauge markers
  useEffect(() => {
    if (!map) return;

    const gaugeGeoJSON: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: gauges.map((g) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [g.lon, g.lat] },
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

    function addGaugeLayers() {
      if (!map) return;

      if (map.getSource("water-gauges")) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (map.getSource("water-gauges") as any).setData(gaugeGeoJSON);
        return;
      }

      map.addSource("water-gauges", { type: "geojson", data: gaugeGeoJSON });

      map.addLayer({
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
      });
    }

    if (map.isStyleLoaded()) {
      addGaugeLayers();
    } else {
      map.once("styledata", addGaugeLayers);
    }

    return () => {
      if (!map || !map.isStyleLoaded()) return;
      if (map.getLayer("water-gauges-circle")) map.removeLayer("water-gauges-circle");
      if (map.getSource("water-gauges")) map.removeSource("water-gauges");
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);

  // Update gauge data when gauges prop changes
  useEffect(() => {
    if (!map || !map.isStyleLoaded()) return;
    const source = map.getSource("water-gauges") as
      | { setData: (d: GeoJSON.FeatureCollection) => void }
      | undefined;
    if (!source) return;

    source.setData({
      type: "FeatureCollection",
      features: gauges.map((g) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [g.lon, g.lat] },
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
    });
  }, [map, gauges]);

  // Gauge click popup
  useEffect(() => {
    if (!map) return;

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
        const trendSymbol = TREND_ARROW[props.trend as string] ?? "→";
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
  }, [map, gauges, onGaugeClick]);

  // Groundwater well markers
  useEffect(() => {
    if (!map) return;

    const wellGeoJSON: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: wells.map((w) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [w.lon, w.lat] },
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

    function addWellLayers() {
      if (!map) return;

      if (map.getSource("groundwater-wells")) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (map.getSource("groundwater-wells") as any).setData(wellGeoJSON);
        return;
      }

      map.addSource("groundwater-wells", { type: "geojson", data: wellGeoJSON });

      map.addLayer({
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
      });
    }

    if (map.isStyleLoaded()) {
      addWellLayers();
    } else {
      map.once("styledata", addWellLayers);
    }

    return () => {
      if (!map || !map.isStyleLoaded()) return;
      if (map.getLayer("groundwater-wells-circle")) map.removeLayer("groundwater-wells-circle");
      if (map.getSource("groundwater-wells")) map.removeSource("groundwater-wells");
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);

  // Update wells data
  useEffect(() => {
    if (!map || !map.isStyleLoaded()) return;
    const source = map.getSource("groundwater-wells") as
      | { setData: (d: GeoJSON.FeatureCollection) => void }
      | undefined;
    if (!source) return;

    source.setData({
      type: "FeatureCollection",
      features: wells.map((w) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [w.lon, w.lat] },
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
    });
  }, [map, wells]);

  // Well click popup
  useEffect(() => {
    if (!map) return;

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
        const trendSymbol = TREND_ARROW[props.trend as string] ?? "→";
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
  }, [map, wells, onWellClick]);

  // Watershed polygon outlines
  useEffect(() => {
    if (!map || !watershedsGeoJSON) return;

    function addWatershedLayers() {
      if (!map || !watershedsGeoJSON) return;

      if (map.getSource("watersheds")) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (map.getSource("watersheds") as any).setData(watershedsGeoJSON);
        return;
      }

      map.addSource("watersheds", { type: "geojson", data: watershedsGeoJSON });

      map.addLayer({
        id: "watersheds-fill",
        type: "fill",
        source: "watersheds",
        paint: {
          "fill-color": "#1565c0",
          "fill-opacity": 0.05,
        },
      });

      map.addLayer({
        id: "watersheds-outline",
        type: "line",
        source: "watersheds",
        paint: {
          "line-color": "#1565c0",
          "line-width": 1,
          "line-opacity": 0.6,
        },
      });
    }

    if (map.isStyleLoaded()) {
      addWatershedLayers();
    } else {
      map.once("styledata", addWatershedLayers);
    }

    return () => {
      if (!map || !map.isStyleLoaded()) return;
      if (map.getLayer("watersheds-fill")) map.removeLayer("watersheds-fill");
      if (map.getLayer("watersheds-outline")) map.removeLayer("watersheds-outline");
      if (map.getSource("watersheds")) map.removeSource("watersheds");
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, watershedsGeoJSON]);

  return null;
}
