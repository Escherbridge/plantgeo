import {
  resolveZoomTier,
  ZoomTierResolutionError,
  type ZoomTier,
} from "@/lib/map/zoom-tiers";
import {
  assertNotPerimeter,
  supportCellPolygon,
  zoomBandForTier,
  type AggregateEnvelopeSupport,
  type SupportKind,
} from "@/lib/map/layer-render-contract";
import type { ParquetBrowserReaderResult } from "@/lib/environmental/parquet-presentation";

/**
 * Browser mirror of one published fire-detection aggregation cell.
 *
 * `support` is REQUIRED, here as on the wire: `getFireDetections` declares an `aggregate_cell`
 * envelope at every rung, because FIRMS publishes no raw one. It was optional here until
 * 2026-09-02 so that loosely typed mocks would compile, and the cost was that an envelope-less
 * payload degraded silently to the wave-1 marker with nothing failing to compile -- the exact
 * regression the envelope exists to make impossible. What a renderer must never do is fall back to
 * a GUESSED cell size: a fabricated square is the fire-perimeter claim this whole track exists to
 * prevent, and an envelope that declares no size still yields the marker below.
 */
export interface ParquetBrowserFireDetectionCell {
  longitude: number;
  latitude: number;
  observedDay: string;
  detectionCount: number;
  frpSum: number | null;
  frpObservationCount: number;
  highConfidenceDetectionCount: number;
  newestObservedAt: string;
  support: AggregateEnvelopeSupport;
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
  /** The serving rung these cells were aggregated at, off the cell's own envelope. */
  zoomTier: ZoomTier;
  /** The form the envelope declares this feature stands for; `aggregate_cell` at every rung. */
  supportKind: SupportKind;
  /** The envelope's stable identity, carried so a drawn cell can be named. Never invented. */
  supportId: string;
  /** The declared cell width in degrees; null when none was declared. Never inferred. */
  cellWidthDegrees: number | null;
  /** The declared cell height in degrees; null when none was declared. Never inferred. */
  cellHeightDegrees: number | null;
};

/**
 * Both geometries one collection may hold, because the band decides the shape: the declared
 * square at coarse and middle zoom, a marker at detail zoom (and for any cell whose envelope
 * declared no size). `FireLayer` separates them with `["==", ["geometry-type"], …]` filters
 * rather than with two sources, so one rung's cells never draw twice.
 */
export type FireDetectionCollection = GeoJSON.FeatureCollection<
  GeoJSON.Point | GeoJSON.Polygon,
  FireDetectionCellProperties
>;

/**
 * The one form fire cells are ever drawn in, at every rung.
 *
 * `LAYER_RENDER_CONTRACT.fire` permits `aggregate_cell` in all three bands and permits
 * `native_polygon` in none: a cell says "n hotspots were detected in this square", never "this
 * square burned". The square drawn at coarse and middle zoom and the dot drawn at detail zoom are
 * two RENDERINGS of that one form, not two forms.
 */
export const FIRE_CELL_DRAWN_FORM: SupportKind = "aggregate_cell";

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
 * Published fire-detection cells as drawable features, in the shape their own band calls for.
 *
 * **Coarse and middle zoom draw the DECLARED square**, built by `supportCellPolygon` from the
 * envelope's own origin and cell size. It is not a buffer around a point and its size is never
 * guessed: an envelope that declares no size gets the marker below instead. Adjacent squares share
 * bit-identical edges, so a continental view reads as a density surface rather than as the
 * scattering of individual fires the 2026-09-01 assessment found.
 *
 * **Detail zoom keeps the marker**, because a z13 cell is small enough that the dot sits inside
 * its own square and the count-scaled radius stays the more legible encoding. The caption says
 * "Aggregated at z13" there, which is the whole reason the rung travels on the feature.
 *
 * A square is never a perimeter. `assertNotPerimeter` guards the drawn form on every call: if
 * anything ever changes `FIRE_CELL_DRAWN_FORM` to `native_polygon`, this throws instead of
 * publishing a burned extent nobody measured.
 *
 * **The rung comes from each cell's own envelope and from nowhere else.** Until 2026-09-02 the
 * caller also passed its LATCHED rung as a fallback -- `useParquetFireDetections` holds the last
 * one that actually landed -- for cells that carried no envelope. Every cell carries one now, so
 * the fallback could never fire, and a parameter that can only ever be ignored is a second answer
 * to a question with one. A retained frame is still not relabelled with a rung still in flight,
 * for the stronger reason: its cells state the rung they were really aggregated at.
 */
export function presentParquetFireDetections(
  result: ParquetBrowserReaderResult<ParquetBrowserFireWindow> | undefined
): FireDetectionCollection {
  assertNotPerimeter("fire", FIRE_CELL_DRAWN_FORM);
  if (result?.state !== "ready") return EMPTY_FIRE_DETECTIONS;
  return {
    type: "FeatureCollection",
    features: result.data.cells.map((cell) => {
      const support = cell.support;
      const zoomTier = support.zoomTier;
      const declaredPolygon =
        zoomBandForTier(zoomTier) === "detail"
          ? null
          : supportCellPolygon(cell.longitude, cell.latitude, support);
      return {
        type: "Feature" as const,
        id: support.supportId,
        geometry:
          declaredPolygon ??
          { type: "Point" as const, coordinates: [cell.longitude, cell.latitude] },
        properties: {
          detectionCount: cell.detectionCount,
          frpSum: cell.frpSum,
          frpObservationCount: cell.frpObservationCount,
          highConfidenceDetectionCount: cell.highConfidenceDetectionCount,
          observedDay: cell.observedDay,
          newestObservedAt: cell.newestObservedAt,
          zoomTier,
          supportKind: support.supportKind,
          supportId: support.supportId,
          cellWidthDegrees: support.cellWidthDegrees ?? null,
          cellHeightDegrees: support.cellHeightDegrees ?? null,
        },
      };
    }),
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
