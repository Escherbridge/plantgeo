import { z } from "zod";

import {
  fetchBoundedJson,
  providerUrl,
  UpstreamConfigurationError,
} from "@/lib/server/http/bounded-upstream";

export class WeatherForecastContractError extends Error {}

export const WEATHER_FORECAST_VARIABLES = [
  "temperature_2m",
  "apparent_temperature",
  "relative_humidity_2m",
  "dew_point_2m",
  "cloud_cover",
  "pressure_msl",
  "precipitation_probability",
  "precipitation",
  "weather_code",
  "wind_speed_10m",
  "wind_gusts_10m",
  "wind_direction_10m",
  "wind_u_10m",
  "wind_v_10m",
] as const;

const MAX_RESPONSE_BYTES = 4 * 1024 * 1024;
const UPSTREAM_TIMEOUT_MS = 15_000;
const MAX_WINDOW_MS = 10 * 24 * 60 * 60 * 1_000;
const MAX_SELECTED_VALUES = 4_000;
const MAX_FIELD_VALUES = 10_000;

const tokenSchema = z.string().min(1).max(160).regex(/^[A-Za-z0-9][A-Za-z0-9_.-]*$/);
const textSchema = z.string().min(1).max(2_048).regex(/\S/);
const finiteSchema = z.number().finite();
const utcInstantSchema = z
  .string()
  .datetime({ offset: true })
  .refine((value) => /(?:Z|[+-]00:00)$/.test(value), "timestamp must be explicit UTC");
const utcHourSchema = utcInstantSchema.refine(
  (value) => Date.parse(value) % (60 * 60 * 1_000) === 0,
  "timestamp must be on a UTC hour boundary"
);
const timezoneSchema = z
  .string()
  .min(1)
  .max(100)
  .refine((value) => {
    try {
      new Intl.DateTimeFormat("en-US", { timeZone: value });
      return true;
    } catch {
      return false;
    }
  }, "timezone must be a known IANA timezone");

export const weatherForecastVariableSchema = z.enum(WEATHER_FORECAST_VARIABLES);
export const weatherForecastStatusSchema = z.enum([
  "available",
  "exact_absence",
  "outside_domain",
  "stale_run",
  "not_yet_generated",
  "upstream_unavailable",
  "missing",
]);

const zoomTierSchema = z.union([z.literal(0), z.literal(5), z.literal(9), z.literal(13)]);
const bboxSchema = z
  .tuple([finiteSchema.min(-180).max(180), finiteSchema.min(-90).max(90), finiteSchema.min(-180).max(180), finiteSchema.min(-90).max(90)])
  .refine(([west, south, east, north]) => west < east && south < north, {
    message: "bbox must be an ordered, non-antimeridian window",
  });

const requestWindowShape = {
  start: utcHourSchema,
  end: utcHourSchema,
};

function validWindow(start: string, end: string): boolean {
  const duration = Date.parse(end) - Date.parse(start);
  return duration > 0 && duration <= MAX_WINDOW_MS;
}

export const weatherForecastCapabilityRequestSchema = z
  .object({ productId: tokenSchema })
  .strict();

export const selectedWeatherForecastRequestSchema = z
  .object({
    productId: tokenSchema,
    runId: tokenSchema,
    longitude: finiteSchema.min(-180).max(180),
    latitude: finiteSchema.min(-90).max(90),
    ...requestWindowShape,
    timezone: timezoneSchema,
    zoom: z.number().int().min(0).max(22),
    maxDistanceM: finiteSchema.min(0).max(100_000),
  })
  .strict()
  .refine(({ start, end }) => validWindow(start, end), {
    message: "forecast window must be positive and no longer than ten days",
    path: ["end"],
  });

