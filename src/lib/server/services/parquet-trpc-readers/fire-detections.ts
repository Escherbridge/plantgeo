import { z } from "zod";
import type { AggregateEnvelopeSupport } from "@/lib/map/layer-render-contract";
import { resolveZoomTier } from "@/lib/map/zoom-tiers";
import {
  getParquetLayerDayWindow,
  ParquetPlaneRequestError,
} from "@/lib/server/services/parquet-plane-client";
import {
  addUtcDays,
  boundedResult,
  cellSupport,
  daySchema,
  finiteNumberSchema,
  instantSchema,
  mapEnvelope,
  parseRows,
  selectedDay,
  windowTerminal,
  type ParquetReaderResult,
  type ParquetViewportRead,
} from "./shared";

const MAX_FIRE_DETECTION_DAY_RANGE = 10;

const fireDetectionRowSchema = z
  .object({
    cell_longitude: finiteNumberSchema,
    cell_latitude: finiteNumberSchema,
    observed_day: daySchema,
    detection_count: z.number().int().nonnegative(),
    frp_sum: finiteNumberSchema.nullable(),
    frp_observation_count: z.number().int().nonnegative(),
    high_confidence_detection_count: z.number().int().nonnegative(),
    newest_observed_at: instantSchema,
  })
  .strict();

export interface ParquetFireDetectionCell {
  longitude: number;
  latitude: number;
  observedDay: string;
  detectionCount: number;
  frpSum: number | null;
  frpObservationCount: number;
  highConfidenceDetectionCount: number;
  newestObservedAt: string;
  /**
   * `aggregate_cell` at EVERY rung: FIRMS publishes no raw rung, so even a z13 row is a
   * detection-density cell rather than one hotspot.
   */
  support: AggregateEnvelopeSupport;
}

export interface ParquetFireWindow {
  firstDay: string;
  lastDay: string;
  cells: readonly ParquetFireDetectionCell[];
  days: readonly ParquetReaderResult<readonly ParquetFireDetectionCell[]>[];
}

/** FIRMS detection-density cells over a bounded trailing window; an explicit date pins it to one day. */
export async function getParquetFireDetections(
  input: ParquetViewportRead & { dayRange?: number }
): Promise<ParquetReaderResult<ParquetFireWindow>> {
  const lastDay = selectedDay(input.date, input.nowMs);
  const requestedRange = input.date === undefined ? input.dayRange ?? 1 : 1;
  if (
    !Number.isInteger(requestedRange) ||
    requestedRange < 1 ||
    requestedRange > MAX_FIRE_DETECTION_DAY_RANGE
  ) {
    throw new ParquetPlaneRequestError(
      `dayRange must be an integer from 1 through ${MAX_FIRE_DETECTION_DAY_RANGE}, got ${requestedRange}`
    );
  }
  const firstDay = addUtcDays(lastDay, -(requestedRange - 1));
  const zoomTier = resolveZoomTier(input.mapZoom);
  return boundedResult(async () => {
    const envelopes = await getParquetLayerDayWindow({
      layer: "fire-detections",
      firstDay,
      lastDay,
      zoomTier,
      ...(input.bbox === undefined ? {} : { bbox: input.bbox }),
      ...(input.signal === undefined ? {} : { signal: input.signal }),
    });
    const days = envelopes.map((envelope) =>
      mapEnvelope(envelope, (rows) =>
        parseRows(rows, fireDetectionRowSchema, "fire-detections").map((row) => ({
          longitude: row.cell_longitude,
          latitude: row.cell_latitude,
          observedDay: row.observed_day,
          detectionCount: row.detection_count,
          frpSum: row.frp_sum,
          frpObservationCount: row.frp_observation_count,
          highConfidenceDetectionCount: row.high_confidence_detection_count,
          newestObservedAt: row.newest_observed_at,
          support: cellSupport({
            lane: "fire-detections",
            zoomTier,
            supportKind: "aggregate_cell",
            aggregationMethod: "count",
            contributorCount: row.detection_count,
            // The lane publishes no cell identity at any rung: its grain IS the snapped
            // coordinate pair, so the id is minted from the rung and that pair.
            cellId: null,
            longitude: row.cell_longitude,
            latitude: row.cell_latitude,
            observedDay: row.observed_day,
            newestObservedAt: row.newest_observed_at,
          }),
        }))
      )
    );
    const published = days.filter(
      (day): day is Extract<(typeof days)[number], { state: "ready" }> => day.state === "ready"
    );
    if (published.length === 0) return windowTerminal(days, firstDay, lastDay);
    const newest = published[published.length - 1];
    return {
      state: "ready",
      requestedDay: lastDay,
      servedDay: newest.servedDay,
      data: { firstDay, lastDay, cells: published.flatMap((day) => day.data), days },
      truncated: published.some((day) => day.truncated),
    };
  });
}
