import { z } from "zod";
import type { AggregateEnvelopeSupport } from "@/lib/map/layer-render-contract";
import { BASE_ZOOM_TIER, type ZoomTier } from "@/lib/map/zoom-tiers";
import { isFreshObservation } from "@/lib/server/services/environmental-time";
import {
  getParquetLayerDay,
  getParquetLayerDayWindow,
} from "@/lib/server/services/parquet-plane-client";
import {
  addUtcDays,
  boundedResult,
  cellSupport,
  commonRequest,
  contractError,
  currentUtcDay,
  daySchema,
  finiteNumberSchema,
  instantSchema,
  mapEnvelope,
  newestByKey,
  parseRows,
  windowTerminal,
  type ParquetReaderResult,
  type ParquetViewportRead,
} from "./shared";

const WATER_LIVE_MAX_AGE_MS = 6 * 60 * 60 * 1_000;

const waterGaugeRowSchema = z
  .object({
    site_number: z.string().nullable(),
    observed_at: instantSchema,
    observed_day: daySchema,
    site_name: z.string().nullable(),
    latitude: finiteNumberSchema.nullable(),
    longitude: finiteNumberSchema.nullable(),
    flow_cfs: finiteNumberSchema.nullable(),
    percentile: finiteNumberSchema.nullable(),
    condition: z.string().nullable(),
    trend: z.string().nullable(),
    source: z.string().min(1),
    geometry_linked: z.boolean(),
    data_available_at: instantSchema.nullable(),
    ingested_at: instantSchema,
  })
  .strict();

export interface ParquetWaterGauge {
  siteNumber: string | null;
  observedAt: string;
  observedDay: string;
  siteName: string | null;
  latitude: number | null;
  longitude: number | null;
  flowCfs: number | null;
  percentile: number | null;
  condition: string | null;
  trend: string | null;
  source: string;
  geometryLinked: boolean;
  dataAvailableAt: string | null;
  ingestedAt: string;
  /**
   * What this row stands for on the ground: `raw_point` at the detail rung, `aggregate_cell` on
   * every derived rung. See `src/lib/server/services/AGENTS.md` §water-gauges.
   */
  support: AggregateEnvelopeSupport;
}

function decodeWaterRows(
  rows: readonly Record<string, unknown>[],
  zoomTier: ZoomTier
): ParquetWaterGauge[] {
  return parseRows(rows, waterGaugeRowSchema, "water-gauges").map((row) => ({
    siteNumber: row.site_number,
    observedAt: row.observed_at,
    observedDay: row.observed_day,
    siteName: row.site_name,
    latitude: row.latitude,
    longitude: row.longitude,
    flowCfs: row.flow_cfs,
    percentile: row.percentile,
    condition: row.condition,
    trend: row.trend,
    source: row.source,
    geometryLinked: row.geometry_linked,
    dataAvailableAt: row.data_available_at,
    ingestedAt: row.ingested_at,
    support: cellSupport({
      lane: "water-gauges",
      zoomTier,
      supportKind: zoomTier === BASE_ZOOM_TIER ? "raw_point" : "aggregate_cell",
      // `mean` above the raw rung, never `count`: a coarse cell's `flow_cfs` is mean discharge.
      aggregationMethod: zoomTier === BASE_ZOOM_TIER ? "none" : "mean",
      // One row is one reading; `newestWaterRows` sums these as it folds a cell together.
      contributorCount: 1,
      // An UNLOCATED gauge has no position to mint an id from, so its reading instant is the identity.
      cellId:
        row.site_number ??
        (row.longitude === null || row.latitude === null
          ? `water-gauges:${row.observed_day}:${row.observed_at}`
          : null),
      longitude: row.longitude ?? 0,
      latitude: row.latitude ?? 0,
      observedDay: row.observed_day,
      newestObservedAt: row.observed_at,
    }),
  }));
}

/**
 * The newest reading per gauge or per cell, carrying the COUNT of everything folded into it.
 *
 * See `src/lib/server/services/AGENTS.md` §water-gauges for why the fold owns the contributor count.
 */
function newestWaterRows(rows: readonly ParquetWaterGauge[]): ParquetWaterGauge[] {
  const keyFor = (row: ParquetWaterGauge) =>
    row.siteNumber ?? `${row.longitude}:${row.latitude}`;
  const foldedCounts = new Map<string, number>();
  for (const row of rows) {
    const key = keyFor(row);
    foldedCounts.set(key, (foldedCounts.get(key) ?? 0) + row.support.contributorCount);
  }
  return newestByKey(rows, keyFor, (row) => row.observedAt).map((row) => ({
    ...row,
    support: {
      ...row.support,
      contributorCount: foldedCounts.get(keyFor(row)) ?? row.support.contributorCount,
    },
  }));
}

export async function getParquetWaterGauges(
  input: ParquetViewportRead & { bbox: string }
): Promise<ParquetReaderResult<readonly ParquetWaterGauge[]>> {
  const nowMs = input.nowMs ?? Date.now();
  const { day, request } = commonRequest({ ...input, nowMs }, "water-gauges");
  if (day !== currentUtcDay(nowMs)) {
    return boundedResult(async () =>
      mapEnvelope(await getParquetLayerDay(request), (rows) =>
        newestWaterRows(decodeWaterRows(rows, request.zoomTier))
      )
    );
  }

  return boundedResult(async () => {
    const firstDay = addUtcDays(day, -1);
    const envelopes = await getParquetLayerDayWindow({
      layer: "water-gauges",
      firstDay,
      lastDay: day,
      zoomTier: request.zoomTier,
      bbox: input.bbox,
      ...(input.signal === undefined ? {} : { signal: input.signal }),
    });
    const days = envelopes.map((envelope) =>
      mapEnvelope(envelope, (rows) => decodeWaterRows(rows, request.zoomTier))
    );
    const published = days.filter(
      (entry): entry is Extract<(typeof days)[number], { state: "ready" }> => entry.state === "ready"
    );
    if (published.length === 0) return windowTerminal(days, firstDay, day);
    const data = newestWaterRows(
      published
        .flatMap((entry) => entry.data)
        .filter((row) => isFreshObservation(row.observedAt, WATER_LIVE_MAX_AGE_MS, nowMs))
    );
    if (data.length === 0) {
      const current = days.find((entry) =>
        "requestedDay" in entry ? entry.requestedDay === day : false
      );
      if (current === undefined) {
        throw contractError(`water-gauges window omitted its requested current day ${day}`);
      }
      if (published.some((entry) => entry.truncated)) {
        return {
          state: "upstream_unavailable",
          fault: {
            kind: "contract",
            message: "The truncated water live window could not prove whether a fresh row exists",
          },
        };
      }
      if (current.state === "ready") return { ...current, data: [] };
      return current;
    }
    const freshest = data.reduce((newest, row) =>
      Date.parse(row.observedAt) > Date.parse(newest.observedAt) ? row : newest
    );
    return {
      state: "ready",
      requestedDay: day,
      // The served day is the freshest row's OWN published day string, never a UTC conversion of
      // its instant; see `src/lib/server/services/AGENTS.md` §named-day-rule.
      servedDay: freshest.observedDay,
      data,
      truncated: published.some((entry) => entry.truncated),
    };
  });
}
