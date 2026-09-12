import { z } from "zod";

const utc = z.string().datetime();
export const forecastQuerySchema = z.object({
  run_id: z.string().regex(/^[a-zA-Z0-9_-]{1,100}$/),
  lat: z.coerce.number().finite().min(-90).max(90),
  lon: z.coerce.number().finite().min(-180).max(180),
  start: utc,
  end: utc,
}).strict().refine(({ start, end }) => {
  const duration = Date.parse(end) - Date.parse(start);
  return duration > 0 && duration <= 48 * 3600_000
    && Date.parse(start) % 3600_000 === 0 && Date.parse(end) % 3600_000 === 0;
}, "Choose a positive UTC hourly window of at most 48 hours.");

export type ForecastQuery = z.infer<typeof forecastQuerySchema>;
const nullableNumber = z.number().finite().nullable();
const valuesSchema = z.object({
  temperature_2m: nullableNumber,
  relative_humidity_2m: z.number().min(0).max(100).nullable(),
  cloud_cover: z.number().min(0).max(100).nullable(),
  precipitation: z.number().min(0).nullable(),
  wind_u_10m: nullableNumber,
  wind_v_10m: nullableNumber,
  wind_speed_10m: z.number().min(0).nullable(),
  wind_direction_10m: z.number().min(0).max(360).nullable(),
});

export const forecastResponseSchema = z.object({
  schema_version: z.literal("weather-forecast/v1"),
  mode: z.literal("fixture"),
  status: z.enum(["ready", "partial", "outside-domain", "stale-run", "not-generated", "upstream-unavailable", "refused"]),
  reason: z.string().nullable(),
  run: z.object({
    run_id: z.string(), provider: z.string(), model: z.string(),
    initialization_time: utc, issue_time: utc.nullable(), fetched_at: utc,
    admitted_at: utc, published_at: utc, valid_start: utc, valid_end: utc,
    licence: z.string(), support: z.literal("sampled_point"), source_resolution: z.string(),
    lifecycle_basis: z.literal("synthetic_fixture_clock"), production_admitted: z.literal(false),
  }).nullable(),
  request: forecastQuerySchema,
  support: z.object({ kind: z.literal("sampled_point"), latitude: z.number(), longitude: z.number(), distance_km: z.literal(0) }).nullable(),
  variables: z.record(z.object({ unit: z.string(), statistic: z.string() })),
  hourly: z.array(z.object({
    valid_time: utc, interval_start: utc, interval_end: utc,
    lead_hours: z.number().nonnegative(), values: valuesSchema,
    missingness: z.record(z.string()),
  })).max(48),
  daily: z.array(z.object({
    day: z.string().regex(/^\d{4}-\d{2}-\d{2}$/), hours: z.number().int().min(0).max(24),
    expected_hours: z.literal(24), temperature_min: nullableNumber,
    temperature_max: nullableNumber, precipitation_sum: nullableNumber,
    relative_humidity_mean: nullableNumber, cloud_cover_mean: nullableNumber,
    wind_speed: nullableNumber, wind_direction: nullableNumber, complete: z.boolean(),
    missingness: z.record(z.string()),
  })).max(3),
  uncertainty: z.object({ kind: z.literal("deterministic"), message: z.string() }),
  limits: z.object({ max_hours: z.number(), max_points: z.number(), max_rows: z.number(), max_bytes: z.number() }),
});

export type ForecastResponse = z.infer<typeof forecastResponseSchema>;
export type ForecastHour = ForecastResponse["hourly"][number];

const variableDefinitions: Record<string, { unit: string; statistic: string }> = {
  temperature_2m: { unit: "degC", statistic: "instantaneous" },
  relative_humidity_2m: { unit: "%", statistic: "instantaneous" },
  cloud_cover: { unit: "%", statistic: "instantaneous" },
  precipitation: { unit: "mm", statistic: "sum_over_following_hour" },
  wind_u_10m: { unit: "m/s", statistic: "instantaneous_earth_relative" },
  wind_v_10m: { unit: "m/s", statistic: "instantaneous_earth_relative" },
  wind_speed_10m: { unit: "m/s", statistic: "derived_vector_magnitude" },
  wind_direction_10m: { unit: "degree", statistic: "meteorological_from_true_north" },
};

/** Reject a result that could repaint another selected place, run or hour. */
export function decodeForecastResponse(input: unknown, query: ForecastQuery): ForecastResponse {
  const result = forecastResponseSchema.parse(input);
  for (const key of ["run_id", "lat", "lon", "start", "end"] as const) {
    if (result.request[key] !== query[key]) throw new Error("Forecast request identity mismatch.");
  }
  const populated = result.status === "ready" || result.status === "partial";
  if (!populated && (result.hourly.length || result.daily.length)) throw new Error("Refused forecast contains values.");
  if (populated && (!result.run || result.run.run_id !== query.run_id || !result.support
    || result.support.latitude !== query.lat || result.support.longitude !== query.lon)) {
    throw new Error("Forecast run or selected-location support mismatch.");
  }
  if (populated) {
    for (const [name, definition] of Object.entries(variableDefinitions)) {
      const served = result.variables[name];
      if (served?.unit !== definition.unit || served.statistic !== definition.statistic) {
        throw new Error("Forecast variable units or accumulation definition mismatch.");
      }
    }
  }
  let previous = -Infinity;
  for (const hour of result.hourly) {
    const time = Date.parse(hour.valid_time);
    if (time <= previous || time < Date.parse(query.start) || time >= Date.parse(query.end)
      || time % 3600_000 !== 0 || Date.parse(hour.interval_start) !== time
      || Date.parse(hour.interval_end) - time !== 3600_000
      || (result.run && hour.lead_hours !== (time - Date.parse(result.run.initialization_time)) / 3600_000)) {
      throw new Error("Forecast valid-time identity mismatch.");
    }
    for (const [variable, value] of Object.entries(hour.values)) {
      if (value === null && !hour.missingness[variable]) throw new Error("Missing forecast value has no reason.");
      if (!result.variables[variable]) throw new Error("Forecast variable definition missing.");
    }
    previous = time;
  }
  if (result.status === "ready" && result.hourly.length !== (Date.parse(query.end) - Date.parse(query.start)) / 3600_000) {
    throw new Error("Ready forecast does not cover the requested hours.");
  }
  const dailyDates = new Set<string>();
  for (const day of result.daily) {
    const hours = result.hourly.filter((row) => row.valid_time.slice(0, 10) === day.day);
    if (dailyDates.has(day.day) || hours.length !== day.hours || !hours.length
      || (day.complete && (day.hours !== 24 || hours.some((row) => Object.values(row.values).some((number) => number === null))))) {
      throw new Error("Forecast daily completeness mismatch.");
    }
    dailyDates.add(day.day);
  }
  return result;
}

export const weatherForecastCapability = {
  id: "weather-forecast-fixture", mode: "fixture", servingReader: "parquet",
  support: "sampled_point", registration: "withheld", productionAvailable: false,
  reason: "Source admission and shared catalogue/renderer/agent ownership transfers remain open.",
  localPath: "/weather-forecast", maxWindowHours: 48,
} as const;
