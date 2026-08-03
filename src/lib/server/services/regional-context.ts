import { and, desc, eq, gte, lte, sql } from "drizzle-orm";
import { db } from "@/lib/server/db";
import { features, layers } from "@/lib/server/db/schema";
import type { SoilProperties } from "@/lib/server/services/soilgrids";
import {
  getStrategyRecommendations,
  type StrategyScore,
} from "@/lib/server/services/strategy-scoring";
import {
  getPublishedDroughtClassification,
  getPublishedFireDetections,
  getPublishedStreamflowGauges,
  getPublishedWeatherForPoint,
  type PublishedWeatherObservation,
} from "@/lib/server/services/environmental-read-model";
import {
  getInterventionSuitability,
  type InterventionSuitability,
} from "@/lib/server/services/carbon-potential";
import type { WaterGauge } from "@/lib/server/services/usgs-water";
import { droughtLevelAtPoint } from "@/lib/server/services/alert-engine";

/** Half-width of the context window around the requested point, in degrees. */
const CONTEXT_RADIUS_DEGREES = 0.25;
const NEAREST_GAUGE_MAX_DEGREES = 0.5;
const MAX_FIRE_DETECTIONS = 25;
const MAX_FIRE_PERIMETERS = 10;

export interface NearbyFireDetection {
  observedAt: string;
  lat: number;
  lon: number;
  confidence: string | null;
  frp: number | null;
}

export interface NearbyFirePerimeter {
  name: string;
  irwinId: string | null;
  updatedAt: string;
}

export interface RegionalContextPayload {
  location: { lat: number; lon: number; geohash: string };
  strategyRecommendations: StrategyScore[] | null;
  soilProperties: SoilProperties | null;
  waterScarcity: {
    droughtClass: string | null;
    nearestGauge: WaterGauge | null;
  } | null;
  weather: PublishedWeatherObservation | null;
  fireDetections: { detections: NearbyFireDetection[]; totalCount: number } | null;
  firePerimeters: { perimeters: NearbyFirePerimeter[]; totalCount: number } | null;
  mtbsPerimeters: { fires: GeoJSON.Feature[]; totalCount: number } | null;
  carbonPotential: InterventionSuitability | null;
}

export interface RegionalContextResult {
  payload: RegionalContextPayload;
  dataFreshness: Record<string, string>;
  /** True when no warehouse layer resolved; the agent must say so rather than infer. */
  contextIsEmpty: boolean;
  cacheHit: boolean;
}

function droughtClassAtPoint(
  collection: GeoJSON.FeatureCollection,
  latitude: number,
  longitude: number
): string | null {
  const labels = [
    "D0 (Abnormally Dry)",
    "D1 (Moderate Drought)",
    "D2 (Severe Drought)",
    "D3 (Extreme Drought)",
    "D4 (Exceptional Drought)",
  ];
  const highest = droughtLevelAtPoint(collection, latitude, longitude);
  return highest !== null ? labels[highest] ?? `D${highest}` : null;
}

function nearestGauge(
  gauges: WaterGauge[],
  latitude: number,
  longitude: number
): WaterGauge | null {
  let nearest: WaterGauge | null = null;
  let distance = Number.POSITIVE_INFINITY;
  for (const gauge of gauges) {
    const candidate = Math.hypot(gauge.lat - latitude, gauge.lon - longitude);
    if (candidate < distance) {
      nearest = gauge;
      distance = candidate;
    }
  }
  return distance <= NEAREST_GAUGE_MAX_DEGREES ? nearest : null;
}

function settled<T>(result: PromiseSettledResult<T>, fallback: T): T {
  return result.status === "fulfilled" ? result.value : fallback;
}

