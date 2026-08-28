import { z } from "zod";
import { resolveZoomTier } from "@/lib/map/zoom-tiers";
import { isFreshObservation } from "@/lib/server/services/environmental-time";
import {
  UpstreamConfigurationError,
  UpstreamHttpError,
  UpstreamPayloadError,
  UpstreamTimeoutError,
} from "@/lib/server/http/bounded-upstream";
import {
  getParquetLatestRelease,
  getParquetLayerDay,
  getParquetLayerDayWindow,
  ParquetPlaneContractError,
  ParquetPlaneRequestError,
} from "@/lib/server/services/parquet-plane-client";
import {
  assertExhaustiveParquetPlaneState,
  type GovernedAbsenceEvidence,
  type ParquetPlaneEnvelope,
} from "@/lib/server/services/parquet-envelope";

const DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const DAY_MS = 86_400_000;
const WATER_LIVE_MAX_AGE_MS = 6 * 60 * 60 * 1_000;
const WEATHER_LIVE_MAX_AGE_MS = 3 * 60 * 60 * 1_000;
const DROUGHT_RELEASE_INTERVAL_DAYS = 7;
const DROUGHT_MAX_CARRY_FORWARD_DAYS = 14;
export const VEGETATION_TRAILING_DAYS = 30;

export type ParquetReaderFailureKind =
  | "configuration"
  | "http"
  | "network"
  | "payload"
  | "timeout"
  | "contract";

export type ParquetReaderResult<T> =
  | {
      state: "ready";
      requestedDay: string;
      servedDay: string;
      data: T;
      truncated: boolean;
    }
  | {
      state: "absent";
      requestedDay: string;
      servedDay: string;
      evidence: GovernedAbsenceEvidence;
    }
  | {
      state: "not_generated";
      requestedDay: string;
      reason: "day_not_written" | "lane_never_written";
    }
  | {
      state: "upstream_unavailable";
      fault: {
        kind: ParquetReaderFailureKind;
        message: string;
        status?: number;
      };
    };

export interface ParquetViewportRead {
  bbox?: string;
  date?: string;
  mapZoom: number;
  /** Test seam for omitted-day selection; production callers leave it unset. */
  nowMs?: number;
}

const daySchema = z.string().regex(DAY_PATTERN);
const instantSchema = z
  .string()
  .endsWith("Z")
  .refine((value) => Number.isFinite(Date.parse(value)), "Expected a UTC instant");
const finiteNumberSchema = z.number().finite();

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
}

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
}

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
}

const positionSchema = z.tuple([finiteNumberSchema, finiteNumberSchema]).rest(finiteNumberSchema);
const ringSchema = z.array(positionSchema).min(4);
const polygonCoordinatesSchema = z.array(ringSchema).min(1);
const droughtGeometrySchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("Polygon"), coordinates: polygonCoordinatesSchema }).strict(),
  z
    .object({ type: z.literal("MultiPolygon"), coordinates: z.array(polygonCoordinatesSchema).min(1) })
    .strict(),
]);

const droughtRowSchema = z
  .object({
    area_id: z.string().min(1),
    valid_date: daySchema,
    dm_category: z.number().int().min(0).max(4),
    source_url: z.string().min(1),
    ingested_at: instantSchema,
    geom: z.string().min(1),
  })
  .strict();

function decodeDroughtGeometry(geojson: string): z.infer<typeof droughtGeometrySchema> {
  let rawGeometry: unknown;
  try {
    rawGeometry = JSON.parse(geojson);
  } catch {
    throw contractError("drought geom is not GeoJSON text");
  }
  const geometry = droughtGeometrySchema.safeParse(rawGeometry);
  if (!geometry.success) throw contractError("drought geom is not a Polygon or MultiPolygon");
  return geometry.data;
}

export interface ParquetDroughtArea {
  areaId: string;
  validDate: string;
  droughtCategory: 0 | 1 | 2 | 3 | 4;
  sourceUrl: string;
  ingestedAt: string;
  geometry: z.infer<typeof droughtGeometrySchema>;
}

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
}

export interface ParquetVegetationWindow {
  firstDay: string;
  lastDay: string;
  observations: readonly ParquetVegetationObservation[];
  days: readonly ParquetReaderResult<readonly ParquetVegetationObservation[]>[];
}

export interface ParquetFireWindow {
  firstDay: string;
  lastDay: string;
  cells: readonly ParquetFireDetectionCell[];
  days: readonly ParquetReaderResult<readonly ParquetFireDetectionCell[]>[];
}

