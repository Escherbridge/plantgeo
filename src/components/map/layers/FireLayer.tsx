"use client";

import { useEffect, useRef, useCallback } from "react";
import type { Map as MapLibreMap, Popup } from "maplibre-gl";
import { getFirstSymbolLayer, safeRemoveLayerAndSource } from "@/lib/map/layer-utils";
import { useStyleReady } from "@/components/map/layers/use-style-ready";
import {
  fireCellCaptionText,
  fireDetectionCellLines,
  FIRE_CELL_CAPTION_TITLE,
} from "@/lib/map/fire-cell-caption";
import type { FireDetectionCollection } from "@/lib/environmental/parquet-fire-presentation";
import type { ExpressionSpecification } from "@/types/map";
import type { FilterSpecification } from "@maplibre/maplibre-gl-style-spec";

const EMPTY_FIRE_DATA: FireDetectionCollection = {
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
 * Published fire-detection cells: colour by total fire radiative power, in megawatts.
 *
 * The NIFC containment ramp and the per-detection brightness ramp that stood here until the
 * 2026-09-01 Parquet cutover are gone with the feeds that fed them. `/api/fires` was the only
 * producer of `PercentContained`, `IncidentSize`, `brightness` and `confidence`, and no caller
 * reaches this component with those keys any more -- an expression reading them would paint
 * every cell from its `coalesce` fallback while looking like it was reading data.
 */
export const FIRE_DETECTION_FRP_COLOR_STOPS: readonly FireColorStop[] = [
  { value: 0, color: "#fbbf24", label: "0 MW" },
  { value: 100, color: "#f97316", label: "100 MW" },
  { value: 500, color: "#dc2626", label: "500 MW" },
  { value: 2000, color: "#991b1b", label: "2,000 MW" },
];

/**
 * The detection counts the CELL FILL is keyed to at coarse and middle zoom, where a square has
 * no size channel to carry them in.
 *
 * The same five breaks the dot radius uses below, and the radius ramp is built from these stops
 * rather than restating them: the square a reader sees at z5 and the dot that replaces it at z13
 * are the same cell, and two hand-kept lists are how they would come to disagree about what
 * "many detections" means.
 *
 * Count rather than radiative power, deliberately. The runbook's coarse-band rule is that an event
 * aggregate carries "count/intensity/provenance", and the count is the quantity the cell IS: a
 * detection density over a declared square. FRP keeps the detail-band dot, where the second
 * channel exists to show it.
 */
export const FIRE_DETECTION_COUNT_COLOR_STOPS: readonly FireColorStop[] = [
  { value: 1, color: "#fde68a", label: "1 detection" },
  { value: 5, color: "#fbbf24", label: "5" },
  { value: 25, color: "#f97316", label: "25" },
  { value: 100, color: "#dc2626", label: "100" },
  { value: 500, color: "#7f1d1d", label: "500+" },
];

/** The dot radius at each break above, in the same order. */
const FIRE_DETECTION_COUNT_RADII = [4, 7, 11, 16, 22] as const;

/**
 * A cell whose detections carried no FRP reading at all. Deliberately off the ramp rather
 * than at its 0 MW end: no reported power is not zero power.
 */
export const FIRE_DETECTION_NO_FRP_COLOR = "#94a3b8";
export const FIRE_DETECTION_NO_FRP_LABEL = "No FRP reported";

/** The ring on a cell holding at least one high-confidence detection. */
export const FIRE_DETECTION_HIGH_CONFIDENCE_RING_COLOR = "#ffffff";
export const FIRE_DETECTION_HIGH_CONFIDENCE_RING_LABEL =
  "Contains a high-confidence detection";

/** The ring on a cell holding none. */
export const FIRE_DETECTION_LOW_CONFIDENCE_RING_COLOR = "#64748b";
export const FIRE_DETECTION_LOW_CONFIDENCE_RING_LABEL = "No high-confidence detection";

/**
 * Derived from the exported stops rather than restated, so the map and the legend cannot
 * drift. The assertion is forced by the same typing MapLibre imposes on VegetationLayer's
 * NDVI fill: spreading a mapped array widens the fixed-length expression tuple.
 *
 * The unclassified branch tests BOTH `frpObservationCount > 0` and a non-null `frpSum`, which is
 * the same condition `fireDetectionCellLines` uses to decide whether it prints a number -- so the
 * colour and the caption can never disagree about which cells reported power. Either half alone
 * lets a null sum fall through `coalesce` to 0 MW and paint "no reading" as "no power": the count
 * alone misses a cell that counted observations but carries no sum, and the sum alone misses
 * nothing today but restates a rule the caption owns.
 */
const FIRE_CIRCLE_COLOR = [
  "case",
  [
    "any",
    ["<=", ["coalesce", ["get", "frpObservationCount"], 0], 0],
    ["==", ["typeof", ["get", "frpSum"]], "null"],
  ],
  FIRE_DETECTION_NO_FRP_COLOR,
  [
    "interpolate", ["linear"], ["coalesce", ["get", "frpSum"], 0],
    ...FIRE_DETECTION_FRP_COLOR_STOPS.flatMap((stop) => [stop.value, stop.color]),
  ],
] as unknown as ExpressionSpecification;

/** The ring says whether any detection in the cell cleared the high-confidence bar. */
const FIRE_RING_COLOR = [
  "case",
  [">", ["coalesce", ["get", "highConfidenceDetectionCount"], 0], 0],
  FIRE_DETECTION_HIGH_CONFIDENCE_RING_COLOR,
  FIRE_DETECTION_LOW_CONFIDENCE_RING_COLOR,
] as unknown as ExpressionSpecification;

/**
 * The cell fill at coarse and middle zoom: how many detections the declared square aggregates.
 *
 * Derived from the exported stops for the same reason the FRP ramp above is -- the legend reads
 * the very array this paints from -- and asserted for the same reason: spreading a mapped array
 * widens MapLibre's fixed-length expression tuple.
 */
const FIRE_CELL_FILL_COLOR = [
  "interpolate", ["linear"], ["coalesce", ["get", "detectionCount"], 1],
  ...FIRE_DETECTION_COUNT_COLOR_STOPS.flatMap((stop) => [stop.value, stop.color]),
] as unknown as ExpressionSpecification;

/**
 * Dot size is how many detections the cell aggregates. Written once and reused at each zoom
 * anchor below rather than restated three times, which is how the containment/acreage pair
 * this replaces came to carry six copies of two ramps.
 */
const FIRE_DETECTION_COUNT_RADIUS = [
  "interpolate", ["linear"],
  ["coalesce", ["get", "detectionCount"], 1],
  ...FIRE_DETECTION_COUNT_COLOR_STOPS.flatMap((stop, index) => [
    stop.value,
    FIRE_DETECTION_COUNT_RADII[index],
  ]),
];

/** The same detection-count ramp, scaled down as the map zooms out. */
const FIRE_CIRCLE_RADIUS = [
  "interpolate", ["linear"], ["zoom"],
  4, ["*", 0.45, FIRE_DETECTION_COUNT_RADIUS],
  8, ["*", 0.7, FIRE_DETECTION_COUNT_RADIUS],
  12, FIRE_DETECTION_COUNT_RADIUS,
] as unknown as ExpressionSpecification;

const FIRE_SOURCE = "published-fire-source";
const FIRE_CIRCLES = "published-fire-circles";
const FIRE_OUTLINES = "published-fire-outlines";
/** The declared cell squares, drawn at coarse and middle zoom. See `presentParquetFireDetections`. */
const FIRE_CELLS_FILL = "published-fire-cells-fill";

/**
 * One geometry per band, told apart by the geometry the presenter chose rather than by a second
 * source. `presentParquetFireDetections` emits a Polygon for a coarse or middle rung and a Point
 * for a detail one, so these two filters are what deliver the spec's first acceptance gate --
 * one physical rung renders at a time -- without this component knowing the zoom at all.
 */
const POLYGON_ONLY: FilterSpecification = ["==", ["geometry-type"], "Polygon"];
const POINT_ONLY: FilterSpecification = ["==", ["geometry-type"], "Point"];

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
  /** The layer's authored strength. The design value, not a control. */
  opacity?: number;
  /**
   * The reader's per-layer MULTIPLIER over `opacity`, from `layer-store.layerOpacity`. 1 is
   * "exactly as authored". This component is the only writer of both circle opacity
   * properties, which is why the multiplier arrives rather than a replacement value -- see
   * src/lib/map/layer-opacity.ts.
   */
  opacityScale?: number;
  /** Published fire-detection cells. Missing data renders an empty layer. */
  geojson?: FireDetectionCollection;
}

