"use client";

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap, Popup, GeoJSONSource } from "maplibre-gl";
import type { GroundwaterWell, WaterGauge } from "@/lib/environmental/water";
import type { WaterGaugeCell } from "@/lib/environmental/parquet-presentation";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import { useStyleReady } from "@/components/map/layers/use-style-ready";
import { formatTimestampWithRelative, toIsoTimestamp } from "@/lib/map/time-format";
import {
  formatSupportCellSize,
  WATER_CELL_AGGREGATE_NOTE,
  WATER_CELL_CAPTION_TITLE,
} from "@/lib/map/water-cell-caption";
import {
  assertNotPerimeter,
  supportCellPolygon,
  type SupportKind,
} from "@/lib/map/layer-render-contract";
import type { ExpressionSpecification } from "@/types/map";
import type { FilterSpecification } from "@maplibre/maplibre-gl-style-spec";

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

/**
 * The one form a coarse-rung streamflow cell is drawn in.
 *
 * `permittedFormsForTier("water", zoomTier)` offers `aggregate_cell`, `heatmap` and `cluster` in
 * the coarse and middle bands. The cell is the only one of the three the reader's envelope actually
 * supports: it declares an origin and a cell size, so the square is a DECLARED footprint rather
 * than a client-side re-binning. A `cluster` would be this component inventing a grouping the
 * warehouse did not perform, and a `heatmap` would smear discharge across ground between cells
 * where no gauge reported -- the fictitious finer footprint the spec forbids.
 */
const WATER_CELL_DRAWN_FORM: SupportKind = "aggregate_cell";

/**
 * Mean discharge over a coarse-rung cell, in cubic feet per second.
 *
 * Decade stops interpolated linearly, which is the same treatment the burn-acreage ramp gets and
 * for the same reason: discharge spans four orders of magnitude across the gauge network, and
 * evenly-spaced value stops would paint every cell but the largest rivers the same colour. Purple
 * throughout, so an aggregate can never be mistaken for the blue of a named reporting gauge.
 */
export const WATER_CELL_MEAN_FLOW_COLOR_STOPS: readonly {
  value: number;
  color: string;
  label: string;
}[] = [
  { value: 1, color: "#ede9fe", label: "1 cfs" },
  { value: 10, color: "#c4b5fd", label: "10" },
  { value: 100, color: "#a78bfa", label: "100" },
  { value: 1000, color: "#7c3aed", label: "1,000" },
  { value: 10000, color: "#4c1d95", label: "10,000 cfs" },
];

/**
 * Cells that reported no discharge at all take the gauge palette's no-reading grey rather than
 * the ramp's lightest purple: an unreported mean is not a small one.
 */
const WATER_CELL_FILL_COLOR = [
  "case",
  ["==", ["typeof", ["get", "flowCfs"]], "null"],
  GAUGE_READING_COLORS.no_reading,
  [
    "interpolate", ["linear"], ["coalesce", ["get", "flowCfs"], 0],
    ...WATER_CELL_MEAN_FLOW_COLOR_STOPS.flatMap((stop) => [stop.value, stop.color]),
  ],
] as unknown as ExpressionSpecification;

/**
 * One geometry per band from one source: the presenter emits a Polygon for a cell whose envelope
 * declared a footprint and leaves everything else a Point, so the square and the marker can never
 * both draw for the same cell.
 */
const POLYGON_ONLY: FilterSpecification = ["==", ["geometry-type"], "Polygon"];
const POINT_ONLY: FilterSpecification = ["==", ["geometry-type"], "Point"];

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
   * (it moved onto the baked `watersheds-fill`/`watersheds-outline` style layers, which read the
   * Parquet lane through `environmental.getWatershedBoundaries` since wave C and read
   * `geo.watershed_tiles()` before that).
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

/**
 * Coarse-rung cells as the squares their envelopes declare, or as markers where the envelope
 * declares no footprint.
 *
 * The square is never buffered from the centroid: `supportCellPolygon` builds it from the
 * envelope's own corner and cell size, and returns null rather than guessing when the envelope
 * carries no size -- which is what an unlocated or raw-point row presents as. `assertNotPerimeter`
 * guards the drawn form because `water` is an `event_point` layer: a mean over a square of ground
 * is no more a watershed boundary than a fire cell is a perimeter.
 */