function readString(source: Record<string, unknown>, key: string): string | null {
  const value = source[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function readNumber(source: Record<string, unknown>, key: string): number | null {
  const value = source[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Fire perimeters have no per-feature observation time upstream, so row
 * `updatedAt` is the only honest freshness signal available for them.
 */
async function readPublishedFirePerimeters(
  west: number,
  south: number,
  east: number,
  north: number
): Promise<{ perimeters: NearbyFirePerimeter[]; latestUpdatedAt: string | null }> {
  const rows = await db
    .select({ properties: features.properties, updatedAt: features.updatedAt })
    .from(features)
    .innerJoin(layers, eq(features.layerId, layers.id))
    .where(
      and(
        eq(layers.name, process.env.FIRES_LAYER_ID ?? "fire-perimeters"),
        eq(features.status, "published"),
        gte(sql<number>`ST_XMax(${features.geom})`, west),
        lte(sql<number>`ST_XMin(${features.geom})`, east),
        gte(sql<number>`ST_YMax(${features.geom})`, south),
        lte(sql<number>`ST_YMin(${features.geom})`, north)
      )
    )
    .orderBy(desc(features.updatedAt))
    .limit(MAX_FIRE_PERIMETERS);

  const perimeters: NearbyFirePerimeter[] = [];
  let latestUpdatedAt: string | null = null;
  for (const row of rows) {
    const properties =
      row.properties && typeof row.properties === "object"
        ? (row.properties as Record<string, unknown>)
        : null;
    if (!properties || !row.updatedAt) continue;
    const updatedAt = row.updatedAt.toISOString();
    latestUpdatedAt ??= updatedAt;
    perimeters.push({
      name:
        readString(properties, "name") ??
        readString(properties, "id") ??
        "Unnamed perimeter",
      irwinId: readString(properties, "irwinId"),
      updatedAt,
    });
  }
  return { perimeters, latestUpdatedAt };
}

/**
 * Assembles published warehouse observations for the agent. Sources that have
 * not been published resolve to `unavailable` in `dataFreshness` rather than
 * blocking the request, so the agent can still answer while saying what it
 * could not see.
 */
export async function assembleRegionalContext(
  lat: number,
  lon: number
): Promise<RegionalContextResult> {
  const west = Math.max(-180, lon - CONTEXT_RADIUS_DEGREES);
  const south = Math.max(-90, lat - CONTEXT_RADIUS_DEGREES);
  const east = Math.min(180, lon + CONTEXT_RADIUS_DEGREES);
  const north = Math.min(90, lat + CONTEXT_RADIUS_DEGREES);
  const bbox = `${west},${south},${east},${north}`;

  const [strategy, drought, gauges, weather, fires, perimeters, carbon] =
    await Promise.allSettled([
      getStrategyRecommendations(lat, lon),
      getPublishedDroughtClassification(),
      getPublishedStreamflowGauges(bbox),
      getPublishedWeatherForPoint(lat, lon),
      getPublishedFireDetections(bbox),
      readPublishedFirePerimeters(west, south, east, north),
      getInterventionSuitability(lat, lon),
    ]);

  const strategyValues = settled(strategy, [] as StrategyScore[]);
  const droughtValue = drought.status === "fulfilled" ? drought.value : null;
  const gaugeValues = settled(gauges, [] as WaterGauge[]);
  const weatherValue = weather.status === "fulfilled" ? weather.value : null;
  const fireCollection = settled(fires, {
    type: "FeatureCollection",
    features: [],
  } as GeoJSON.FeatureCollection<GeoJSON.Point>);
  const perimeterValue = settled(perimeters, {
    perimeters: [] as NearbyFirePerimeter[],
    latestUpdatedAt: null as string | null,
  });
  const carbonValue = carbon.status === "fulfilled" ? carbon.value : null;

  const detections: NearbyFireDetection[] = [];
  for (const feature of fireCollection.features) {
    const properties = (feature.properties ?? {}) as Record<string, unknown>;
    const observedAt = readString(properties, "observedAt");
    if (!observedAt) continue;
    detections.push({
      observedAt,
      lon: feature.geometry.coordinates[0],
      lat: feature.geometry.coordinates[1],
      confidence: readString(properties, "confidence"),
      frp: readNumber(properties, "frp"),
    });
    if (detections.length >= MAX_FIRE_DETECTIONS) break;
  }
  const latestDetectionAt = detections
    .map((detection) => detection.observedAt)
    .sort()
    .at(-1);

  const dataFreshness: Record<string, string> = {
    drought:
      droughtValue?.availability === "published" && droughtValue.observedAt
        ? droughtValue.observedAt
        : "unavailable",
    streamflow:
      gaugeValues
        .map((gauge) => gauge.updatedAt)
        .filter((value) => Number.isFinite(Date.parse(value)))
        .sort()
        .at(-1) ?? "unavailable",
    weatherObservations: weatherValue?.observedAt ?? "unavailable",
    fireDetections: latestDetectionAt ?? "unavailable",
    firePerimeters: perimeterValue.latestUpdatedAt ?? "unavailable",
    strategyRecommendations:
      strategyValues.length > 0 ? "published_revision_required" : "unavailable",
    soilProperties: "unavailable",
    mtbsPerimeters: "unavailable",
    carbonPotential:
      carbonValue?.availability === "published"
        ? "published_revision_required"
        : "unavailable",
  };

  const payload: RegionalContextPayload = {
    location: { lat, lon, geohash: `${lat.toFixed(2)}_${lon.toFixed(2)}` },
    strategyRecommendations: strategyValues.length > 0 ? strategyValues : null,
    soilProperties: null,
    waterScarcity:
      droughtValue?.availability === "published" || gaugeValues.length > 0
        ? {
            droughtClass: droughtValue
              ? droughtClassAtPoint(droughtValue, lat, lon)
              : null,
            nearestGauge: nearestGauge(gaugeValues, lat, lon),
          }
        : null,
    weather: weatherValue,
    fireDetections: detections.length
      ? { detections, totalCount: fireCollection.features.length }
      : null,
    firePerimeters: perimeterValue.perimeters.length
      ? {
          perimeters: perimeterValue.perimeters,
          totalCount: perimeterValue.perimeters.length,
        }
      : null,
    mtbsPerimeters: null,
    carbonPotential:
      carbonValue?.availability === "published" ? carbonValue : null,
  };

  const contextIsEmpty = Object.values(dataFreshness).every(
    (value) => value === "unavailable"
  );

  return { payload, dataFreshness, contextIsEmpty, cacheHit: false };
}