export const weatherForecastFieldRequestSchema = z
  .object({
    productId: tokenSchema,
    runId: tokenSchema,
    bbox: bboxSchema,
    ...requestWindowShape,
    variable: weatherForecastVariableSchema,
    zoom: z.number().int().min(0).max(22),
  })
  .strict()
  .refine(({ start, end }) => validWindow(start, end), {
    message: "forecast window must be positive and no longer than ten days",
    path: ["end"],
  });

export type WeatherForecastCapabilityRequest = z.infer<
  typeof weatherForecastCapabilityRequestSchema
>;
export type SelectedWeatherForecastRequest = z.infer<
  typeof selectedWeatherForecastRequestSchema
>;
export type WeatherForecastFieldRequest = z.infer<typeof weatherForecastFieldRequestSchema>;

const spatialSupportSchema = z
  .object({
    kind: z.literal("sampled_point"),
    represented_support: z.literal("sample_coordinate"),
    source_resolution_m: finiteSchema.positive().nullable(),
    resolution_evidence: textSchema.nullable(),
  })
  .strict()
  .refine(
    ({ source_resolution_m, resolution_evidence }) =>
      (source_resolution_m === null) === (resolution_evidence === null),
    "source resolution and evidence must be supplied together"
  );

const forecastRunSchema = z
  .object({
    schema_version: z.literal("weather-forecast/v1"),
    product_kind: z.literal("forecast"),
    forecast_kind: z.literal("deterministic"),
    run_id: tokenSchema,
    product_id: tokenSchema,
    provider: tokenSchema,
    model: tokenSchema,
    model_version: textSchema.nullable(),
    model_init_at: utcInstantSchema,
    provider_issued_at: utcInstantSchema.nullable(),
    fetched_at: utcInstantSchema,
    admitted_at: utcInstantSchema,
    published_at: utcInstantSchema,
    licence: textSchema,
    source_url: z.string().url().max(2_048).refine((value) => new URL(value).protocol === "https:"),
    source_payload_sha256: z.string().regex(/^[0-9a-f]{64}$/),
    support: spatialSupportSchema,
    variables: z
      .array(weatherForecastVariableSchema)
      .min(1)
      .max(WEATHER_FORECAST_VARIABLES.length)
      .refine((variables) => new Set(variables).size === variables.length),
  })
  .strict()
  .superRefine((run, context) => {
    const modelInit = Date.parse(run.model_init_at);
    const fetched = Date.parse(run.fetched_at);
    const admitted = Date.parse(run.admitted_at);
    const published = Date.parse(run.published_at);
    if (!(modelInit <= fetched && fetched <= admitted && admitted <= published)) {
      context.addIssue({ code: z.ZodIssueCode.custom, message: "run lifecycle is out of order" });
    }
    if (run.provider_issued_at !== null) {
      const issued = Date.parse(run.provider_issued_at);
      if (!(modelInit <= issued && issued <= fetched)) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          message: "provider issue time is outside the run lifecycle",
        });
      }
    }
  });

const unitSchema = z.enum(["degC", "%", "hPa", "mm", "code", "m/s", "degree"]);

const VARIABLE_UNITS: Record<WeatherForecastVariable, z.infer<typeof unitSchema>> = {
  temperature_2m: "degC",
  apparent_temperature: "degC",
  relative_humidity_2m: "%",
  dew_point_2m: "degC",
  cloud_cover: "%",
  pressure_msl: "hPa",
  precipitation_probability: "%",
  precipitation: "mm",
  weather_code: "code",
  wind_speed_10m: "m/s",
  wind_gusts_10m: "m/s",
  wind_direction_10m: "degree",
  wind_u_10m: "m/s",
  wind_v_10m: "m/s",
};

const INTERVAL_VARIABLES = new Set<WeatherForecastVariable>([
  "precipitation_probability",
  "precipitation",
  "wind_gusts_10m",
]);

