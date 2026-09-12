export type ForecastVariable =
  | "temperature_2m" | "apparent_temperature" | "relative_humidity_2m"
  | "dew_point_2m" | "cloud_cover" | "pressure_msl" | "precipitation_probability"
  | "precipitation" | "weather_code" | "wind_speed_10m" | "wind_gusts_10m"
  | "wind_direction_10m" | "wind_u_10m" | "wind_v_10m";

export type ForecastMissingness = "available" | "missing" | "outside_domain"
  | "stale_run" | "not_yet_generated" | "exact_absence" | "upstream_unavailable";

export const FORECAST_VARIABLES: Record<ForecastVariable, { label: string; unit: string }> = {
  temperature_2m: { label: "Temperature", unit: "degC" },
  apparent_temperature: { label: "Feels like", unit: "degC" },
  relative_humidity_2m: { label: "Humidity", unit: "%" },
  dew_point_2m: { label: "Dew point", unit: "degC" },
  cloud_cover: { label: "Cloud cover", unit: "%" },
  pressure_msl: { label: "Sea-level pressure", unit: "hPa" },
  precipitation_probability: { label: "Precipitation probability", unit: "%" },
  precipitation: { label: "Precipitation amount", unit: "mm" },
  weather_code: { label: "Weather code", unit: "code" },
  wind_speed_10m: { label: "Wind speed", unit: "m/s" },
  wind_gusts_10m: { label: "Wind gust", unit: "m/s" },
  wind_direction_10m: { label: "Wind from", unit: "degree" },
  wind_u_10m: { label: "Eastward wind", unit: "m/s" },
  wind_v_10m: { label: "Northward wind", unit: "m/s" },
};

export interface WeatherForecastRun {
  run_id: string;
  product_id: string;
  provider: string;
  model: string;
  model_version: string | null;
  model_init_at: string;
  provider_issued_at: string | null;
  fetched_at: string;
  admitted_at: string;
  published_at: string;
  licence: string;
  source_url: string;
  source_payload_sha256: string;
  support: {
    kind: "sampled_point";
    represented_support: "sample_coordinate";
    source_resolution_m: number | null;
  };
  variables: ForecastVariable[];
}

export interface WeatherForecastValue {
  run_id: string;
  sample_id: string;
  longitude: number;
  latitude: number;
  variable: ForecastVariable;
  unit: string;
  valid_at: string;
  lead_seconds: number;
  interval_start: string | null;
  interval_end: string | null;
  value: number | null;
  status: ForecastMissingness;
}

export interface ForecastSelection {
  run_id: string;
  longitude: number;
  latitude: number;
  start: string;
  end: string;
  timezone: string;
}

export interface SelectedWeatherForecast {
  selection: ForecastSelection;
  status: ForecastMissingness;
  run: WeatherForecastRun | null;
  sample_distance_m: number | null;
  values: WeatherForecastValue[];
}

export const FORECAST_STATUS_LABELS: Record<ForecastMissingness, string> = {
  available: "Available forecast",
  missing: "No forecast value for this interval",
  outside_domain: "Outside the available forecast area",
  stale_run: "This forecast run is stale",
  not_yet_generated: "This forecast interval has not been generated",
  exact_absence: "No forecast exists for this exact interval",
  upstream_unavailable: "The forecast provider is unavailable",
};

const WEATHER_CONDITIONS: Record<number, string> = {
  0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
  45: "Fog", 48: "Depositing rime fog", 51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
  56: "Light freezing drizzle", 57: "Dense freezing drizzle", 61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
  66: "Light freezing rain", 67: "Heavy freezing rain", 71: "Slight snowfall", 73: "Moderate snowfall",
  75: "Heavy snowfall", 77: "Snow grains", 80: "Slight rain showers", 81: "Moderate rain showers",
  82: "Violent rain showers", 85: "Slight snow showers", 86: "Heavy snow showers",
  95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
};

/** Keep selected-place, window and run identity together across delayed responses. */
export function matchesForecastSelection(result: SelectedWeatherForecast, selected: ForecastSelection, productId: string): boolean {
  return (Object.keys(selected) as (keyof ForecastSelection)[])
    .every((key) => key === "start" || key === "end"
      ? utcInstantKey(selected[key]) !== null && utcInstantKey(result.selection[key]) === utcInstantKey(selected[key])
      : result.selection[key] === selected[key])
    && (result.run === null
      ? result.values.length === 0 && result.status !== "available" && result.status !== "stale_run"
      : result.run.run_id === selected.run_id && result.run.product_id === productId)
    && result.values.every((row) => row.run_id === selected.run_id
      && Date.parse(row.valid_at) >= Date.parse(selected.start)
      && Date.parse(row.valid_at) < Date.parse(selected.end));
}

export function utcInstantKey(value: string): string | null {
  const epoch = Date.parse(value);
  return /(?:Z|\+00:00)$/.test(value) && Number.isFinite(epoch) ? new Date(epoch).toISOString() : null;
}