function currentUtcDay(nowMs = Date.now()): string {
  return new Date(nowMs).toISOString().slice(0, 10);
}

function selectedDay(date: string | undefined, nowMs: number | undefined): string {
  const day = date ?? currentUtcDay(nowMs);
  const parsedMs = Date.parse(`${day}T00:00:00Z`);
  if (
    !DAY_PATTERN.test(day) ||
    Number.isNaN(parsedMs) ||
    new Date(parsedMs).toISOString().slice(0, 10) !== day
  ) {
    throw new ParquetPlaneRequestError(`date must be a YYYY-MM-DD calendar day, got "${day}"`);
  }
  return day;
}

function addUtcDays(day: string, count: number): string {
  return new Date(Date.parse(`${day}T00:00:00Z`) + count * DAY_MS).toISOString().slice(0, 10);
}

function rejectFutureDay(day: string, nowMs: number, layer: string): void {
  const today = currentUtcDay(nowMs);
  if (day > today) {
    throw new ParquetPlaneRequestError(
      `${layer} cannot read future day ${day}; server UTC today is ${today}`
    );
  }
}

function envelopeDayMs(day: string, field: string): number {
  const parsedMs = Date.parse(`${day}T00:00:00Z`);
  if (
    !DAY_PATTERN.test(day) ||
    Number.isNaN(parsedMs) ||
    new Date(parsedMs).toISOString().slice(0, 10) !== day
  ) {
    throw contractError(`Parquet plane returned an invalid ${field} calendar day "${day}"`);
  }
  return parsedMs;
}

function releaseServedDay(envelope: ParquetPlaneEnvelope, asOfDay: string): string {
  if (envelope.state !== "published" && envelope.state !== "governed_absence") {
    throw contractError(
      `drought release lookup for ${asOfDay} contradicted an earlier published release with ${envelope.state}`
    );
  }
  return envelope.servedDay;
}

function contractError(message: string): ParquetPlaneContractError {
  return new ParquetPlaneContractError(message);
}

function parseRows<T>(rows: readonly Record<string, unknown>[], schema: z.ZodType<T>, layer: string): T[] {
  const parsed = z.array(schema).safeParse(rows);
  if (!parsed.success) {
    throw contractError(`${layer} rows do not match the registered Parquet schema`);
  }
  return parsed.data;
}

function newestByKey<T>(
  rows: readonly T[],
  keyFor: (row: T) => string,
  observedAtFor: (row: T) => string
): T[] {
  const newest = new Map<string, T>();
  for (const row of rows) {
    const key = keyFor(row);
    const previous = newest.get(key);
    if (
      previous === undefined ||
      Date.parse(observedAtFor(row)) > Date.parse(observedAtFor(previous))
    ) {
      newest.set(key, row);
    }
  }
  return [...newest.values()];
}

function mapEnvelope<T>(
  envelope: ParquetPlaneEnvelope,
  decodeRows: (rows: readonly Record<string, unknown>[]) => T
): ParquetReaderResult<T> {
  switch (envelope.state) {
    case "published":
      return {
        state: "ready",
        requestedDay: envelope.requestedDay,
        servedDay: envelope.servedDay,
        data: decodeRows(envelope.rows),
        truncated: envelope.truncated,
      };
    case "governed_absence":
      return {
        state: "absent",
        requestedDay: envelope.requestedDay,
        servedDay: envelope.servedDay,
        evidence: envelope.evidence,
      };
    case "day_not_written":
      return {
        state: "not_generated",
        requestedDay: envelope.requestedDay,
        reason: "day_not_written",
      };
    case "lane_never_written":
      return {
        state: "not_generated",
        requestedDay: envelope.requestedDay,
        reason: "lane_never_written",
      };
    default:
      return assertExhaustiveParquetPlaneState(envelope);
  }
}

