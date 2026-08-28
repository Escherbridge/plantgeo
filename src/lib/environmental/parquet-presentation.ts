import type { WaterGauge } from "@/lib/environmental/water";

/** Browser-safe mirror of the four public tRPC states. */
export type ParquetBrowserReaderResult<T> =
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
      evidence: {
        reason: string;
        upstreamResponse: string;
        recordedAt: string;
        runId: string;
      };
    }
  | {
      state: "not_generated";
      requestedDay: string;
      reason: "day_not_written" | "lane_never_written";
    }
  | {
      state: "upstream_unavailable";
      fault: { kind: string; message: string; status?: number };
    };

export interface ParquetBrowserWaterGauge {
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

export interface ParquetBrowserDroughtArea {
  areaId: string;
  validDate: string;
  droughtCategory: 0 | 1 | 2 | 3 | 4;
  sourceUrl: string;
  ingestedAt: string;
  geometry: GeoJSON.Polygon | GeoJSON.MultiPolygon;
}

export interface ParquetBrowserVegetationObservation {
  cellId: string | null;
  gridName: string;
  metricName: string;
  metricUnit: string;
  observedDay: string;
  metricValue: number;
  observationChecksum: string | null;
  dataAvailableAt: string;
  releaseCount: number;
  allowedClientExposure: boolean;
  longitude: number;
  latitude: number;
}

export interface ParquetBrowserVegetationWindow {
  firstDay: string;
  lastDay: string;
  observations: readonly ParquetBrowserVegetationObservation[];
}

export interface ParquetBrowserWeatherObservation {
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

/** An anonymous coarse-rung mean, located at the warehouse cell centroid. */
export interface WaterGaugeCell {
  latitude: number;
  longitude: number;
  flowCfs: number | null;
  observedAt: string;
  observedDay: string;
  source: string;
}

export interface WaterGaugePresentation {
  gauges: WaterGauge[];
  cells: WaterGaugeCell[];
  unlocatedRows: number;
}

const EMPTY_WATER_PRESENTATION: WaterGaugePresentation = {
  gauges: [],
  cells: [],
  unlocatedRows: 0,
};

function waterCondition(value: string | null): WaterGauge["condition"] {
  return value === "above_normal" ||
    value === "normal" ||
    value === "below_normal" ||
    value === "low" ||
    value === "critically_low"
    ? value
    : "unknown";
}

/** Separates named z13 gauges from anonymous z9/z5/z0 cells without inventing identities. */
export function presentParquetWater(
  result: ParquetBrowserReaderResult<readonly ParquetBrowserWaterGauge[]> | undefined
): WaterGaugePresentation {
  if (result?.state !== "ready") return EMPTY_WATER_PRESENTATION;

  const gauges: WaterGauge[] = [];
  const cells: WaterGaugeCell[] = [];
  let unlocatedRows = 0;
  for (const row of result.data) {
    if (row.latitude === null || row.longitude === null) {
      unlocatedRows += 1;
      continue;
    }
    if (row.siteNumber === null) {
      cells.push({
        latitude: row.latitude,
        longitude: row.longitude,
        flowCfs: row.flowCfs,
        observedAt: row.observedAt,
        observedDay: row.observedDay,
        source: row.source,
      });
      continue;
    }
    gauges.push({
      siteNo: row.siteNumber,
      siteName: row.siteName ?? "",
      lat: row.latitude,
      lon: row.longitude,
      flowCfs: row.flowCfs,
      percentile: row.percentile,
      condition: waterCondition(row.condition),
      trend:
        row.trend === "rising" || row.trend === "stable" || row.trend === "declining"
          ? row.trend
          : null,
      updatedAt: row.observedAt,
    });
  }
  return { gauges, cells, unlocatedRows };
}

/** Converts a published USDM release to the existing browser-safe GeoJSON presentation. */
export function presentParquetDrought(
  result: ParquetBrowserReaderResult<readonly ParquetBrowserDroughtArea[]> | undefined
): GeoJSON.FeatureCollection {
  if (result?.state !== "ready") return { type: "FeatureCollection", features: [] };
  return {
    type: "FeatureCollection",
    features: result.data.map((area) => ({
      type: "Feature" as const,
      id: area.areaId,
      geometry: area.geometry,
      properties: {
        DM: area.droughtCategory,
        label: `D${area.droughtCategory}`,
        validDate: area.validDate,
        observedAt: `${area.validDate}T00:00:00Z`,
        source: "US Drought Monitor",
        sourceUrl: area.sourceUrl,
      },
    })),
  };
}

/** Preserves measured vegetation support as points; no sampling square is fabricated. */
export function presentParquetVegetation(
  result: ParquetBrowserReaderResult<ParquetBrowserVegetationWindow> | undefined
): GeoJSON.FeatureCollection {
  if (result?.state !== "ready") return { type: "FeatureCollection", features: [] };
  return {
    type: "FeatureCollection",
    features: result.data.observations.map((observation, index) => ({
      type: "Feature" as const,
      id: observation.cellId ?? `${observation.longitude}:${observation.latitude}:${index}`,
      geometry: {
        type: "Point" as const,
        coordinates: [observation.longitude, observation.latitude],
      },
      properties: {
        cellId: observation.cellId,
        gridName: observation.gridName,
        metricName: observation.metricName,
        metricUnit: observation.metricUnit,
        ndvi: observation.metricValue,
        observedDay: observation.observedDay,
        dataAvailableAt: observation.dataAvailableAt,
        releaseCount: observation.releaseCount,
      },
    })),
  };
}

/** The weather layer's inert point vocabulary, projected from strict Parquet rows. */
export function presentParquetWeather(
  result: ParquetBrowserReaderResult<readonly ParquetBrowserWeatherObservation[]> | undefined
) {
  if (result?.state !== "ready") return [];
  return result.data.map((observation) => ({
    coordinates: [observation.longitude, observation.latitude] as [number, number],
    windSpeed: observation.windSpeedMs,
    windDirection: observation.windDirectionDeg,
    temperature: observation.temperatureC,
    humidity: observation.relativeHumidityPct,
    observedAt: observation.observedAt,
  }));
}
