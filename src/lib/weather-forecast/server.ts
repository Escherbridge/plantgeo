import { decodeForecastResponse, forecastQuerySchema, type ForecastQuery } from "./contract";

const MAX_RESPONSE_BYTES = 256 * 1024;

/** Read the local governed forecast plane; see AGENTS.md for activation limits. */
export async function readWeatherForecast(input: ForecastQuery, signal?: AbortSignal) {
  const query = forecastQuerySchema.parse(input);
  const configured = process.env.WEATHER_FORECAST_LOCAL_URL;
  if (process.env.NODE_ENV === "production" || !configured) throw new Error("Local forecast service is not enabled.");
  const base = new URL(configured);
  if (base.protocol !== "http:" || !["localhost", "127.0.0.1", "[::1]"].includes(base.hostname)
    || base.username || base.password || base.search || base.hash || base.pathname !== "/") {
    throw new Error("Local forecast service must use a loopback HTTP origin.");
  }
  const url = new URL("/weather-forecast/location", base);
  Object.entries(query).forEach(([key, value]) => url.searchParams.set(key, String(value)));
  const timeout = AbortSignal.timeout(10_000);
  const response = await fetch(url, {
    cache: "no-store", redirect: "error", signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
  });
  if (!response.ok || !response.body) throw new Error("Local forecast plane is unavailable.");
  if (Number(response.headers.get("content-length")) > MAX_RESPONSE_BYTES) throw new Error("Forecast response exceeds the byte limit.");
  const reader = response.body.getReader();
  let bytes = 0;
  const chunks: Uint8Array[] = [];
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > MAX_RESPONSE_BYTES) throw new Error("Forecast response exceeds the byte limit.");
      chunks.push(value);
    }
  } finally {
    await reader.cancel();
  }
  const payload = Buffer.concat(chunks).toString("utf8");
  return decodeForecastResponse(JSON.parse(payload), query);
}

export const weatherForecastTool = {
  name: "get_weather_forecast_window",
  description: "Read a synthetic local Parquet forecast for the exact selected coordinates, pinned run and UTC window. State fixture provenance, missingness, source support and units; no live forecast is admitted.",
  inputSchema: forecastQuerySchema,
  execute: readWeatherForecast,
  registration: "withheld-pending-owner-transfer",
} as const;
