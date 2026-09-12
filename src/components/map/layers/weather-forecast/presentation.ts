import {
  FORECAST_VARIABLES, forecastScalarColor, utcInstantKey, windVectorPresentation,
  type ForecastVariable, type WeatherForecastValue,
} from "@/lib/environmental/weather-forecast";

export const MAX_FORECAST_POINTS = 2_000;

export function scalarForecastFeatures(values: WeatherForecastValue[], runId: string, validAt: string,
  variable: ForecastVariable): GeoJSON.FeatureCollection<GeoJSON.Point> {
  const instant = utcInstantKey(validAt);
  const rows = instant === null ? [] : values.filter((row) => row.run_id === runId
    && utcInstantKey(row.valid_at) === instant && row.variable === variable);
  if (rows.length > MAX_FORECAST_POINTS || new Set(rows.map((row) => row.sample_id)).size !== rows.length) {
    return { type: "FeatureCollection", features: [] };
  }
  return { type: "FeatureCollection", features: rows.filter((row) => row.status === "available"
    && row.value !== null && Number.isFinite(row.value) && row.unit === FORECAST_VARIABLES[variable].unit
    && Number.isFinite(row.longitude) && Math.abs(row.longitude) <= 180
    && Number.isFinite(row.latitude) && Math.abs(row.latitude) <= 90).map((row) => ({
      type: "Feature", id: row.sample_id, geometry: { type: "Point", coordinates: [row.longitude, row.latitude] },
      properties: { value: row.value, color: forecastScalarColor(variable, row.value!), unit: row.unit,
        variable, run_id: runId, valid_at: validAt, support: "sampled_point" },
    })) };
}

export function windForecastFeatures(values: WeatherForecastValue[], runId: string,
  validAt: string): GeoJSON.FeatureCollection<GeoJSON.Point> {
  const u = scalarForecastFeatures(values, runId, validAt, "wind_u_10m");
  const v = new Map(scalarForecastFeatures(values, runId, validAt, "wind_v_10m").features.map((point) => [point.id, point]));
  return { type: "FeatureCollection", features: u.features.flatMap((point) => {
    const north = v.get(point.id);
    if (!north || point.geometry.coordinates.some((coordinate, i) => coordinate !== north.geometry.coordinates[i])) return [];
    const wind = windVectorPresentation(point.properties!.value as number, north.properties!.value as number);
    if (wind.to === null) return [];
    return [{ ...point, properties: { ...point.properties, ...wind } }];
  }) };
}
