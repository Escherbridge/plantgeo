import type { IngestFeatureInput } from "@/lib/server/services/ingest";
import { ingestFeatures } from "@/lib/server/services/ingest";
import { fetchActiveFiresNASA } from "./nasa-firms";
import { getStreamflowGauges } from "./usgs-water";
import {
  isFreshObservation,
  parseFirmsObservationTime,
} from "./environmental-time";

const FIRMS_LAYER_ID = process.env.FIRMS_LAYER_ID ?? "fire-detections";
const WATER_GAUGES_LAYER_ID =
  process.env.WATER_GAUGES_LAYER_ID ?? "water-gauges";
const MAX_SOURCE_RECORDS = 5_000;

export type IngestionJobStatus = "ingested" | "skipped" | "failed";

export interface IngestionJobResult {
  source: "nasa-firms" | "usgs-streamflow" | "ndvi";
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
  dayRange = 1
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