export const FORECAST_TEMPERATURE_RAMP = [
  { value: -20, color: "#3b4cc0" }, { value: 0, color: "#9abbff" },
  { value: 20, color: "#f7b89c" }, { value: 40, color: "#b40426" },
] as const;

export function forecastScalarColor(variable: ForecastVariable, value: number): string {
  if (FORECAST_VARIABLES[variable].unit === "degC") {
    return FORECAST_TEMPERATURE_RAMP.find((stop) => value <= stop.value)?.color ?? "#b40426";
  }
  if (FORECAST_VARIABLES[variable].unit === "%") {
    return value < 25 ? "#eff3ff" : value < 50 ? "#bdd7e7" : value < 75 ? "#6baed6" : "#2171b5";
  }
  return "#38bdf8";
}

export function formatForecastValue(row: WeatherForecastValue | undefined): string {
  if (!row) return "Not supplied";
  if (row.status !== "available") return FORECAST_STATUS_LABELS[row.status];
  if (row.value === null || !Number.isFinite(row.value)
    || row.unit !== FORECAST_VARIABLES[row.variable].unit) return "Invalid forecast value";
  if (row.variable === "weather_code") return WEATHER_CONDITIONS[row.value] ?? `Weather code ${row.value}`;
  const unit = row.unit === "degC" ? "°C" : row.unit === "degree" ? "° from north" : row.unit;
  return `${new Intl.NumberFormat("en", { maximumFractionDigits: 1 }).format(row.value)} ${unit}`;
}

export function forecastHours(values: WeatherForecastValue[]): Map<string, Map<ForecastVariable, WeatherForecastValue>> {
  const hours = new Map<string, Map<ForecastVariable, WeatherForecastValue>>();
  for (const row of [...values].sort((a, b) => Date.parse(a.valid_at) - Date.parse(b.valid_at))) {
    const at = utcInstantKey(row.valid_at);
    if (at === null) throw new Error("Forecast time must be an explicit UTC instant");
    const hour = hours.get(at) ?? new Map<ForecastVariable, WeatherForecastValue>();
    if (hour.has(row.variable)) throw new Error("Forecast location has duplicate variable/time values");
    hour.set(row.variable, row);
    hours.set(at, hour);
  }
  return hours;
}

export interface ForecastDay {
  day: string;
  low: number | null;
  high: number | null;
  complete: boolean;
  precipitation: number | null;
}

/** Summarize the local calendar day; partial days never claim a full-day total. */
export function forecastDays(values: WeatherForecastValue[], timezone: string): ForecastDay[] {
  const formatter = new Intl.DateTimeFormat("en-CA", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit" });
  const dayOf = (time: number) => formatter.format(time);
  const groups = new Map<string, [string, Map<ForecastVariable, WeatherForecastValue>][] >();
  for (const entry of forecastHours(values)) {
    const day = dayOf(Date.parse(entry[0]));
    groups.set(day, [...(groups.get(day) ?? []), entry]);
  }
  return [...groups].map(([day, hours]) => {
    const times = hours.map(([time]) => Date.parse(time));
    const temperatures = hours.map(([, fields]) => fields.get("temperature_2m"));
    const numbers = temperatures.flatMap((row) => row?.status === "available" && row.value !== null ? [row.value] : []);
    const complete = times.every((time, i) => i === 0 || time - times[i - 1] === 3_600_000)
      && dayOf(times[0] - 3_600_000) !== day
      && dayOf(times[times.length - 1] + 3_600_000) !== day
      && numbers.length === hours.length;
    // Precipitation belongs to its interval, not the date of its endpoint.
    const precipitationRows = values.filter((row) => row.variable === "precipitation"
      && row.interval_start !== null && row.interval_end !== null
      && dayOf(Date.parse(row.interval_start)) === day
      && dayOf(Date.parse(row.interval_end) - 1) === day);
    const ordered = precipitationRows.sort((a, b) => Date.parse(a.interval_start!) - Date.parse(b.interval_start!));
    const fullPrecipitation = complete && ordered.length === hours.length
      && ordered.every((row, i) => row.status === "available" && row.value !== null && row.unit === "mm"
        && Date.parse(row.interval_start!) === times[i]
        && Date.parse(row.interval_end!) - Date.parse(row.interval_start!) === 3_600_000);
    return { day, low: numbers.length ? Math.min(...numbers) : null,
      high: numbers.length ? Math.max(...numbers) : null, complete,
      precipitation: fullPrecipitation ? ordered.reduce((sum, row) => sum + row.value!, 0) : null };
  });
}

export function windVectorPresentation(u: number, v: number): { speed: number; from: number | null; to: number | null } {
  if (!Number.isFinite(u) || !Number.isFinite(v)) throw new RangeError("Wind components must be finite");
  const speed = Math.hypot(u, v);
  if (speed < 1e-8) return { speed: 0, from: null, to: null };
  const to = (Math.atan2(u, v) * 180 / Math.PI + 360) % 360;
  return { speed, from: (to + 180) % 360, to };
}