const forecastValueSchema = z
  .object({
    run_id: tokenSchema,
    sample_id: tokenSchema,
    longitude: finiteSchema.min(-180).max(180),
    latitude: finiteSchema.min(-90).max(90),
    variable: weatherForecastVariableSchema,
    unit: unitSchema,
    valid_at: utcInstantSchema,
    lead_seconds: z.number().int().nonnegative(),
    interval_start: utcInstantSchema.nullable(),
    interval_end: utcInstantSchema.nullable(),
    value: finiteSchema.nullable(),
    status: weatherForecastStatusSchema,
  })
  .strict()
  .superRefine((row, context) => {
    if (row.unit !== VARIABLE_UNITS[row.variable]) {
      context.addIssue({ code: z.ZodIssueCode.custom, message: "variable unit mismatch" });
    }
    if ((row.status === "available") !== (row.value !== null)) {
      context.addIssue({ code: z.ZodIssueCode.custom, message: "value status mismatch" });
    }
    const hasStart = row.interval_start !== null;
    const hasEnd = row.interval_end !== null;
    if (hasStart !== hasEnd || (INTERVAL_VARIABLES.has(row.variable) && !hasStart)) {
      context.addIssue({ code: z.ZodIssueCode.custom, message: "invalid value interval" });
    } else if (
      row.interval_start !== null &&
      row.interval_end !== null &&
      !(Date.parse(row.interval_start) < Date.parse(row.interval_end) && row.interval_end === row.valid_at)
    ) {
      context.addIssue({ code: z.ZodIssueCode.custom, message: "invalid value interval" });
    }
    if (
      row.value !== null &&
      ((["relative_humidity_2m", "cloud_cover", "precipitation_probability"] as const).includes(
        row.variable as "relative_humidity_2m"
      ) &&
        (row.value < 0 || row.value > 100))
    ) {
      context.addIssue({ code: z.ZodIssueCode.custom, message: "value outside variable support" });
    }
    if (
      row.value !== null &&
      ((row.variable === "temperature_2m" || row.variable === "dew_point_2m") && row.value < -273.15 ||
        (["pressure_msl", "precipitation", "weather_code", "wind_speed_10m", "wind_gusts_10m"] as const).includes(
          row.variable as "pressure_msl"
        ) && row.value < 0 ||
        row.variable === "wind_direction_10m" && (row.value < 0 || row.value > 360) ||
        row.variable === "weather_code" && !Number.isInteger(row.value))
    ) {
      context.addIssue({ code: z.ZodIssueCode.custom, message: "value outside variable support" });
    }
  });

const selectionSchema = z
  .object({
    run_id: tokenSchema,
    longitude: finiteSchema.min(-180).max(180),
    latitude: finiteSchema.min(-90).max(90),
    start: utcHourSchema,
    end: utcHourSchema,
    timezone: timezoneSchema,
  })
  .strict()
  .refine(({ start, end }) => validWindow(start, end), { path: ["end"] });

export const weatherForecastCapabilitySchema = z
  .object({
    schema_version: z.literal("weather-forecast-capability/v1"),
    product_id: tokenSchema,
    source: z.literal("local"),
    status: z.enum(["available", "stale_run", "not_yet_generated", "upstream_unavailable"]),
    active_run_id: tokenSchema.nullable(),
    run: forecastRunSchema.nullable(),
    support: z.literal("sampled_points").nullable(),
    variables: z.array(weatherForecastVariableSchema).min(1).max(WEATHER_FORECAST_VARIABLES.length).nullable(),
    valid_start: utcInstantSchema.nullable(),
    valid_end: utcInstantSchema.nullable(),
    sample_count: z.number().int().min(1).max(256).nullable(),
    row_count: z.number().int().min(1).max(50_000).nullable(),
  })
  .strict()
  .superRefine((capability, context) => {
    const metadata = [
      capability.active_run_id,
      capability.run,
      capability.support,
      capability.variables,
      capability.valid_start,
      capability.valid_end,
      capability.sample_count,
      capability.row_count,
    ];
    const available = capability.status === "available" || capability.status === "stale_run";
    if (available ? metadata.some((value) => value === null) : metadata.some((value) => value !== null)) {
      context.addIssue({ code: z.ZodIssueCode.custom, message: "capability metadata contradicts status" });
      return;
    }
    if (capability.run !== null) {
      if (
        capability.run.product_id !== capability.product_id ||
        capability.run.run_id !== capability.active_run_id ||
        capability.support !== "sampled_points" ||
        !sameStrings(capability.variables ?? [], capability.run.variables)
      ) {
        context.addIssue({ code: z.ZodIssueCode.custom, message: "capability run identity mismatch" });
      }
    }
    if (
      capability.valid_start !== null &&
      capability.valid_end !== null &&
      Date.parse(capability.valid_start) >= Date.parse(capability.valid_end)
    ) {
      context.addIssue({ code: z.ZodIssueCode.custom, message: "capability window is invalid" });
    }
  });

