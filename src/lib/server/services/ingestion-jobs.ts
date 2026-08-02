import type { IngestFeatureInput } from "@/lib/server/services/ingest";
import { ingestFeatures } from "@/lib/server/services/ingest";
import { fetchActiveFiresNASA } from "./nasa-firms";
import { getStreamflowGauges } from "./usgs-water";
import { getCurrentWeather } from "./weather";
import { fetchWfigsFirePerimeters } from "./wfigs-fire-perimeters";
import {
  firmsDayRange,
  isFreshObservation,
  parseFirmsObservationTime,
} from "./environmental-time";

const FIRMS_LAYER_ID = process.env.FIRMS_LAYER_ID ?? "fire-detections";
const WATER_GAUGES_LAYER_ID =
  process.env.WATER_GAUGES_LAYER_ID ?? "water-gauges";
const WEATHER_LAYER_ID = process.env.WEATHER_LAYER_ID ?? "weather-observations";
const FIRE_PERIMETERS_LAYER_ID =
  process.env.FIRE_PERIMETERS_LAYER_ID ?? "fire-perimeters";
const MAX_SOURCE_RECORDS = 5_000;
/** 3x3 grid keeps a bounded fan-out into Open-Meteo per INGEST_BBOX. */
const WEATHER_SAMPLE_GRID = 3;

export type IngestionJobStatus = "ingested" | "skipped" | "failed";

export interface IngestionJobResult {
  source:
    | "nasa-firms"
    | "usgs-streamflow"
    | "ndvi"
    | "open-meteo"
    | "wfigs-fire-perimeters";
  status: IngestionJobStatus;
  recordsSeen: number;
  recordsWritten: number;
  truncated?: boolean;
  reason?: string;
}

function resolveBoundedBbox(override?: string): string | null {
  const value = override?.trim() || process.env.INGEST_BBOX?.trim();
  if (!value) return null;

  const parts = value.split(",").map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isFinite(part))) {
    throw new Error("INGEST_BBOX must be west,south,east,north");
  }

  const [west, south, east, north] = parts;
  if (
    west < -180 ||
    east > 180 ||
    south < -90 ||
    north > 90 ||
    west >= east ||
    south >= north ||
    east - west > 30 ||
    north - south > 20
  ) {
    throw new Error("INGEST_BBOX is outside the bounded ingestion policy");
  }

  return parts.join(",");
}

async function ingestInBatches(inputs: IngestFeatureInput[]): Promise<number> {
  return ingestFeatures(inputs);
}

function firmsObservationId(
  properties: {
    satellite: string;
    acqDate: string;
    acqTime: string;
  },
  coordinates: number[]
): string {
  return [
    properties.satellite,
    properties.acqDate,
    properties.acqTime,
    coordinates[1]?.toFixed(4),
    coordinates[0]?.toFixed(4),
  ].join(":");
}

/** Fetches bounded FIRMS observations and writes idempotent source events. */
export async function runFireIngestionJob(
  bbox?: string,
  dayRange = firmsDayRange()
): Promise<IngestionJobResult> {
  const area = resolveBoundedBbox(bbox);
  if (!area) {
    return {
      source: "nasa-firms",
      status: "skipped",
      recordsSeen: 0,
      recordsWritten: 0,
      reason: "INGEST_BBOX is not configured",
    };
  }

  const collection = await fetchActiveFiresNASA(area, dayRange);
  const selected = collection.features.slice(0, MAX_SOURCE_RECORDS);
  const maxObservationAgeMs =
    Math.min(10, Math.max(1, dayRange)) * 24 * 60 * 60 * 1_000;
  const records: IngestFeatureInput[] = selected.flatMap((feature) => {
    const observedAt = parseFirmsObservationTime(feature.properties);
    if (!observedAt || !isFreshObservation(observedAt, maxObservationAgeMs)) {
      return [];
    }
    return [
      {
        layerId: FIRMS_LAYER_ID,
        featureId: firmsObservationId(
          feature.properties,
          feature.geometry.coordinates
        ),
        properties: {
          ...feature.properties,
          observedAt,
          source: "NASA FIRMS",
          geometry: feature.geometry,
        },
        channel: "layer:fire-detections",
      },
    ];
  });

  return {
    source: "nasa-firms",
    status: "ingested",
    recordsSeen: collection.features.length,
    recordsWritten: await ingestInBatches(records),
    truncated: collection.features.length > selected.length,
  };
}

/** Fetches bounded USGS gauges and writes timestamped source observations. */
export async function runWaterDroughtIngestionJob(
  bbox?: string
): Promise<IngestionJobResult> {
  const area = resolveBoundedBbox(bbox);
  if (!area) {
    return {
      source: "usgs-streamflow",
      status: "skipped",
      recordsSeen: 0,
      recordsWritten: 0,
      reason: "INGEST_BBOX is not configured",
    };
  }

  const gauges = await getStreamflowGauges(area);
  const selected = gauges.slice(0, MAX_SOURCE_RECORDS);
  const records: IngestFeatureInput[] = selected.map((gauge) => ({
    layerId: WATER_GAUGES_LAYER_ID,
    featureId: `${gauge.siteNo}:${gauge.updatedAt}`,
    properties: {
      ...gauge,
      source: "USGS NWIS",
      geometry: {
        type: "Point",
        coordinates: [gauge.lon, gauge.lat],
      },
    },
    channel: "layer:water-gauges",
  }));

  return {
    source: "usgs-streamflow",
    status: "ingested",
    recordsSeen: gauges.length,
    recordsWritten: await ingestInBatches(records),
    truncated: gauges.length > selected.length,
  };
}

