import {
  resolveZoomTier,
  ZoomTierResolutionError,
  type ZoomTier,
} from "@/lib/map/zoom-tiers";
import type { ParquetBrowserReaderResult } from "@/lib/environmental/parquet-presentation";

/** Browser mirror of one published fire-detection aggregation cell. */
export interface ParquetBrowserFireDetectionCell {
  longitude: number;
  latitude: number;
  observedDay: string;
  detectionCount: number;
  frpSum: number | null;
  frpObservationCount: number;
  highConfidenceDetectionCount: number;
  newestObservedAt: string;
}

/** Browser mirror of one served day-window of fire cells. */
export interface ParquetBrowserFireWindow {
  firstDay: string;
  lastDay: string;
  cells: readonly ParquetBrowserFireDetectionCell[];
}

/**
 * Exactly what `FireLayer` paints with and what the popup reads; no other key is written.
 *
 * A type alias rather than an interface, deliberately: MapLibre's `addSource`/`setData` take
 * `GeoJSON.GeoJSON`, whose `properties` is an index-signature type, and only an alias gets the
 * implicit index signature that makes a typed collection assignable to it (TypeScript #15300).
 * An interface here compiles everywhere except the two calls that actually draw the layer.
 */
export type FireDetectionCellProperties = {
  detectionCount: number;
  frpSum: number | null;
  frpObservationCount: number;
  highConfidenceDetectionCount: number;
  observedDay: string;
  newestObservedAt: string;
  /** The serving rung these cells were aggregated at; null when no rung could be named. */
  zoomTier: ZoomTier | null;
};

export type FireDetectionCollection = GeoJSON.FeatureCollection<
  GeoJSON.Point,
  FireDetectionCellProperties
>;

/** What a caption may state about a window of cells without reading any single one. */
export interface FireDetectionTotals {
  /** Detections aggregated into the window -- the number a "Fire Detections" caption means. */
  detectionCount: number;
  /** Cells drawn. At every rung above z13 this is far smaller than `detectionCount`. */
  cellCount: number;
  highConfidenceDetectionCount: number;
}

const EMPTY_FIRE_DETECTIONS: FireDetectionCollection = {
  type: "FeatureCollection",
  features: [],
};

const EMPTY_FIRE_TOTALS: FireDetectionTotals = {
  detectionCount: 0,
  cellCount: 0,
  highConfidenceDetectionCount: 0,
};

/** `resolveZoomTier` as a render-safe read: an unservable zoom names no rung instead of throwing. */
export function servingZoomTierForMapZoom(mapZoom: number): ZoomTier | null {
  try {
    return resolveZoomTier(mapZoom);
  } catch (error) {
    if (error instanceof ZoomTierResolutionError) return null;
    throw error;
  }
}

/**
 * Published fire-detection cells as drawable points. Cells stay points: a buffered square would
 * assert a burned extent the aggregation never measured.
 *
 * `zoomTier` is passed in rather than read off the payload, which does not carry it. The caller
 * owes the tier that the cells IN HAND were served at -- see `useParquetFireDetections`, which
 * latches the last landed tier so a retained frame is never relabelled with the tier of a
 * request that has not answered yet.
 */
export function presentParquetFireDetections(
  result: ParquetBrowserReaderResult<ParquetBrowserFireWindow> | undefined,
  zoomTier: ZoomTier | null = null
): FireDetectionCollection {
  if (result?.state !== "ready") return EMPTY_FIRE_DETECTIONS;
  return {
    type: "FeatureCollection",
    features: result.data.cells.map((cell) => ({
      type: "Feature" as const,
      id: `${cell.longitude}:${cell.latitude}:${cell.observedDay}`,
      geometry: {
        type: "Point" as const,
        coordinates: [cell.longitude, cell.latitude],
      },
      properties: {
        detectionCount: cell.detectionCount,
        frpSum: cell.frpSum,
        frpObservationCount: cell.frpObservationCount,
        highConfidenceDetectionCount: cell.highConfidenceDetectionCount,
        observedDay: cell.observedDay,
        newestObservedAt: cell.newestObservedAt,
        zoomTier,
      },
    })),
  };
}

/**
 * Totals over a served window. Zero is returned only for a `ready` window that holds no cells:
 * every other state has no total at all, and a caller must render the refusal rather than a 0.
 */
export function fireDetectionTotals(
  result: ParquetBrowserReaderResult<ParquetBrowserFireWindow> | undefined
): FireDetectionTotals {
  if (result?.state !== "ready") return EMPTY_FIRE_TOTALS;
  let detectionCount = 0;
  let highConfidenceDetectionCount = 0;
  for (const cell of result.data.cells) {
    detectionCount += cell.detectionCount;
    highConfidenceDetectionCount += cell.highConfidenceDetectionCount;
  }
  return { detectionCount, cellCount: result.data.cells.length, highConfidenceDetectionCount };
}