function buildAggregateCellGeoJSON(cells: WaterGaugeCell[]): GeoJSON.FeatureCollection {
  assertNotPerimeter("water", WATER_CELL_DRAWN_FORM);
  return {
    type: "FeatureCollection",
    features: cells.map((cell) => {
      const support = cell.support;
      const declaredCell = supportCellPolygon(cell.longitude, cell.latitude, support);
      return {
        type: "Feature" as const,
        id: support.supportId,
        geometry:
          declaredCell ?? { type: "Point" as const, coordinates: [cell.longitude, cell.latitude] },
        properties: {
          flowCfs: cell.flowCfs,
          observedAt: cell.observedAt,
          observedDay: cell.observedDay,
          source: cell.source,
          color: cell.flowCfs === null ? GAUGE_READING_COLORS.no_reading : AGGREGATE_CELL_COLOR,
          supportKind: declaredCell === null ? null : WATER_CELL_DRAWN_FORM,
          supportId: support.supportId,
          cellWidthDegrees: support.cellWidthDegrees ?? null,
          cellHeightDegrees: support.cellHeightDegrees ?? null,
          // The number that makes the cell readable as an aggregate: how many gauges the mean
          // was taken over. Never inferred from the feature count, which is one per cell.
          gaugeCount: support.contributorCount,
        },
      };
    }),
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
    // The declared squares. No line layer, deliberately: neighbouring cells share bit-identical
    // edges, and a stroked boundary over them is what would reintroduce the visible seams the
    // 2026-09-01 assessment found. `fill-outline-color` puts a hairline ON the shared edge.
    if (!m.getLayer("water-gauge-cells-fill")) {
      m.addLayer(
        {
          id: "water-gauge-cells-fill",
          type: "fill",
          source: "water-gauge-cells",
          filter: POLYGON_ONLY,
          paint: {
            "fill-color": WATER_CELL_FILL_COLOR,
            "fill-outline-color": "#ffffff",
            "fill-opacity": gaugeOpacity,
          },
        },
        beforeId
      );
    }
    if (!m.getLayer("water-gauge-cells-circle")) {
      m.addLayer(
        {
          id: "water-gauge-cells-circle",
          type: "circle",
          source: "water-gauge-cells",
          filter: POINT_ONLY,
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
    safeRemoveLayerAndSource(
      m,
      ["water-gauge-cells-fill", "water-gauge-cells-circle"],
      "water-gauge-cells"
    );
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
    if (map.getLayer("water-gauge-cells-fill")) {
      map.setPaintProperty("water-gauge-cells-fill", "fill-opacity", gaugeOpacity);
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
        // How many gauges the mean was taken over, and how much ground it covers -- the two
        // things that make a filled square readable as an aggregate rather than as a boundary
        // somebody surveyed. Both are the envelope's own declarations, printed only when it
        // made them.
        const gaugeCount = finiteNumber(props.gaugeCount);
        const cellWidth = finiteNumber(props.cellWidthDegrees);
        const cellHeight = finiteNumber(props.cellHeightDegrees);
        const html = `
          <div style="font-size:12px;min-width:180px">
            <strong style="display:block;margin-bottom:4px">${escapeHtml(WATER_CELL_CAPTION_TITLE)}</strong>
            <div>Mean discharge: <strong>${flow !== null ? `${escapeHtml(flow.toFixed(1))} cfs` : "not reported"}</strong></div>
            ${gaugeCount !== null ? `<div>Gauges: <strong>${escapeHtml(gaugeCount.toLocaleString())}</strong></div>` : ""}
            ${measured ? `<div class="map-popup-meta">Newest reading: ${escapeHtml(measured)}</div>` : ""}
            ${cellWidth !== null && cellHeight !== null ? `<div class="map-popup-meta">Cell: ${escapeHtml(formatSupportCellSize(cellWidth, cellHeight))}</div>` : ""}
            <div class="map-popup-meta">${escapeHtml(WATER_CELL_AGGREGATE_NOTE)}</div>
          </div>
        `;
        popupRef.current = new Popup({ closeButton: true, maxWidth: "260px" })
          .setLngLat(e.lngLat)
          .setHTML(html)
          .addTo(map);
      });
    }

    map.on("click", "water-gauge-cells-fill", handleAggregateClick);
    map.on("click", "water-gauge-cells-circle", handleAggregateClick);
    return () => {
      map.off("click", "water-gauge-cells-fill", handleAggregateClick);
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