/** Samples an evenly spaced grid of points within a bounded bbox for point-based upstreams. */
function boundedSamplePoints(
  bbox: string,
  gridSize: number
): Array<{ lat: number; lon: number }> {
  const [west, south, east, north] = bbox.split(",").map(Number);
  const points: Array<{ lat: number; lon: number }> = [];
  for (let column = 0; column < gridSize; column++) {
    for (let row = 0; row < gridSize; row++) {
      points.push({
        lon: west + ((east - west) * (column + 0.5)) / gridSize,
        lat: south + ((north - south) * (row + 0.5)) / gridSize,
      });
    }
  }
  return points;
}

/** Fetches current weather for a bounded sample grid and writes fresh point observations. */
export async function runWeatherIngestionJob(
  bbox?: string
): Promise<IngestionJobResult> {
  const area = resolveBoundedBbox(bbox);
  if (!area) {
    return {
      source: "open-meteo",
      status: "skipped",
      recordsSeen: 0,
      recordsWritten: 0,
      reason: "INGEST_BBOX is not configured",
    };
  }

  const points = boundedSamplePoints(area, WEATHER_SAMPLE_GRID);
  const observations = await Promise.allSettled(
    points.map((point) => getCurrentWeather(point.lat, point.lon))
  );

  const records: IngestFeatureInput[] = [];
  observations.forEach((result, index) => {
    if (result.status !== "fulfilled") return;
    const { lat, lon } = points[index];
    const weather = result.value;
    records.push({
      layerId: WEATHER_LAYER_ID,
      featureId: `${lat.toFixed(4)}:${lon.toFixed(4)}:${weather.observedAt}`,
      properties: {
        ...weather,
        source: "Open-Meteo",
        geometry: { type: "Point", coordinates: [lon, lat] },
      },
      channel: "layer:weather-observations",
    });
  });

  return {
    source: "open-meteo",
    status: "ingested",
    recordsSeen: points.length,
    recordsWritten: await ingestInBatches(records),
  };
}

/** Fetches bounded WFIGS interagency fire perimeters and writes idempotent perimeter events. */
export async function runFirePerimetersIngestionJob(
  bbox?: string
): Promise<IngestionJobResult> {
  const area = resolveBoundedBbox(bbox);
  if (!area) {
    return {
      source: "wfigs-fire-perimeters",
      status: "skipped",
      recordsSeen: 0,
      recordsWritten: 0,
      reason: "INGEST_BBOX is not configured",
    };
  }

  const collection = await fetchWfigsFirePerimeters(area);
  const selected = collection.features.slice(0, MAX_SOURCE_RECORDS);
  // poly_PolygonDateTime is frequently null upstream and the "_Current" feed is
  // already scoped server-side to active (non-contained) incidents, so no
  // additional client-side freshness rejection is applied here.
  const records: IngestFeatureInput[] = selected.map((perimeter) => ({
    layerId: FIRE_PERIMETERS_LAYER_ID,
    featureId: perimeter.uniqueFireIdentifier,
    properties: {
      uniqueFireIdentifier: perimeter.uniqueFireIdentifier,
      irwinId: perimeter.irwinId,
      incidentName: perimeter.incidentName,
      fireDiscoveryDateTime: perimeter.fireDiscoveryDateTime,
      polygonDateTime: perimeter.polygonDateTime,
      gisAcres: perimeter.gisAcres,
      fireCause: perimeter.fireCause,
      incidentTypeCategory: perimeter.incidentTypeCategory,
      pooState: perimeter.pooState,
      percentContained: perimeter.percentContained,
      source: "WFIGS Interagency Fire Perimeters",
      geometry: perimeter.geometry,
    },
    channel: "layer:fire-perimeters",
  }));

  return {
    source: "wfigs-fire-perimeters",
    status: "ingested",
    recordsSeen: collection.features.length,
    recordsWritten: await ingestInBatches(records),
    truncated: collection.features.length > selected.length,
  };
}

/** Refuses to publish NDVI until a versioned warehouse adapter exists. */
export async function runVegetationIngestionJob(): Promise<IngestionJobResult> {
  return {
    source: "ndvi",
    status: "skipped",
    recordsSeen: 0,
    recordsWritten: 0,
    reason: "No versioned warehouse-backed NDVI adapter is configured",
  };
}

/** Runs independent sources without allowing one failure to erase other progress. */
export async function runAllIngestionJobs(): Promise<IngestionJobResult[]> {
  const jobs: Array<{
    source: IngestionJobResult["source"];
    run: () => Promise<IngestionJobResult>;
  }> = [
    { source: "nasa-firms", run: () => runFireIngestionJob() },
    { source: "usgs-streamflow", run: () => runWaterDroughtIngestionJob() },
    { source: "open-meteo", run: () => runWeatherIngestionJob() },
    {
      source: "wfigs-fire-perimeters",
      run: () => runFirePerimetersIngestionJob(),
    },
    { source: "ndvi", run: runVegetationIngestionJob },
  ];

  return Promise.all(
    jobs.map(async ({ source, run }) => {
      try {
        return await run();
      } catch (error) {
        return {
          source,
          status: "failed" as const,
          recordsSeen: 0,
          recordsWritten: 0,
          reason:
            error instanceof Error ? error.message : "Unknown ingestion failure",
        };
      }
    })
  );
}