export const selectedWeatherForecastSchema = z
  .object({
    selection: selectionSchema,
    status: weatherForecastStatusSchema,
    run: forecastRunSchema.nullable(),
    zoom: zoomTierSchema,
    sample_distance_m: finiteSchema.nonnegative().nullable(),
    values: z.array(forecastValueSchema).max(MAX_SELECTED_VALUES),
  })
  .strict();

export const weatherForecastFieldSchema = z
  .object({
    product_id: tokenSchema,
    run_id: tokenSchema,
    bbox: bboxSchema,
    start: utcHourSchema,
    end: utcHourSchema,
    variable: weatherForecastVariableSchema,
    zoom: zoomTierSchema,
    status: weatherForecastStatusSchema,
    run: forecastRunSchema.nullable(),
    values: z.array(forecastValueSchema).max(MAX_FIELD_VALUES),
  })
  .strict()
  .refine(({ start, end }) => validWindow(start, end), { path: ["end"] });

export type WeatherForecastVariable = z.infer<typeof weatherForecastVariableSchema>;
export type WeatherForecastCapability = z.infer<typeof weatherForecastCapabilitySchema>;
export type SelectedWeatherForecast = z.infer<typeof selectedWeatherForecastSchema>;
export type WeatherForecastField = z.infer<typeof weatherForecastFieldSchema>;

