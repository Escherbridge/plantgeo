import { z } from "zod";
import {
  climateFieldSignalDefinition,
  climateFieldSignalName,
  type AirTemperatureVariant,
  type ClimateFieldSignalId,
} from "@/lib/environmental/climate-field";
import {
  SOIL_FIELD_ATTRIBUTION,
  SOIL_FIELD_SOURCE_KEY,
  SOIL_FIELD_SUPPORT_KEY,
  soilFieldBandFor,
  soilFieldDepthDefinition,
  soilFieldMeasureDefinition,
  type SoilFieldDepth,
  type SoilFieldMeasure,
} from "@/lib/environmental/soil-field";
import { resolveZoomTier } from "@/lib/map/zoom-tiers";
import { isFreshObservation } from "@/lib/server/services/environmental-time";
import {
  SOIL_FIELD_MAX_CELLS,
  type PublishedSoilFieldCollection,
  type SoilFieldFeatureProperties,
  type SoilFieldReadOptions,
} from "@/lib/server/services/environmental-read-model";
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

const SOIL_FIELD_BASE_CELL_DEGREES = 0.25;
const SOIL_FIELD_PARQUET_MAX_OBSERVATION_AGE_DAYS = 0;
const SOIL_FIELD_TIER_RESOLUTION = { 0: 5, 5: 0.2, 9: 0.01, 13: null } as const;
const SNAPSHOT_SOURCE_MANIFEST_SHA256 =
  "465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f";

const SOIL_FIELD_LANES = {
  moisture: {
    surface: "soil-field-moisture-0-7cm",
    "root-zone": "soil-field-moisture-7-28cm",
    deep: "soil-field-moisture-28-100cm",
  },
  temperature: {
    surface: "soil-temperature-0-to-7cm",
    "root-zone": "soil-temperature-7-to-28cm",
    deep: "soil-temperature-28-to-100cm",
    substratum: "soil-temperature-100-to-255cm",
  },
  vpd: { surface: "soil-field-vpd" },
} as const;

const SOIL_TEMPERATURE_SOURCE_PARAMETERS = {
  surface: "soil_temperature_0_to_7cm_mean",
  "root-zone": "soil_temperature_7_to_28cm_mean",
  deep: "soil_temperature_28_to_100cm_mean",
  substratum: "soil_temperature_100_to_255cm_mean",
} as const satisfies Readonly<Record<SoilFieldDepth, string>>;

const SOIL_MOISTURE_SOURCE_PARAMETERS = {
  surface: "soil_moisture_0_to_7cm_mean",
  "root-zone": "soil_moisture_7_to_28cm_mean",
  deep: "soil_moisture_28_to_100cm_mean",
} as const satisfies Readonly<Record<Exclude<SoilFieldDepth, "substratum">, string>>;

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

const climateSnapshotLineageRowSchema = z
  .object({
    support_key: z.string().min(1),
    signal_name: z.string().min(1),
    normalized_unit: z.string().min(1),
    cell_id: z.string().nullable(),
    observed_day: daySchema,
    normalized_value: finiteNumberSchema,
    observation_count: z.number().int().positive(),
    newest_observed_at: instantSchema,
    coverage_fraction: finiteNumberSchema.nullable(),
    allowed_client_exposure: z.boolean().nullable(),
    cell_longitude: finiteNumberSchema,
    cell_latitude: finiteNumberSchema,
    source_key: z.string().min(1),
    source_parameter: z.string().min(1),
    source_snapshot_id: z.string().min(1),
    source_manifest_sha256: z.string().regex(/^[a-f0-9]{64}$/),
    precedence_contract: z.string().min(1),
    selected_source_row_id: z.number().int().nullable(),
    selected_source_row_sha256: z.string().regex(/^[a-f0-9]{64}$/).nullable(),
    selected_source_release_id: z.string().nullable(),
    selected_source_release_retrieved_at: instantSchema.nullable(),
    selected_source_release_payload_checksum: z.string().nullable(),
    selected_source_part_key: z.string().nullable(),
    selected_source_part_sha256: z.string().regex(/^[a-f0-9]{64}$/).nullable(),
    selected_source_row_ordinal: z.number().int().nonnegative().nullable(),
    input_source_row_count: z.number().int().positive(),
    input_source_row_digest: z.string().nullable(),
    input_source_row_ids: z.array(z.number().int()).nullable(),
    input_source_row_sha256s: z.array(z.string().regex(/^[a-f0-9]{64}$/)).nullable(),
    input_source_release_ids: z.array(z.string()).nullable(),
    input_source_part_keys: z.array(z.string()).nullable(),
    input_source_part_sha256s: z.array(z.string().regex(/^[a-f0-9]{64}$/)).nullable(),
    input_source_row_ordinals: z.array(z.number().int().nonnegative()).nullable(),
  })
  .strict();