function upstreamFailure(error: unknown): ParquetReaderResult<never> | null {
  if (error instanceof UpstreamConfigurationError) {
    return { state: "upstream_unavailable", fault: { kind: "configuration", message: error.message } };
  }
  if (error instanceof UpstreamHttpError) {
    return {
      state: "upstream_unavailable",
      fault: { kind: "http", message: error.message, status: error.status },
    };
  }
  if (error instanceof UpstreamPayloadError) {
    return { state: "upstream_unavailable", fault: { kind: "payload", message: error.message } };
  }
  if (error instanceof UpstreamTimeoutError) {
    return { state: "upstream_unavailable", fault: { kind: "timeout", message: error.message } };
  }
  if (
    error instanceof TypeError &&
    /(?:fetch failed|failed to fetch|networkerror|network request failed)/i.test(error.message)
  ) {
    return { state: "upstream_unavailable", fault: { kind: "network", message: error.message } };
  }
  if (error instanceof ParquetPlaneContractError) {
    return { state: "upstream_unavailable", fault: { kind: "contract", message: error.message } };
  }
  return null;
}

async function boundedResult<T>(work: () => Promise<ParquetReaderResult<T>>): Promise<ParquetReaderResult<T>> {
  try {
    return await work();
  } catch (error) {
    const failure = upstreamFailure(error);
    if (failure !== null) return failure;
    throw error;
  }
}

function commonRequest(input: ParquetViewportRead, layer: string) {
  const day = selectedDay(input.date, input.nowMs);
  const zoomTier = resolveZoomTier(input.mapZoom);
  return { day, request: { layer, day, zoomTier, ...(input.bbox === undefined ? {} : { bbox: input.bbox }) } };
}

function decodeWaterRows(rows: readonly Record<string, unknown>[]): ParquetWaterGauge[] {
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
  }));
}

function newestWaterRows(rows: readonly ParquetWaterGauge[]): ParquetWaterGauge[] {
  return newestByKey(
    rows,
    (row) => row.siteNumber ?? `${row.longitude}:${row.latitude}`,
    (row) => row.observedAt
  );
}

function decodeWeatherRows(rows: readonly Record<string, unknown>[]): ParquetWeatherObservation[] {
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
  }));
}

function newestWeatherRows(rows: readonly ParquetWeatherObservation[]): ParquetWeatherObservation[] {
  return newestByKey(
    rows,
    (row) => `${row.longitude}:${row.latitude}`,
    (row) => row.observedAt
  );
}

export async function getParquetWaterGauges(
  input: ParquetViewportRead & { bbox: string }
): Promise<ParquetReaderResult<readonly ParquetWaterGauge[]>> {
  const nowMs = input.nowMs ?? Date.now();
  const { day, request } = commonRequest({ ...input, nowMs }, "water-gauges");
  if (day !== currentUtcDay(nowMs)) {
    return boundedResult(async () =>
      mapEnvelope(await getParquetLayerDay(request), (rows) => newestWaterRows(decodeWaterRows(rows)))
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
    });
    const days = envelopes.map((envelope) => mapEnvelope(envelope, decodeWaterRows));
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
      servedDay: freshest.observedDay,
      data,
      truncated: published.some((entry) => entry.truncated),
    };
  });
}

export async function getParquetWeatherObservations(
  input: ParquetViewportRead & { bbox: string }
): Promise<ParquetReaderResult<readonly ParquetWeatherObservation[]>> {
  const nowMs = input.nowMs ?? Date.now();
  const { day, request } = commonRequest({ ...input, nowMs }, "weather-observations");
  if (day !== currentUtcDay(nowMs)) {
    return boundedResult(async () =>
      mapEnvelope(await getParquetLayerDay(request), (rows) => newestWeatherRows(decodeWeatherRows(rows)))
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
    });
    const days = envelopes.map((envelope) => mapEnvelope(envelope, decodeWeatherRows));
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
      servedDay: freshest.observedDay,
      data,
      truncated: published.some((entry) => entry.truncated),
    };
  });
}

