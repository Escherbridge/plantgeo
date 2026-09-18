import { z } from "zod";
import type { AggregateEnvelopeSupport } from "@/lib/map/layer-render-contract";
import { resolveZoomTier } from "@/lib/map/zoom-tiers";
import { getParquetLayerDayWindow } from "@/lib/server/services/parquet-plane-client";
import {
  addUtcDays,
  boundedResult,
  cellSupport,
  daySchema,
  finiteNumberSchema,
  instantSchema,
  mapEnvelope,
  parseRows,
  rejectFutureDay,
  selectedDay,
  windowTerminal,
  type ParquetReaderResult,
  type ParquetViewportRead,
} from "./shared";

export const VEGETATION_TRAILING_DAYS = 30;

const vegetationRowSchema = z
  .object({
    cell_id: z.string().nullable(),
    grid_name: z.string().min(1),
    metric_name: z.string().min(1),
    metric_unit: z.string().min(1),
    observed_day: daySchema,
    metric_value: finiteNumberSchema,
    observation_checksum: z.string().nullable(),
    data_available_at: instantSchema,
    release_count: z.number().int().nonnegative(),
    allowed_client_exposure: z.literal(true),
    cell_longitude: finiteNumberSchema,
    cell_latitude: finiteNumberSchema,
  })
  .strict();

export interface ParquetVegetationObservation {
  cellId: string | null;
  gridName: string;
  metricName: string;
  metricUnit: string;
  observedDay: string;
  metricValue: number;
  observationChecksum: string | null;
  dataAvailableAt: string;
  releaseCount: number;
  allowedClientExposure: true;
  longitude: number;
  latitude: number;
  /** The quarter-degree cell this NDVI mean describes, at every rung. */
  support: AggregateEnvelopeSupport;
}

export interface ParquetVegetationWindow {
  firstDay: string;
  lastDay: string;
  observations: readonly ParquetVegetationObservation[];
  days: readonly ParquetReaderResult<readonly ParquetVegetationObservation[]>[];
}

/** The newest NDVI cell in a trailing 30-day window, plus each day's own read state. */
export async function getParquetVegetation(
  input: ParquetViewportRead & { bbox: string }
): Promise<ParquetReaderResult<ParquetVegetationWindow>> {
  const nowMs = input.nowMs ?? Date.now();
  const lastDay = selectedDay(input.date, nowMs);
  rejectFutureDay(lastDay, nowMs, "vegetation");
  const firstDay = addUtcDays(lastDay, -(VEGETATION_TRAILING_DAYS - 1));
  const zoomTier = resolveZoomTier(input.mapZoom);
  return boundedResult(async () => {
    const envelopes = await getParquetLayerDayWindow({
      layer: "vegetation",
      firstDay,
      lastDay,
      zoomTier,
      bbox: input.bbox,
      ...(input.signal === undefined ? {} : { signal: input.signal }),
    });
    const days = envelopes.map((envelope) =>
      mapEnvelope(envelope, (rows) =>
        parseRows(rows, vegetationRowSchema, "vegetation").map((row) => ({
          cellId: row.cell_id,
          gridName: row.grid_name,
          metricName: row.metric_name,
          metricUnit: row.metric_unit,
          observedDay: row.observed_day,
          metricValue: row.metric_value,
          observationChecksum: row.observation_checksum,
          dataAvailableAt: row.data_available_at,
          releaseCount: row.release_count,
          allowedClientExposure: row.allowed_client_exposure,
          longitude: row.cell_longitude,
          latitude: row.cell_latitude,
          support: cellSupport({
            lane: "vegetation",
            zoomTier,
            // `tessellated_cell`, never `raw_point`: a centre dot for a quarter-degree measurement
            // is the fictitious footprint the render contract forbids.
            supportKind: "tessellated_cell",
            aggregationMethod: "mean",
            contributorCount: row.release_count,
            cellId: row.cell_id,
            longitude: row.cell_longitude,
            latitude: row.cell_latitude,
            observedDay: row.observed_day,
            // The lane publishes an AVAILABILITY instant, never an observation one, so the
            // envelope says null rather than passing a publication time off as a measurement time.
            newestObservedAt: null,
          }),
        }))
      )
    );
    const published = days.filter(
      (day): day is Extract<(typeof days)[number], { state: "ready" }> => day.state === "ready"
    );
    if (published.length === 0) return windowTerminal(days, firstDay, lastDay);

    const newestByCell = new Map<string, ParquetVegetationObservation>();
    for (const day of published) {
      for (const row of day.data) {
        const key = row.cellId ?? `${row.longitude}:${row.latitude}`;
        newestByCell.set(key, row);
      }
    }
    const newest = published[published.length - 1];
    return {
      state: "ready",
      requestedDay: lastDay,
      servedDay: newest.servedDay,
      data: { firstDay, lastDay, observations: [...newestByCell.values()], days },
      truncated: published.some((day) => day.truncated),
    };
  });
}
