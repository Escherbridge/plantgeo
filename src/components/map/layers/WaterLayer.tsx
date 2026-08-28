"use client";

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap, Popup, GeoJSONSource } from "maplibre-gl";
import type { GroundwaterWell, WaterGauge } from "@/lib/environmental/water";
import type { WaterGaugeCell } from "@/lib/environmental/parquet-presentation";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import { useStyleReady } from "@/components/map/layers/use-style-ready";
import { formatTimestampWithRelative, toIsoTimestamp } from "@/lib/map/time-format";

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

/**
 * The two states a USGS gauge can actually be drawn in.
 *
 * This replaced a five-class flow-condition ramp (critically low -> above normal). That ramp
 * was unreachable: classifying a condition needs a flow percentile, NWIS's instantaneous-values
 * service does not return one, and `usgs-water.ts` accordingly binds `percentile` to a literal
 * null -- so `classifyCondition` returned "unknown" for every gauge ever served, and the map
 * painted all of them the same grey under a legend advertising five colours none of them could
 * take. Percentiles need the separate NWIS statistics service; until that lane exists the only
 * honest distinction is whether the gauge reported a discharge value at all.
 */
export const GAUGE_READING_COLORS: Record<string, string> = {
  reporting: "#2166ac",
  no_reading: "#616161",
};

/**
 * Groundwater wells are coloured by water-level trend, not by the two-state vocabulary above:
 * a well reports a direction of change measured against its own record, which is a claim a
 * stream gauge's instantaneous discharge cannot make.
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

/**
 * Authored strengths, one per circle layer. Wells sit slightly under gauges so a well plotted
 * on top of a gauge does not read as the brighter of the two.
 */
const GAUGE_CIRCLE_OPACITY = 0.9;
const WELL_CIRCLE_OPACITY = 0.85;

/** Anonymous coarse-rung means are purple so they cannot be mistaken for named gauges. */
const AGGREGATE_CELL_COLOR = "#7c3aed";

