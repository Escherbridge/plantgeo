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

const WEATHER_LIVE_MAX_AGE_MS = 3 * 60 * 60 * 1_000;

const weatherRowSchema = z
  .object({
    latitude: finiteNumberSchema,
    longitude: finiteNumberSchema,
    observed_at: instantSchema,
    observed_day: daySchema,
    external_id: z.string().nullable(),
    temperature_c: finiteNumberSchema,
    relative_humidity_pct: finiteNumberSchema,
    wind_speed_ms: finiteNumberSchema,
    wind_direction_deg: finiteNumberSchema.nullable(),
    precipitation_mm: finiteNumberSchema,
    source: z.string().min(1),
    feature_id: z.string().nullable(),
    ingested_at: instantSchema,
  })
  .strict();

export interface ParquetWeatherObservation {
  latitude: number;
  longitude: number;
  observedAt: string;
  observedDay: string;
  externalId: string | null;
  temperatureC: number;
  relativeHumidityPct: number;
  windSpeedMs: number;
  windDirectionDeg: number | null;
  precipitationMm: number;
  source: string;
  featureId: string | null;
  ingestedAt: string;
  /**
   * What this row stands for on the ground, on the same rule as the gauges.
   * See `src/lib/server/services/AGENTS.md` §weather-observations.
   */
  support: AggregateEnvelopeSupport;
}

function decodeWeatherRows(
  rows: readonly Record<string, unknown>[],
  zoomTier: ZoomTier
): ParquetWeatherObservation[] {
  return parseRows(rows, weatherRowSchema, "weather-observations").map((row) => ({
    latitude: row.latitude,
    longitude: row.longitude,
    observedAt: row.observed_at,
    observedDay: row.observed_day,
    externalId: row.external_id,
    temperatureC: row.temperature_c,
    relativeHumidityPct: row.relative_humidity_pct,
    windSpeedMs: row.wind_speed_ms,
    windDirectionDeg: row.wind_direction_deg,
    precipitationMm: row.precipitation_mm,
    source: row.source,
    featureId: row.feature_id,
    ingestedAt: row.ingested_at,
    support: cellSupport({
      lane: "weather-observations",
      zoomTier,
      supportKind: zoomTier === BASE_ZOOM_TIER ? "raw_point" : "aggregate_cell",
      // `mean` above the raw rung: a derived row carries averaged temperature, humidity and wind.
      aggregationMethod: zoomTier === BASE_ZOOM_TIER ? "none" : "mean",
      // One row is one observation; the lane publishes no observation-count column.
      contributorCount: 1,
      cellId: row.external_id ?? row.feature_id,
      longitude: row.longitude,
      latitude: row.latitude,
      observedDay: row.observed_day,
      newestObservedAt: row.observed_at,
    }),
  }));
}

function newestWeatherRows(rows: readonly ParquetWeatherObservation[]): ParquetWeatherObservation[] {
  return newestByKey(
    rows,
    (row) => `${row.longitude}:${row.latitude}`,
    (row) => row.observedAt
  );
}

export async function getParquetWeatherObservations(
  input: ParquetViewportRead & { bbox: string }
): Promise<ParquetReaderResult<readonly ParquetWeatherObservation[]>> {
  const nowMs = input.nowMs ?? Date.now();
  const { day, request } = commonRequest({ ...input, nowMs }, "weather-observations");
  if (day !== currentUtcDay(nowMs)) {
    return boundedResult(async () =>
      mapEnvelope(await getParquetLayerDay(request), (rows) =>
        newestWeatherRows(decodeWeatherRows(rows, request.zoomTier))
      )
    );
  }

  return boundedResult(async () => {
    const firstDay = addUtcDays(day, -1);
    const envelopes = await getParquetLayerDayWindow({
      layer: "weather-observations",
      firstDay,
      lastDay: day,
      zoomTier: request.zoomTier,
      bbox: input.bbox,
      ...(input.signal === undefined ? {} : { signal: input.signal }),
    });
    const days = envelopes.map((envelope) =>
      mapEnvelope(envelope, (rows) => decodeWeatherRows(rows, request.zoomTier))
    );
    const published = days.filter(
      (entry): entry is Extract<(typeof days)[number], { state: "ready" }> => entry.state === "ready"
    );
    if (published.length === 0) return windowTerminal(days, firstDay, day);

    const data = newestWeatherRows(
      published
        .flatMap((entry) => entry.data)
        .filter((row) => isFreshObservation(row.observedAt, WEATHER_LIVE_MAX_AGE_MS, nowMs))
    );
    if (data.length === 0) {
      const current = days.find((entry) =>
        "requestedDay" in entry ? entry.requestedDay === day : false
      );
      if (current === undefined) {
        throw contractError(`weather-observations window omitted its requested current day ${day}`);
      }
      if (published.some((entry) => entry.truncated)) {
        return {
          state: "upstream_unavailable",
          fault: {
            kind: "contract",
            message: "The truncated weather live window could not prove whether a fresh row exists",
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
      // The row's own published day string, never a UTC conversion of its instant.
      servedDay: freshest.observedDay,
      data,
      truncated: published.some((entry) => entry.truncated),
    };
  });
}