function sameStrings(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function servingZoomTier(zoom: number): 0 | 5 | 9 | 13 {
  if (zoom >= 13) return 13;
  if (zoom >= 9) return 9;
  if (zoom >= 5) return 5;
  return 0;
}

function endpoint(route: "capability" | "selected" | "field"): URL {
  const url = providerUrl("AGRI_DATA_SERVICE_URL", "http://localhost:8000");
  url.pathname = `${url.pathname.replace(/\/$/, "")}/api/v1/weather-forecast/${route}`;
  return url;
}

async function readPayload(
  route: "capability" | "selected" | "field",
  search: URLSearchParams,
  signal?: AbortSignal
): Promise<unknown> {
  const url = endpoint(route);
  url.search = search.toString();
  return fetchBoundedJson(
    url,
    { method: "GET", headers: { Accept: "application/json" } },
    {
      maxBytes: MAX_RESPONSE_BYTES,
      timeoutMs: UPSTREAM_TIMEOUT_MS,
      ...(signal === undefined ? {} : { signal }),
    }
  );
}

function parseContract<T>(schema: z.ZodType<T>, payload: unknown): T {
  const parsed = schema.safeParse(payload);
  if (!parsed.success) {
    throw new WeatherForecastContractError("weather forecast payload violated its serving contract");
  }
  return parsed.data;
}

function unavailableCapability(productId: string): WeatherForecastCapability {
  return {
    schema_version: "weather-forecast-capability/v1",
    product_id: productId,
    source: "local",
    status: "upstream_unavailable",
    active_run_id: null,
    run: null,
    support: null,
    variables: null,
    valid_start: null,
    valid_end: null,
    sample_count: null,
    row_count: null,
  };
}

export async function getWeatherForecastCapability(
  input: WeatherForecastCapabilityRequest,
  signal?: AbortSignal
): Promise<WeatherForecastCapability> {
  const request = weatherForecastCapabilityRequestSchema.parse(input);
  let payload: unknown;
  try {
    payload = await readPayload(
      "capability",
      new URLSearchParams({ product_id: request.productId }),
      signal
    );
  } catch (error) {
    if (error instanceof UpstreamConfigurationError) return unavailableCapability(request.productId);
    throw error;
  }
  const result = parseContract(weatherForecastCapabilitySchema, payload);
  if (result.product_id !== request.productId) {
    throw new WeatherForecastContractError("weather forecast capability substituted product identity");
  }
  return result;
}

export async function getSelectedWeatherForecast(
  input: SelectedWeatherForecastRequest,
  signal?: AbortSignal
): Promise<SelectedWeatherForecast> {
  const request = selectedWeatherForecastRequestSchema.parse(input);
  const search = new URLSearchParams({
    product_id: request.productId,
    run_id: request.runId,
    longitude: String(request.longitude),
    latitude: String(request.latitude),
    start: request.start,
    end: request.end,
    timezone: request.timezone,
    zoom: String(request.zoom),
    max_distance_m: String(request.maxDistanceM),
  });
  const payload = await readPayload("selected", search, signal);
  const result = parseContract(selectedWeatherForecastSchema, payload);
  const expectedZoom = servingZoomTier(request.zoom);
  const selection = result.selection;
  if (
    selection.run_id !== request.runId ||
    selection.longitude !== request.longitude ||
    selection.latitude !== request.latitude ||
    Date.parse(selection.start) !== Date.parse(request.start) ||
    Date.parse(selection.end) !== Date.parse(request.end) ||
    selection.timezone !== request.timezone ||
    result.zoom !== expectedZoom ||
    (result.run !== null &&
      (result.run.product_id !== request.productId || result.run.run_id !== request.runId)) ||
    result.values.some((row) =>
      row.run_id !== request.runId ||
      Date.parse(row.valid_at) < Date.parse(request.start) ||
      Date.parse(row.valid_at) >= Date.parse(request.end)
    )
  ) {
    throw new WeatherForecastContractError("selected weather forecast substituted request identity");
  }
  return result;
}

export async function getWeatherForecastField(
  input: WeatherForecastFieldRequest,
  signal?: AbortSignal
): Promise<WeatherForecastField> {
  const request = weatherForecastFieldRequestSchema.parse(input);
  const search = new URLSearchParams({
    product_id: request.productId,
    run_id: request.runId,
    bbox: request.bbox.join(","),
    start: request.start,
    end: request.end,
    variable: request.variable,
    zoom: String(request.zoom),
  });
  const payload = await readPayload("field", search, signal);
  const result = parseContract(weatherForecastFieldSchema, payload);
  const expectedZoom = servingZoomTier(request.zoom);
  const [west, south, east, north] = request.bbox;
  if (
    result.product_id !== request.productId ||
    result.run_id !== request.runId ||
    !result.bbox.every((value, index) => value === request.bbox[index]) ||
    Date.parse(result.start) !== Date.parse(request.start) ||
    Date.parse(result.end) !== Date.parse(request.end) ||
    result.variable !== request.variable ||
    result.zoom !== expectedZoom ||
    (result.run !== null &&
      (result.run.product_id !== request.productId || result.run.run_id !== request.runId)) ||
    result.values.some(
      (row) =>
        row.run_id !== request.runId ||
        row.variable !== request.variable ||
        row.longitude < west ||
        row.longitude > east ||
        row.latitude < south ||
        row.latitude > north ||
        Date.parse(row.valid_at) < Date.parse(request.start) ||
        Date.parse(row.valid_at) >= Date.parse(request.end)
    )
  ) {
    throw new WeatherForecastContractError("weather forecast field substituted request identity");
  }
  return result;
}