const signalPlaneRowSchema = z
  .object({
    support_key: z.string().min(1),
    signal_name: z.string().min(1),
    normalized_unit: z.string().min(1),
    cell_id: z.string().nullable(),
    observed_day: daySchema,
    normalized_value: finiteNumberSchema,
    observation_count: z.number().int().positive(),
    newest_observed_at: instantSchema,
    coverage_fraction: finiteNumberSchema.nullable(),
    allowed_client_exposure: z.boolean().nullable(),
    cell_longitude: finiteNumberSchema,
    cell_latitude: finiteNumberSchema,
  })
  .strict();

const selectedSnapshotRowShape = {
  selected_observation_id: z.number().int().nullable(),
  selected_canonical_row_sha256: z.string().regex(/^[a-f0-9]{64}$/).nullable(),
  selected_source_release_id: z.string().nullable(),
  selected_release_retrieved_at: instantSchema.nullable(),
  physical_candidate_count: z.number().int().positive(),
  lineage_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  input_manifest_sha256: z.string().regex(/^[a-f0-9]{64}$/),
};

const soilWetnessRowSchema = signalPlaneRowSchema.extend(selectedSnapshotRowShape).strict();

const soilTemperatureRowSchema = signalPlaneRowSchema
  .extend({
    data_source_key: z.string().min(1),
    source_parameter: z.string().min(1),
    ...selectedSnapshotRowShape,
  })
  .strict();

type SoilServingRow = Pick<
  z.infer<typeof signalPlaneRowSchema>,
  | "support_key"
  | "signal_name"
  | "normalized_unit"
  | "cell_id"
  | "observed_day"
  | "normalized_value"
  | "coverage_fraction"
  | "allowed_client_exposure"
  | "cell_longitude"
  | "cell_latitude"
>;

export const PARQUET_CLIMATE_FIELD_SIGNAL_IDS = [
  "air-temperature",
  "dew-point",
  "precipitation",
  "relative-humidity",
  "shortwave-radiation",
  "wind-speed",
  "soil-wetness-surface",
  "soil-wetness-root-zone",
  "soil-wetness-profile",
] as const satisfies readonly ClimateFieldSignalId[];

export type ParquetClimateFieldSignalId =
  (typeof PARQUET_CLIMATE_FIELD_SIGNAL_IDS)[number];

const PARQUET_CLIMATE_FIELD_SIGNAL_SET = new Set<ClimateFieldSignalId>(
  PARQUET_CLIMATE_FIELD_SIGNAL_IDS
);

const STANDARD_CLIMATE_FIELD_LANES = {
  "air-temperature": {
    layer: null,
    rowContract: "signal-plane",
    sourceParameter: null,
  },
  "dew-point": {
    layer: "climate-field-dew-point",
    rowContract: "signal-plane",
    sourceParameter: null,
  },
  precipitation: {
    layer: "climate-field-precipitation",
    rowContract: "snapshot-lineage",
    sourceParameter: "PRECTOTCORR",
  },
  "relative-humidity": {
    layer: "climate-field-relative-humidity",
    rowContract: "snapshot-lineage",
    sourceParameter: "RH2M",
  },
  "shortwave-radiation": {
    layer: "climate-field-shortwave-radiation",
    rowContract: "snapshot-lineage",
    sourceParameter: "ALLSKY_SFC_SW_DWN",
  },
  "wind-speed": {
    layer: "climate-field-wind-speed",
    rowContract: "signal-plane",
    sourceParameter: null,
  },
  "soil-wetness-surface": {
    layer: "soil-wetness-surface",
    rowContract: "soil-wetness",
    sourceParameter: null,
  },
  "soil-wetness-root-zone": {
    layer: "soil-wetness-root-zone",
    rowContract: "soil-wetness",
    sourceParameter: null,
  },
  "soil-wetness-profile": {
    layer: "soil-wetness-profile",
    rowContract: "soil-wetness",
    sourceParameter: null,
  },
} as const satisfies Record<
  ClimateFieldSignalId,
  {
    layer: string | null;
    rowContract: "signal-plane" | "snapshot-lineage" | "soil-wetness";
    sourceParameter: string | null;
  }
>;

export interface ParquetClimateFieldObservation {
  cellId: string;
  observedDay: string;
  value: number;
  observationCount: number;
  newestObservedAt: string;
  coverageFraction: number | null;
  allowedClientExposure: boolean | null;
  longitude: number;
  latitude: number;
}

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