export function FireLayer({
  map,
  visible = true,
  opacity = 0.85,
  opacityScale = 1,
  geojson,
}: FireLayerProps) {
  const popupRef = useRef<Popup | null>(null);

  const fireData = geojson ?? EMPTY_FIRE_DATA;
  const drawnOpacity = opacity * opacityScale;

  // Keep latest props in refs so style.load handler uses current values
  const propsRef = useRef({ visible, drawnOpacity, fireData });
  useEffect(() => {
    propsRef.current = { visible, drawnOpacity, fireData };
  }, [visible, drawnOpacity, fireData]);

  const addAllLayers = useCallback((m: MapLibreMap) => {
    const { drawnOpacity, fireData } = propsRef.current;
    const beforeId = getFirstSymbolLayer(m);

    if (!m.getSource(FIRE_SOURCE)) {
      m.addSource(FIRE_SOURCE, { type: "geojson", data: fireData });
    }
    // The declared squares go on FIRST so the detail-band dots, when both somehow coexist,
    // draw over them rather than under. Adjacent squares share bit-identical edges
    // (`supportCellPolygon`), so no map background shows through the seams -- which is why
    // there is no separate line layer here: a stroked boundary is what would reintroduce the
    // visible seams the 2026-09-01 assessment found on the soil blocks. `fill-outline-color`
    // draws its hairline ON the shared boundary instead, and carries the confidence ring the
    // dots show as a stroke.
    if (!m.getLayer(FIRE_CELLS_FILL)) {
      m.addLayer(
        {
          id: FIRE_CELLS_FILL,
          type: "fill",
          source: FIRE_SOURCE,
          filter: POLYGON_ONLY,
          paint: {
            "fill-color": FIRE_CELL_FILL_COLOR,
            "fill-outline-color": FIRE_RING_COLOR,
            "fill-opacity": drawnOpacity,
          },
        },
        beforeId,
      );
    }
    if (!m.getLayer(FIRE_CIRCLES)) {
      m.addLayer(
        {
          id: FIRE_CIRCLES,
          type: "circle",
          source: FIRE_SOURCE,
          filter: POINT_ONLY,
          paint: {
            "circle-color": FIRE_CIRCLE_COLOR,
            "circle-radius": FIRE_CIRCLE_RADIUS,
            "circle-opacity": drawnOpacity,
            "circle-stroke-width": 1.5,
            "circle-stroke-color": FIRE_RING_COLOR,
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
          filter: POINT_ONLY,
          paint: {
            "circle-radius": FIRE_CIRCLE_RADIUS,
            "circle-color": "transparent",
            "circle-stroke-width": 1.5,
            "circle-stroke-color": FIRE_RING_COLOR,
            // Deliberately 0, and it stays 0 at every multiplier: the ring this layer exists
            // to draw is on circle-stroke-opacity below. This is the worked example for why
            // opacity is a multiplier -- an absolute writer would fill every fire circle with
            // a second opaque disc over the coloured one.
            "circle-opacity": 0,
            "circle-stroke-opacity": drawnOpacity,
          },
        },
        beforeId,
      );
    }
  }, []);

  const removeAllLayers = useCallback((m: MapLibreMap) => {
    safeRemoveLayerAndSource(
      m,
      [FIRE_CELLS_FILL, FIRE_CIRCLES, FIRE_OUTLINES],
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

    if (map.getLayer(FIRE_CELLS_FILL)) {
      map.setPaintProperty(FIRE_CELLS_FILL, "fill-opacity", drawnOpacity);
    }
    if (map.getLayer(FIRE_CIRCLES)) {
      map.setPaintProperty(FIRE_CIRCLES, "circle-opacity", drawnOpacity);
    }
    // The outline's circle-opacity is never written here: it is authored 0 and must stay 0.
    if (map.getLayer(FIRE_OUTLINES)) {
      map.setPaintProperty(FIRE_OUTLINES, "circle-stroke-opacity", drawnOpacity);
    }
  }, [map, drawnOpacity, visible]);

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

        // The cell vocabulary, and only it. Every incident field this popup used to read
        // (`IncidentName`, `IncidentSize`, `PercentContained`, `POOState`) came from
        // `/api/fires`, which no map surface calls any more -- reading them would print
        // "Unknown Fire / N/A / N/A" over a cell that has real numbers to show.
        //
        // The wording is `fire-cell-caption.ts`'s, shared with the hover tooltip: this popup and
        // that tooltip describe the SAME cell, and each maintaining its own copy of the six
        // fields is how they came to disagree about capitalisation, digit grouping and which
        // reading counts as an FRP measurement. Only the markup is this file's.
        const captionLines = fireDetectionCellLines(props);
        const body = captionLines
          .map((line) =>
            line.meta
              ? `<div class="map-popup-meta">${escapeHtml(fireCellCaptionText(line))}</div>`
              : `<div>${escapeHtml(line.label ?? "")}: <strong>${escapeHtml(line.value)}</strong></div>`
          )
          .join("\n            ");

        const html = `
          <div style="font-size:12px;min-width:180px">
            <strong style="display:block;margin-bottom:4px;color:#dc2626">${escapeHtml(FIRE_CELL_CAPTION_TITLE)}</strong>
            ${body}
          </div>
        `;

        popupRef.current = new Popup({ closeButton: true, maxWidth: "240px" })
          .setLngLat(e.lngLat)
          .setHTML(html)
          .addTo(map);
      });
    }

    // Both renderings of the one cell, because a reader clicks the shape that is there: the
    // square at coarse and middle zoom, the dot at detail zoom. One handler, because the caption
    // is the same cell's either way -- including the "not a fire perimeter" line, which the
    // square needs most and the dot must not contradict.
    map.on("click", FIRE_CELLS_FILL, handleFireClick);
    map.on("click", FIRE_CIRCLES, handleFireClick);
    return () => {
      map.off("click", FIRE_CELLS_FILL, handleFireClick);
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
