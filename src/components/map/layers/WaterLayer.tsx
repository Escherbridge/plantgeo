"use client";

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap, Popup, GeoJSONSource } from "maplibre-gl";
import type { GroundwaterWell, WaterGauge } from "@/lib/environmental/water";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import {
  WATERSHEDS_SOURCE,
  watershedsLayer,
  watershedsOutlineLayer,
} from "@/lib/map/layers";
import { useStyleReady } from "@/components/map/layers/use-style-ready";
import { formatTimestampWithRelative, toIsoTimestamp } from "@/lib/map/time-format";

const WATERSHEDS_LAYER_IDS = [watershedsLayer.id, watershedsOutlineLayer.id];

function escapeHtml(val: unknown): string {
  return String(val ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

/**
 * Feature properties survive a GeoJSON round-trip as strings, and absent values
 * arrive as either null or undefined -- so popups must test for a finite number
 * rather than `!== null`, which would render "Percentile: th" for a missing value.
 */
function finiteNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

// RdBu diverging: above/below normal is diverging data, so "normal" is the neutral
// gray midpoint. Exported so WaterPanel doesn't keep its own duplicate.
export const CONDITION_COLORS: Record<string, string> = {
  above_normal: "#2166ac",
  normal: "#9e9e9e",
  below_normal: "#f4a582",
  low: "#d6604d",
  critically_low: "#b2182b",
  // Darker than the "normal" pivot on purpose: "no reading" and "normal flow" are
  // different claims and must not share a color.
  unknown: "#616161",
};

/**
 * Groundwater wells are coloured by water-level trend, not by the condition vocabulary
 * above: a well reports a direction of change, a stream gauge reports a flow percentile.
 * The last entry is the fallback every unrecognised trend takes.
 */
export const WELL_TREND_COLORS = {
  rising: "#2196f3",
  stable: "#4caf50",
  declining: "#ff9800",
  unknown: "#f44336",
} as const;

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
  /**
   * Independent visibility for the watershed polygons. Without this, `visible` alone
   * decided both lifecycles: turning on watersheds mounted the (empty) gauge/well sources
   * too, and turning watersheds off had no dedicated teardown path -- the polygons only
   * cleared because the caller happened to push an empty FeatureCollection through
   * `watershedsGeoJSON` rather than because anything removed the layer. LayerManager now
   * always passes this separately, so the two switches no longer affect each other's
   * sources. Optional (defaults to `false`) only so a caller that omits it entirely --
   * rather than one relying on the old coupling -- gets the watershed polygons off by
   * default instead of silently inheriting `visible`.
   */
  watershedsVisible?: boolean;
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
            ? WELL_TREND_COLORS.rising
            : w.trend === "stable"
              ? WELL_TREND_COLORS.stable
              : w.trend === "declining"
                ? WELL_TREND_COLORS.declining
                : WELL_TREND_COLORS.unknown,
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
  watershedsVisible = false,
}: WaterLayerProps) {
  const popupRef = useRef<Popup | null>(null);

  // Keep latest data in refs for use inside style.load handlers
  const dataRef = useRef({ gauges, wells, watershedsGeoJSON, visible, watershedsVisible });
  dataRef.current = { gauges, wells, watershedsGeoJSON, visible, watershedsVisible };

  const addPointLayers = useCallback((m: MapLibreMap) => {
    const { gauges, wells } = dataRef.current;
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
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 2.5, 7, 4, 10, 6, 14, 9],
          "circle-color": ["get", "color"],
          "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 4, 0.5, 10, 1, 14, 1.5],
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
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 1.5, 7, 2.5, 10, 4, 14, 6.5],
          "circle-color": ["get", "color"],
          "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 4, 0.5, 14, 1],
          "circle-stroke-color": "#ffffff",
          "circle-opacity": 0.85,
        },
      }, beforeId);
    }
  }, []);

  const removePointLayers = useCallback((m: MapLibreMap) => {
    safeRemoveLayerAndSource(m, ["water-gauges-circle"], "water-gauges");
    safeRemoveLayerAndSource(m, ["groundwater-wells-circle"], "groundwater-wells");
  }, []);

  const addWatershedLayer = useCallback((m: MapLibreMap) => {
    const { watershedsGeoJSON } = dataRef.current;
    if (!watershedsGeoJSON) return;
    const beforeId = getFirstSymbolLayer(m);
    if (!m.getSource(WATERSHEDS_SOURCE)) {
      m.addSource(WATERSHEDS_SOURCE, { type: "geojson", data: watershedsGeoJSON });
    } else {
      (m.getSource(WATERSHEDS_SOURCE) as GeoJSONSource).setData(watershedsGeoJSON);
    }
    if (!m.getLayer(watershedsLayer.id)) {
      m.addLayer(watershedsLayer, beforeId);
    }
    if (!m.getLayer(watershedsOutlineLayer.id)) {
      m.addLayer(watershedsOutlineLayer, beforeId);
    }
  }, []);

  const removeWatershedLayer = useCallback((m: MapLibreMap) => {
    safeRemoveLayerAndSource(m, WATERSHEDS_LAYER_IDS, WATERSHEDS_SOURCE);
  }, []);

  // Persist layers across every future style change (basemap swap included).
  // addLayer/addSource work as soon as "style.load" fires -- see
  // src/components/map/AGENTS.md -- and addPointLayers/addWatershedLayer are each
  // idempotent (guarded on getLayer/getSource), so calling either unconditionally here is
  // safe even if it races with the styleReady effect below. Gauges/wells and the watershed
  // polygons are two independent lifecycles -- see `watershedsVisible` above -- so each
  // gets its own style.load listener and its own teardown, rather than one `visible` flag
  // gating both.
  useEffect(() => {
    if (!map) return;

    if (!visible) {
      removePointLayers(map);
      return;
    }

    const onStyleLoad = () => {
      if (!dataRef.current.visible) return;
      addPointLayers(map);
    };
    map.on("style.load", onStyleLoad);

    return () => {
      map.off("style.load", onStyleLoad);
      removePointLayers(map);
    };
  }, [map, visible, addPointLayers, removePointLayers]);

  useEffect(() => {
    if (!map) return;

    if (!watershedsVisible) {
      removeWatershedLayer(map);
      return;
    }

    const onStyleLoad = () => {
      if (!dataRef.current.watershedsVisible) return;
      addWatershedLayer(map);
    };
    map.on("style.load", onStyleLoad);

    return () => {
      map.off("style.load", onStyleLoad);
      removeWatershedLayer(map);
    };
  }, [map, watershedsVisible, addWatershedLayer, removeWatershedLayer]);

  // Add (or retry adding) once the style is actually ready. This is what
  // covers the bug this hook exists for: a mount (or a swap) where
  // isStyleLoaded() reads false at the moment "style.load" fires, and no
  // further "style.load" arrives to retry -- only "styledata" events do, as
  // tiles land. styleReady is only used to force these effects to re-run;
  // the actual gate re-reads the live map so it can never act on a stale
  // value. See use-style-ready.ts and AGENTS.md.
  const styleReady = useStyleReady(map);
  useEffect(() => {
    if (!map || !visible || !map.isStyleLoaded()) return;
    addPointLayers(map);
  }, [map, visible, addPointLayers, styleReady]);

  useEffect(() => {
    if (!map || !watershedsVisible || !map.isStyleLoaded()) return;
    addWatershedLayer(map);
  }, [map, watershedsVisible, addWatershedLayer, styleReady]);

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
    if (!map || !watershedsVisible || !watershedsGeoJSON) return;
    try { if (!map.getStyle()) return; } catch { return; }
    const source = map.getSource(WATERSHEDS_SOURCE) as
      | { setData: (d: GeoJSON.FeatureCollection) => void }
      | undefined;
    if (!source) return;
    source.setData(watershedsGeoJSON);
  }, [map, watershedsGeoJSON, watershedsVisible]);

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
        const flow = finiteNumber(props.flowCfs);
        const percentile = finiteNumber(props.percentile);
        const measured = formatTimestampWithRelative(toIsoTimestamp(props.updatedAt));
        const html = `
          <div style="font-size:12px;min-width:180px">
            <strong style="display:block;margin-bottom:4px">${escapeHtml(props.siteName ?? "Unknown")}</strong>
            <span style="display:inline-block;padding:1px 6px;border-radius:3px;background:${escapeHtml(condColor)};color:#fff;font-size:10px;margin-bottom:4px">
              ${escapeHtml(String(props.condition ?? "unknown").replace(/_/g, " "))}
            </span>
            <div>Flow: <strong>${flow !== null ? `${escapeHtml(flow.toFixed(1))} cfs` : "N/A"}</strong> ${escapeHtml(trendSymbol)}</div>
            ${percentile !== null ? `<div>Percentile: ${escapeHtml(Math.round(percentile))}th</div>` : ""}
            ${measured ? `<div class="map-popup-meta">Measured: ${escapeHtml(measured)}</div>` : ""}
            <div class="map-popup-meta">USGS #${escapeHtml(props.siteNo)}</div>
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
        const depth = finiteNumber(props.depthFt);
        const measured = formatTimestampWithRelative(toIsoTimestamp(props.updatedAt));
        const html = `
          <div style="font-size:12px;min-width:160px">
            <strong style="display:block;margin-bottom:4px">${escapeHtml(props.siteName ?? "Groundwater Well")}</strong>
            <div>Depth: <strong>${depth !== null ? `${escapeHtml(depth.toFixed(1))} ft` : "N/A"}</strong></div>
            <div>Trend: <strong>${escapeHtml(String(props.trend ?? "stable"))} ${escapeHtml(trendSymbol)}</strong></div>
            ${measured ? `<div class="map-popup-meta">Measured: ${escapeHtml(measured)}</div>` : ""}
            <div class="map-popup-meta">USGS #${escapeHtml(props.siteNo)}</div>
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