interface WaterLayerProps {
  map: MapLibreMap | null;
  gauges?: WaterGauge[];
  aggregateCells?: WaterGaugeCell[];
  wells?: GroundwaterWell[];
  onGaugeClick?: (gauge: WaterGauge) => void;
  onWellClick?: (well: GroundwaterWell) => void;
  visible?: boolean;
  /**
   * The reader's MULTIPLIER over both authored strengths above, from
   * `layer-store.layerOpacity.water`. Both circle layers belong to the one `water` toggle, so
   * they take one scalar; watersheds is a separate toggle and is no longer drawn here at all
   * (it moved onto the baked `geo.watershed_tiles()` style layers).
   */
  opacityScale?: number;
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
        // No `trend` and no `condition`. Trend was `g.trend ?? "stable"`, and "stable" is a
        // claim about change over time that nothing here measured -- `inferTrend` reads NWIS
        // qualifier codes, which say how a value was determined, not which way it is moving.
        color:
          g.flowCfs === null
            ? GAUGE_READING_COLORS.no_reading
            : GAUGE_READING_COLORS.reporting,
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

function buildAggregateCellGeoJSON(cells: WaterGaugeCell[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: cells.map((cell, index) => ({
      type: "Feature" as const,
      id: `${cell.longitude}:${cell.latitude}:${cell.observedDay}:${index}`,
      geometry: {
        type: "Point" as const,
        coordinates: [cell.longitude, cell.latitude],
      },
      properties: {
        flowCfs: cell.flowCfs,
        observedAt: cell.observedAt,
        observedDay: cell.observedDay,
        source: cell.source,
        color: cell.flowCfs === null ? GAUGE_READING_COLORS.no_reading : AGGREGATE_CELL_COLOR,
      },
    })),
  };
}

export function WaterLayer({
  map,
  gauges = [],
  aggregateCells = [],
  wells = [],
  onGaugeClick,
  onWellClick,
  visible = true,
  opacityScale = 1,
}: WaterLayerProps) {
  const popupRef = useRef<Popup | null>(null);

  const gaugeOpacity = GAUGE_CIRCLE_OPACITY * opacityScale;
  const wellOpacity = WELL_CIRCLE_OPACITY * opacityScale;

  // Keep latest data in refs for use inside style.load handlers
  const dataRef = useRef({ gauges, aggregateCells, wells, visible, gaugeOpacity, wellOpacity });
  dataRef.current = { gauges, aggregateCells, wells, visible, gaugeOpacity, wellOpacity };

  const addPointLayers = useCallback((m: MapLibreMap) => {
    const { gauges, aggregateCells, wells, gaugeOpacity, wellOpacity } = dataRef.current;
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
          "circle-opacity": gaugeOpacity,
        },
      }, beforeId);
    }

    // --- Anonymous coarse-rung cells ---
    const aggregateData = buildAggregateCellGeoJSON(aggregateCells);
    if (!m.getSource("water-gauge-cells")) {
      m.addSource("water-gauge-cells", { type: "geojson", data: aggregateData });
    } else {
      (m.getSource("water-gauge-cells") as GeoJSONSource).setData(aggregateData);
    }
    if (!m.getLayer("water-gauge-cells-circle")) {
      m.addLayer(
        {
          id: "water-gauge-cells-circle",
          type: "circle",
          source: "water-gauge-cells",
          paint: {
            "circle-radius": ["interpolate", ["linear"], ["zoom"], 0, 4, 9, 8, 13, 11],
            "circle-color": ["get", "color"],
            "circle-stroke-width": 2,
            "circle-stroke-color": "#ffffff",
            "circle-opacity": gaugeOpacity,
          },
        },
        beforeId
      );
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
          "circle-opacity": wellOpacity,
        },
      }, beforeId);
    }
  }, []);

  const removePointLayers = useCallback((m: MapLibreMap) => {
    safeRemoveLayerAndSource(m, ["water-gauges-circle"], "water-gauges");
    safeRemoveLayerAndSource(m, ["water-gauge-cells-circle"], "water-gauge-cells");
    safeRemoveLayerAndSource(m, ["groundwater-wells-circle"], "groundwater-wells");
  }, []);

  // Persist layers across every future style change (basemap swap included).
  // addLayer/addSource work as soon as "style.load" fires -- see
  // src/components/map/AGENTS.md -- and addPointLayers is idempotent (guarded on
  // getLayer/getSource), so calling it unconditionally here is safe even if it races with
  // the styleReady effect below.
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

  // Update anonymous coarse cells independently from the named-gauge source.
  useEffect(() => {
    if (!map || !visible) return;
    try { if (!map.getStyle()) return; } catch { return; }
    const source = map.getSource("water-gauge-cells") as
      | { setData: (d: GeoJSON.FeatureCollection) => void }
      | undefined;
    if (!source) return;
    source.setData(buildAggregateCellGeoJSON(aggregateCells));
  }, [map, aggregateCells, visible]);

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

  // The reader's multiplier, applied without a rebuild. Separate from the data effects above
  // because those re-run on every viewport response, and folding opacity into them would make
  // a pan rewrite the paint the reader just set.
  useEffect(() => {
    if (!map || !visible) return;
    try { if (!map.getStyle()) return; } catch { return; }
    if (map.getLayer("water-gauges-circle")) {
      map.setPaintProperty("water-gauges-circle", "circle-opacity", gaugeOpacity);
    }
    if (map.getLayer("water-gauge-cells-circle")) {
      map.setPaintProperty("water-gauge-cells-circle", "circle-opacity", gaugeOpacity);
    }
    if (map.getLayer("groundwater-wells-circle")) {
      map.setPaintProperty("groundwater-wells-circle", "circle-opacity", wellOpacity);
    }
  }, [map, gaugeOpacity, wellOpacity, visible]);

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
        const flow = finiteNumber(props.flowCfs);
        const measured = formatTimestampWithRelative(toIsoTimestamp(props.updatedAt));
        // No condition badge, no percentile row, no trend arrow: NWIS instantaneous values
        // carry none of the three. The discharge reading and when it was taken are what this
        // gauge actually reported.
        const html = `
          <div style="font-size:12px;min-width:180px">
            <strong style="display:block;margin-bottom:4px">${escapeHtml(props.siteName ?? "Unknown")}</strong>
            <div>Discharge: <strong>${flow !== null ? `${escapeHtml(flow.toFixed(1))} cfs` : "not reported"}</strong></div>
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

  // Coarse-cell popup: calls the value a mean and deliberately offers no gauge identity.
  useEffect(() => {
    if (!map || !visible) return;

    function handleAggregateClick(
      e: maplibregl.MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] }
    ) {
      if (!map || !e.features?.length) return;
      const props = e.features[0].properties as Record<string, unknown>;
      import("maplibre-gl").then(({ Popup }) => {
        if (popupRef.current) popupRef.current.remove();
        const flow = finiteNumber(props.flowCfs);
        const measured = formatTimestampWithRelative(toIsoTimestamp(props.observedAt));
        const html = `
          <div style="font-size:12px;min-width:180px">
            <strong style="display:block;margin-bottom:4px">Coarse streamflow cell</strong>
            <div>Mean discharge: <strong>${flow !== null ? `${escapeHtml(flow.toFixed(1))} cfs` : "not reported"}</strong></div>
            ${measured ? `<div class="map-popup-meta">Newest reading: ${escapeHtml(measured)}</div>` : ""}
            <div class="map-popup-meta">Several gauges may contribute; no single gauge identity applies.</div>
          </div>
        `;
        popupRef.current = new Popup({ closeButton: true, maxWidth: "260px" })
          .setLngLat(e.lngLat)
          .setHTML(html)
          .addTo(map);
      });
    }

    map.on("click", "water-gauge-cells-circle", handleAggregateClick);
    return () => {
      map.off("click", "water-gauge-cells-circle", handleAggregateClick);
    };
  }, [map, visible]);

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