type ParquetUpstreamFailure = Extract<
  ParquetReaderResult<never>,
  { state: "upstream_unavailable" }
>;

export function parquetUpstreamFailure(error: unknown): ParquetUpstreamFailure | null {
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
    const failure = parquetUpstreamFailure(error);
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

export function isParquetClimateFieldSignal(
  signal: ClimateFieldSignalId
): signal is ParquetClimateFieldSignalId {
  return PARQUET_CLIMATE_FIELD_SIGNAL_SET.has(signal);
}

function climateFieldProduct(
  signal: ParquetClimateFieldSignalId,
  variant: AirTemperatureVariant
) {
  const contract = STANDARD_CLIMATE_FIELD_LANES[signal];
  const layer =
    signal === "air-temperature"
      ? `climate-field-air-temperature-${variant}`
      : contract.layer;
  if (layer === null) {
    throw contractError(`${signal} has no registered Parquet product layer`);
  }
  return {
    ...contract,
    layer,
  };
}

function decodeClimateFieldRows(
  rows: readonly Record<string, unknown>[],
  signal: ParquetClimateFieldSignalId,
  variant: AirTemperatureVariant,
  servedDay: string
): ParquetClimateFieldObservation[] {
  const contract = climateFieldProduct(signal, variant);
  const { layer } = contract;
  const expectedSignalName = climateFieldSignalName(signal, variant);
  const expectedUnit = climateFieldSignalDefinition(signal).unit;
  const seenCells = new Set<string>();
  const parsed =
    contract.rowContract === "snapshot-lineage"
      ? parseRows(rows, climateSnapshotLineageRowSchema, layer)
      : contract.rowContract === "soil-wetness"
        ? parseRows(rows, soilWetnessRowSchema, layer)
        : parseRows(rows, signalPlaneRowSchema, layer);
  return parsed.map((row) => {
    if (
      row.support_key !== "surface" ||
      row.signal_name !== expectedSignalName ||
      row.normalized_unit !== expectedUnit ||
      row.observed_day !== servedDay ||
      row.cell_id === null
    ) {
      throw contractError(`${layer} returned a row outside its registered z13 climate contract`);
    }
    if (
      contract.rowContract === "snapshot-lineage" &&
      (contract.sourceParameter === null ||
        !("source_key" in row) ||
        row.source_key !== "nasa-power-daily" ||
        !("source_parameter" in row) ||
        row.source_parameter !== contract.sourceParameter ||
        !("source_manifest_sha256" in row) ||
        row.source_manifest_sha256 !== SNAPSHOT_SOURCE_MANIFEST_SHA256)
    ) {
      throw contractError(`${layer} returned a row outside its pinned source contract`);
    }
    if (
      contract.rowContract === "soil-wetness" &&
      (!("input_manifest_sha256" in row) ||
        row.input_manifest_sha256 !== SNAPSHOT_SOURCE_MANIFEST_SHA256)
    ) {
      throw contractError(`${layer} returned a row outside its pinned source contract`);
    }
    if (
      row.cell_longitude < -180 ||
      row.cell_longitude > 180 ||
      row.cell_latitude < -90 ||
      row.cell_latitude > 90
    ) {
      throw contractError(`${layer} returned a cell outside WGS84 bounds`);
    }
    if (seenCells.has(row.cell_id)) {
      throw contractError(`${layer} returned duplicate z13 cell ${row.cell_id} for ${servedDay}`);
    }
    seenCells.add(row.cell_id);
    return {
      cellId: row.cell_id,
      observedDay: row.observed_day,
      value: row.normalized_value,
      observationCount: row.observation_count,
      newestObservedAt: row.newest_observed_at,
      coverageFraction: row.coverage_fraction,
      allowedClientExposure: row.allowed_client_exposure,
      longitude: row.cell_longitude,
      latitude: row.cell_latitude,
    };
  });
}

/** Read one exact published climate day from a promoted, frozen-layout Parquet lane. */
export async function getParquetClimateField(input: {
  bbox: string;
  date?: string;
  signal: ClimateFieldSignalId;
  variant: AirTemperatureVariant;
  nowMs?: number;
}): Promise<ParquetReaderResult<readonly ParquetClimateFieldObservation[]>> {
  const day = selectedDay(input.date, input.nowMs);
  if (!isParquetClimateFieldSignal(input.signal)) {
    return {
      state: "upstream_unavailable",
      fault: {
        kind: "contract",
        message: `No frozen-layout Parquet reader is registered for ${input.signal}`,
      },
    };
  }
  const signal = input.signal;
  const layer = climateFieldProduct(signal, input.variant).layer;
  return boundedResult(async () =>
    mapEnvelope(
      await getParquetLayerDay({
        layer,
        day,
        zoomTier: 13,
        bbox: input.bbox,
      }),
      (rows) => decodeClimateFieldRows(rows, signal, input.variant, day)
    )
  );
}

function soilFieldLane(measure: SoilFieldMeasure, depth: SoilFieldDepth): string {
  if (measure === "moisture") {
    if (depth === "root-zone") return SOIL_FIELD_LANES.moisture["root-zone"];
    if (depth === "deep") return SOIL_FIELD_LANES.moisture.deep;
    return SOIL_FIELD_LANES.moisture.surface;
  }
  if (measure === "temperature") {
    return SOIL_FIELD_LANES.temperature[depth];
  }
  return SOIL_FIELD_LANES.vpd.surface;
}

function emptyParquetSoilField(
  reason: NonNullable<PublishedSoilFieldCollection["reason"]>,
  measure: SoilFieldMeasure,
  depth: SoilFieldDepth,
  requestedDay: string,
  zoomTier: 0 | 5 | 9 | 13,
  newestAvailableDay: string | null
): PublishedSoilFieldCollection {
  const definition = soilFieldMeasureDefinition(measure);
  return {
    type: "FeatureCollection",
    features: [],
    availability: "unavailable",
    reason,
    granularity: zoomTier === 13 ? "detail" : zoomTier === 9 ? "regional-average" : "coarse-average",
    measure,
    depth,
    unit: definition.unit,
    attribution: SOIL_FIELD_ATTRIBUTION,
    observedDay: null,
    requestedDay,
    newestAvailableDay,
    cellCount: 0,
    truncated: false,
    maxCellCount: SOIL_FIELD_MAX_CELLS,
    maxObservationAgeDays: SOIL_FIELD_PARQUET_MAX_OBSERVATION_AGE_DAYS,
    latticeDegrees: SOIL_FIELD_TIER_RESOLUTION[zoomTier],
    smoothingSigmaDegrees: null,
    bands: definition.bands,
    sourceClientExposureApproved: false,
  };
}

function decodeSoilFieldRows(
  rows: readonly Record<string, unknown>[],
  measure: SoilFieldMeasure,
  depth: SoilFieldDepth,
  signalName: string,
  unit: string,
  layer: string,
  servedDay: string,
  zoomTier: 0 | 5 | 9 | 13
): SoilServingRow[] {
  const parsed =
    measure === "moisture"
      ? parseRows(rows, climateSnapshotLineageRowSchema, layer)
      : measure === "temperature"
        ? parseRows(rows, soilTemperatureRowSchema, layer)
        : parseRows(rows, signalPlaneRowSchema, layer);
  const seenCells = new Set<string>();
  return parsed.map((row) => {
    if (
      row.support_key !== SOIL_FIELD_SUPPORT_KEY ||
      row.signal_name !== signalName ||
      row.normalized_unit !== unit ||
      row.observed_day !== servedDay ||
      (measure === "moisture" &&
        (!("source_key" in row) ||
          row.source_key !== SOIL_FIELD_SOURCE_KEY ||
          depth === "substratum" ||
          !("source_parameter" in row) ||
          row.source_parameter !== SOIL_MOISTURE_SOURCE_PARAMETERS[depth] ||
          !("source_manifest_sha256" in row) ||
          row.source_manifest_sha256 !== SNAPSHOT_SOURCE_MANIFEST_SHA256)) ||
      (measure === "temperature" &&
        (!("data_source_key" in row) ||
          row.data_source_key !== SOIL_FIELD_SOURCE_KEY ||
          !("source_parameter" in row) ||
          row.source_parameter !== SOIL_TEMPERATURE_SOURCE_PARAMETERS[depth] ||
          !("input_manifest_sha256" in row) ||
          row.input_manifest_sha256 !== SNAPSHOT_SOURCE_MANIFEST_SHA256))
    ) {
      throw contractError(`${layer} returned a row outside its registered soil-field contract`);
    }
    if ((zoomTier === 13) !== (row.cell_id !== null)) {
      throw contractError(`${layer} returned invalid cell identity nullability at z${zoomTier}`);
    }
    if (
      row.cell_longitude < -180 ||
      row.cell_longitude > 180 ||
      row.cell_latitude < -90 ||
      row.cell_latitude > 90
    ) {
      throw contractError(`${layer} returned a cell outside WGS84 bounds`);
    }
    const cellKey = row.cell_id ?? `${row.cell_longitude}:${row.cell_latitude}`;
    if (seenCells.has(cellKey)) {
      throw contractError(`${layer} returned duplicate cell ${cellKey} for ${servedDay}`);
    }
    seenCells.add(cellKey);
    return row;
  });
}

function soilFieldPolygon(
  row: SoilServingRow,
  zoomTier: 0 | 5 | 9 | 13
): GeoJSON.Polygon {
  const resolution = SOIL_FIELD_TIER_RESOLUTION[zoomTier];
  const west =
    resolution === null
      ? row.cell_longitude - SOIL_FIELD_BASE_CELL_DEGREES / 2
      : row.cell_longitude;
  const south =
    resolution === null ? row.cell_latitude - SOIL_FIELD_BASE_CELL_DEGREES / 2 : row.cell_latitude;
  const size = resolution ?? SOIL_FIELD_BASE_CELL_DEGREES;
  const east = west + size;
  const north = south + size;
  return {
    type: "Polygon",
    coordinates: [
      [
        [west, south],
        [east, south],
        [east, north],
        [west, north],
        [west, south],
      ],
    ],
  };
}

/** Read one soil-field viewport exclusively from its registered Parquet product lane. */
export async function getParquetSoilField(
  bbox: string,
  options: SoilFieldReadOptions = {}
): Promise<PublishedSoilFieldCollection> {
  const measure = options.measure ?? "moisture";
  const definition = soilFieldMeasureDefinition(measure);
  const requestedDepth = options.depth ?? definition.defaultDepth;
  const { depth, signalName } = soilFieldDepthDefinition(measure, requestedDepth);
  const requestedDay = selectedDay(options.date, undefined);
  const zoomTier = resolveZoomTier(options.zoom ?? 13);
  if (requestedDay > currentUtcDay()) {
    return emptyParquetSoilField(
      "not_forecastable",
      measure,
      depth,
      requestedDay,
      zoomTier,
      null
    );
  }
  const layer = soilFieldLane(measure, depth);
  const envelope = await getParquetLayerDay({
    layer,
    day: requestedDay,
    zoomTier,
    bbox,
  });
  if (envelope.state !== "published") {
    return emptyParquetSoilField(
      "not_published",
      measure,
      depth,
      requestedDay,
      zoomTier,
      null
    );
  }
  if (envelope.servedDay !== requestedDay) {
    throw contractError(`${layer} served ${envelope.servedDay} for exact day ${requestedDay}`);
  }

  const rows = decodeSoilFieldRows(
    envelope.rows,
    measure,
    depth,
    signalName,
    definition.unit,
    layer,
    envelope.servedDay,
    zoomTier
  );
  const drawable = rows.slice(0, SOIL_FIELD_MAX_CELLS);
  const features = drawable.map((row): GeoJSON.Feature<GeoJSON.Polygon> => {
    const band = soilFieldBandFor(measure, row.normalized_value);
    const properties: SoilFieldFeatureProperties = {
      value: row.normalized_value,
      bandIndex: band.bandIndex,
      bandLabel: band.label,
      aggregated: zoomTier !== 13,
      cellKey: row.cell_id,
      coverageFraction: row.coverage_fraction,
    };
    return {
      type: "Feature",
      id: row.cell_id ?? `${layer}:${envelope.servedDay}:${row.cell_longitude}:${row.cell_latitude}`,
      geometry: soilFieldPolygon(row, zoomTier),
      properties,
    };
  });
  if (features.length === 0) {
    return emptyParquetSoilField(
      "not_published",
      measure,
      depth,
      requestedDay,
      zoomTier,
      envelope.servedDay
    );
  }
  return {
    type: "FeatureCollection",
    features,
    availability: "published",
    reason: null,
    granularity: zoomTier === 13 ? "detail" : zoomTier === 9 ? "regional-average" : "coarse-average",
    measure,
    depth,
    unit: definition.unit,
    attribution: SOIL_FIELD_ATTRIBUTION,
    observedDay: envelope.servedDay,
    requestedDay,
    newestAvailableDay: null,
    cellCount: features.length,
    truncated: envelope.truncated || rows.length > SOIL_FIELD_MAX_CELLS,
    maxCellCount: SOIL_FIELD_MAX_CELLS,
    maxObservationAgeDays: SOIL_FIELD_PARQUET_MAX_OBSERVATION_AGE_DAYS,
    latticeDegrees: SOIL_FIELD_TIER_RESOLUTION[zoomTier],
    smoothingSigmaDegrees: null,
    bands: definition.bands,
    sourceClientExposureApproved: rows.every(
      (row) => row.allowed_client_exposure === true
    ),
  };
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
