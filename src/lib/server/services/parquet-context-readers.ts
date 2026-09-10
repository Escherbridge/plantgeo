import type { WaterGauge } from "@/lib/environmental/water";
import { haversineDistance } from "@/lib/map/measurement";
import type { PublishedDroughtCollection, PublishedWeatherObservation } from "./environmental-read-model";
import { getParquetDrought, getParquetWaterGauges, getParquetWeatherObservations, rejectAborted, type ParquetReaderResult } from "./parquet-trpc-readers";

/** Preserve unwritten detail-rung evidence across the context assembler's settled reads. */
export class ParquetContextReadError extends Error {
  constructor(message: string, readonly rungNotWritten: boolean = false) {
    super(message);
  }
}

/** Refuse incomplete reads before adapting to legacy value-only payloads. */
function rows<T>(read: ParquetReaderResult<readonly T[]>): readonly T[] {
  rejectAborted(read);
  if (read.state === "upstream_unavailable") throw new ParquetContextReadError(read.fault.message);
  if (read.state === "not_generated") throw new ParquetContextReadError("The requested detail rung (zoom=13) has not been published.", true);
  if (read.state === "absent") return [];
  if (read.truncated) throw new ParquetContextReadError("The bounded Parquet read was truncated; nearest evidence cannot be established.");
  return read.data;
}

/** Read actual gauge identities at the detail rung; see AGENTS.md §parquet-context-readers. */
export async function getContextWaterGauges(bbox: string, date?: string): Promise<WaterGauge[]> {
  const gauges = rows(await getParquetWaterGauges({ bbox, date, mapZoom: 13 }));
  return gauges.flatMap((gauge): WaterGauge[] => {
    if (!gauge.siteNumber || !gauge.siteName || gauge.latitude === null || gauge.longitude === null || !Number.isFinite(gauge.latitude) || !Number.isFinite(gauge.longitude)) {
      throw new ParquetContextReadError("A detail gauge lacks identity or location; nearest evidence cannot be established.");
    }
    const condition = gauge.condition;
    const trend = gauge.trend;
    return [{
      siteNo: gauge.siteNumber, siteName: gauge.siteName, lat: gauge.latitude, lon: gauge.longitude,
      flowCfs: gauge.flowCfs, percentile: gauge.percentile, updatedAt: gauge.observedAt,
      condition: condition === "above_normal" || condition === "normal" || condition === "below_normal" || condition === "low" || condition === "critically_low" ? condition : "unknown",
      trend: trend === "rising" || trend === "stable" || trend === "declining" ? trend : null,
    }];
  });
}

/** Adapt the published weather units without inventing missing direction. */
export async function getContextWeatherForBbox(bbox: string, date?: string, signal?: AbortSignal): Promise<PublishedWeatherObservation[]> {
  return rows(await getParquetWeatherObservations({ bbox, date, mapZoom: 13, signal })).map((row) => ({
    lat: row.latitude, lon: row.longitude, observedAt: row.observedAt,
    temperature: row.temperatureC, humidity: row.relativeHumidityPct,
    windSpeed: row.windSpeedMs, windDirection: row.windDirectionDeg, precipitation: row.precipitationMm,
  }));
}

/** Find the nearest sample inside the explicit half-degree context window. */
export async function getContextWeatherForPoint(lat: number, lon: number, signal?: AbortSignal): Promise<PublishedWeatherObservation | null> {
  const bbox = [Math.max(-180, lon - 0.25), Math.max(-90, lat - 0.25), Math.min(180, lon + 0.25), Math.min(90, lat + 0.25)].join(",");
  const observations = await getContextWeatherForBbox(bbox, undefined, signal);
  return observations.reduce<PublishedWeatherObservation | null>((nearest, row) =>
    nearest === null || haversineDistance([lon, lat], [row.lon, row.lat]) < haversineDistance([lon, lat], [nearest.lon, nearest.lat]) ? row : nearest, null);
}

/** Carry the drought release's own date and polygons into the AI payload. */
export async function getContextDrought(bbox?: string, date?: string): Promise<PublishedDroughtCollection> {
  const read = await getParquetDrought({ bbox, date, mapZoom: 13 });
  const areas = rows(read);
  return {
    type: "FeatureCollection", availability: read.state === "ready" ? "published" : "unavailable",
    observedAt: read.state === "ready" ? `${areas[0]?.validDate ?? read.servedDay}T00:00:00Z` : null,
    features: areas.map((area) => ({ type: "Feature", id: area.areaId, geometry: area.geometry, properties: { DM: area.droughtCategory, validDate: area.validDate, sourceUrl: area.sourceUrl } })),
  };
}