export async function getParquetDrought(
  input: ParquetViewportRead
): Promise<ParquetReaderResult<readonly ParquetDroughtArea[]>> {
  const nowMs = input.nowMs ?? Date.now();
  const day = selectedDay(input.date, nowMs);
  rejectFutureDay(day, nowMs, "drought");
  const zoomTier = resolveZoomTier(input.mapZoom);
  return boundedResult(async () => {
    const releaseRequest = {
      layer: "drought",
      zoomTier,
      ...(input.bbox === undefined ? {} : { bbox: input.bbox }),
    } as const;
    const envelope = await getParquetLatestRelease({
      ...releaseRequest,
      asOfDay: day,
    });
    const result = mapEnvelope(
      envelope,
      (rows) =>
        parseRows(rows, droughtRowSchema, "drought").map((row) => ({
          areaId: row.area_id,
          validDate: row.valid_date,
          droughtCategory: row.dm_category as 0 | 1 | 2 | 3 | 4,
          sourceUrl: row.source_url,
          ingestedAt: row.ingested_at,
          geometry: decodeDroughtGeometry(row.geom),
        }))
    );
    if (result.state !== "ready" && result.state !== "absent") return result;

    const requestedMs = envelopeDayMs(day, "requested");
    const servedMs = envelopeDayMs(result.servedDay, "served");
    const carryForwardDays = Math.round((requestedMs - servedMs) / DAY_MS);
    if (carryForwardDays < 0) {
      throw contractError(`drought release ${result.servedDay} is after requested day ${day}`);
    }
    if (result.state === "ready" && result.data.some((row) => row.validDate !== result.servedDay)) {
      throw contractError("drought rows do not match the release day reported by the Parquet plane");
    }

    const today = currentUtcDay(nowMs);
    let maxCarryForwardDays = DROUGHT_MAX_CARRY_FORWARD_DAYS;
    if (day < today) {
      const latestEnvelope = await getParquetLatestRelease({
        ...releaseRequest,
        asOfDay: today,
      });
      const latestServedDay = releaseServedDay(latestEnvelope, today);
      const latestServedMs = envelopeDayMs(latestServedDay, "latest served");
      if (latestServedMs > envelopeDayMs(today, "latest requested")) {
        throw contractError(`drought release ${latestServedDay} is after server UTC today ${today}`);
      }
      if (latestServedMs > servedMs && latestServedMs <= requestedMs) {
        throw contractError(
          `drought lookup for ${day} skipped the newer stored release ${latestServedDay}`
        );
      }
      if (latestServedMs > servedMs) {
        maxCarryForwardDays = DROUGHT_RELEASE_INTERVAL_DAYS - 1;
      }
    }
    if (carryForwardDays > maxCarryForwardDays) {
      return { state: "not_generated", requestedDay: day, reason: "day_not_written" };
    }
    return result;
  });
}

function windowTerminal<T>(
  days: readonly ParquetReaderResult<T>[],
  firstDay: string,
  lastDay: string
): Exclude<ParquetReaderResult<never>, { state: "ready" | "upstream_unavailable" }> {
  const missing = [...days]
    .reverse()
    .find((day): day is Extract<ParquetReaderResult<T>, { state: "not_generated" }> => day.state === "not_generated");
  if (missing !== undefined) return missing;
  const absent = [...days]
    .reverse()
    .find((day): day is Extract<ParquetReaderResult<T>, { state: "absent" }> => day.state === "absent");
  if (absent !== undefined) return absent;
  throw contractError(`Parquet plane described no state in the window ${firstDay}..${lastDay}`);
}

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
    const newest = published.at(-1)!;
    return {
      state: "ready",
      requestedDay: lastDay,
      servedDay: newest.servedDay,
      data: { firstDay, lastDay, observations: [...newestByCell.values()], days },
      truncated: published.some((day) => day.truncated),
    };
  });
}

export async function getParquetFireDetections(
  input: ParquetViewportRead & { dayRange?: number }
): Promise<ParquetReaderResult<ParquetFireWindow>> {
  const lastDay = selectedDay(input.date, input.nowMs);
  const requestedRange = input.date === undefined ? input.dayRange ?? 1 : 1;
  if (!Number.isInteger(requestedRange) || requestedRange < 1 || requestedRange > 10) {
    throw new ParquetPlaneRequestError(`dayRange must be an integer from 1 through 10, got ${requestedRange}`);
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
        }))
      )
    );
    const published = days.filter(
      (day): day is Extract<(typeof days)[number], { state: "ready" }> => day.state === "ready"
    );
    if (published.length === 0) return windowTerminal(days, firstDay, lastDay);
    const newest = published.at(-1)!;
    return {
      state: "ready",
      requestedDay: lastDay,
      servedDay: newest.servedDay,
      data: { firstDay, lastDay, cells: published.flatMap((day) => day.data), days },
      truncated: published.some((day) => day.truncated),
    };
  });
}

/** The frozen day route has no pre-budget signal filter, so generic metric reads fail closed. */
export function getParquetMetricAtDate(): Promise<ParquetReaderResult<never>> {
  return Promise.resolve({
    state: "upstream_unavailable",
    fault: {
      kind: "contract",
      message: "The frozen Parquet route cannot filter signal_name before applying its row budget",
    },
  });
}
