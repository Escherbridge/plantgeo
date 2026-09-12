import type { ForecastQuery, ForecastResponse } from "./contract";

export const fixtureQuery: ForecastQuery = {
  run_id: "fixture-20260912T000000Z-v1", lat: 40, lon: -105,
  start: "2026-09-13T00:00:00Z", end: "2026-09-15T00:00:00.000Z",
};

export function makeForecastFixture(query = fixtureQuery, temperature = 12): ForecastResponse {
  const hourly = Array.from({ length: (Date.parse(query.end) - Date.parse(query.start)) / 3600_000 }, (_, index) => {
    const time = Date.parse(query.start) + index * 3600_000;
    return {
      valid_time: new Date(time).toISOString(), interval_start: new Date(time).toISOString(),
      interval_end: new Date(time + 3600_000).toISOString(),
      lead_hours: (time - Date.parse("2026-09-12T00:00:00Z")) / 3600_000,
      values: { temperature_2m: temperature, relative_humidity_2m: 50, cloud_cover: 30,
        precipitation: 0, wind_u_10m: 0, wind_v_10m: -2, wind_speed_10m: 2, wind_direction_10m: 0 },
      missingness: {},
    };
  });
  return {
    schema_version: "weather-forecast/v1", mode: "fixture", status: "ready", reason: null,
    run: { run_id: query.run_id, provider: "PlantGeo fixture", model: "deterministic-fixture-v1",
      initialization_time: "2026-09-12T00:00:00Z", issue_time: null,
      fetched_at: "2026-09-12T00:00:00Z", admitted_at: "2026-09-12T00:00:00Z", published_at: "2026-09-12T00:00:00Z",
      valid_start: "2026-09-12T00:00:00Z", valid_end: "2026-09-15T00:00:00Z", licence: "CC0-1.0",
      support: "sampled_point", source_resolution: "not-applicable: synthetic samples",
      lifecycle_basis: "synthetic_fixture_clock", production_admitted: false },
    request: query, support: { kind: "sampled_point", latitude: query.lat, longitude: query.lon, distance_km: 0 },
    variables: {
      temperature_2m: { unit: "degC", statistic: "instantaneous" },
      relative_humidity_2m: { unit: "%", statistic: "instantaneous" },
      cloud_cover: { unit: "%", statistic: "instantaneous" },
      precipitation: { unit: "mm", statistic: "sum_over_following_hour" },
      wind_u_10m: { unit: "m/s", statistic: "instantaneous_earth_relative" },
      wind_v_10m: { unit: "m/s", statistic: "instantaneous_earth_relative" },
      wind_speed_10m: { unit: "m/s", statistic: "derived_vector_magnitude" },
      wind_direction_10m: { unit: "degree", statistic: "meteorological_from_true_north" },
    },
    hourly,
    daily: [...new Set(hourly.map((row) => row.valid_time.slice(0, 10)))].map((day) => {
      const hours = hourly.filter((row) => row.valid_time.startsWith(day)).length;
      const missingness: Record<string, string> = hours === 24 ? {} : { precipitation: "partial-utc-day" };
      return { day, hours, expected_hours: 24, temperature_min: temperature, temperature_max: temperature,
        precipitation_sum: hours === 24 ? 0 : null, relative_humidity_mean: 50, cloud_cover_mean: 30,
        wind_speed: 2, wind_direction: 0, complete: hours === 24,
        missingness };
    }),
    uncertainty: { kind: "deterministic", message: "Synthetic deterministic fixture; no probability or confidence intervals." },
    limits: { max_hours: 72, max_points: 4, max_rows: 2304, max_bytes: 2097152 },
  };
}
