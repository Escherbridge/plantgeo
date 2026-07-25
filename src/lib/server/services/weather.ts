import { z } from "zod";
import {
  fetchBoundedJson,
  UpstreamPayloadError,
} from "@/lib/server/http/bounded-upstream";
import { isFreshObservation } from "@/lib/server/services/environmental-time";

export interface CurrentWeather {
  observedAt: string;
  temperature: number;
  humidity: number;
  windSpeed: number;
  windDirection: number;
  precipitation: number;
}

const BASE_URL = new URL("https://api.open-meteo.com/v1/forecast");
const MAX_RESPONSE_BYTES = 128 * 1024;
const REQUEST_TIMEOUT_MS = 5_000;
const MAX_OBSERVATION_AGE_MS = 3 * 60 * 60 * 1_000;

const OpenMeteoCurrentSchema = z
  .object({
    current: z
      .object({
        time: z.number().int().nonnegative(),
        temperature_2m: z.number().finite().min(-100).max(70),
        relative_humidity_2m: z.number().finite().min(0).max(100),
        wind_speed_10m: z.number().finite().min(0).max(150),
        wind_direction_10m: z.number().finite().min(0).max(360),
        precipitation: z.number().finite().min(0).max(1_000),
      })
      .strip(),
  })
  .strip();

/**
 * Fetch current weather conditions from Open-Meteo (free, no API key required).
 */
export async function getCurrentWeather(
  lat: number,
  lon: number
): Promise<CurrentWeather> {
  if (
    !Number.isFinite(lat) ||
    lat < -90 ||
    lat > 90 ||
    !Number.isFinite(lon) ||
    lon < -180 ||
    lon > 180
  ) {
    throw new RangeError("Weather coordinates are outside WGS84 bounds");
  }

  const url = new URL(BASE_URL);
  url.search = new URLSearchParams({
    latitude: String(lat),
    longitude: String(lon),
    current:
      "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation",
    wind_speed_unit: "ms",
    timeformat: "unixtime",
    timezone: "GMT",
  }).toString();
  const payload = await fetchBoundedJson(url, { method: "GET" }, {
    maxBytes: MAX_RESPONSE_BYTES,
    timeoutMs: REQUEST_TIMEOUT_MS,
  });
  const parsed = OpenMeteoCurrentSchema.safeParse(payload);
  if (!parsed.success) {
    throw new UpstreamPayloadError("Open-Meteo returned an invalid current observation");
  }
  const c = parsed.data.current;
  const observedAt = new Date(c.time * 1_000).toISOString();
  if (!isFreshObservation(observedAt, MAX_OBSERVATION_AGE_MS)) {
    throw new UpstreamPayloadError("Open-Meteo returned a stale current observation");
  }

  return {
    observedAt,
    temperature: c.temperature_2m,
    humidity: c.relative_humidity_2m,
    windSpeed: c.wind_speed_10m,
    windDirection: c.wind_direction_10m,
    precipitation: c.precipitation,
  };
}
