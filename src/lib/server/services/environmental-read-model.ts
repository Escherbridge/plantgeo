import { and, desc, eq, gte, lte, sql } from "drizzle-orm";
import { db } from "@/lib/server/db";
import { droughtData, features, layers } from "@/lib/server/db/schema";
import type { GroundwaterWell, WaterGauge } from "./usgs-water";
import {
  isFreshObservation,
  parseFirmsObservationTime,
  parseZonedObservationTime,
} from "./environmental-time";

const MAX_ROWS = 2_000;
const STREAMFLOW_MAX_AGE_MS = 6 * 60 * 60 * 1_000;
const DROUGHT_MAX_AGE_MS = 14 * 24 * 60 * 60 * 1_000;

function parseBbox(value: string): [number, number, number, number] {
  const coordinates = value.split(",").map(Number);
  if (
    coordinates.length !== 4 ||
    coordinates.some((coordinate) => !Number.isFinite(coordinate))
  ) {
    throw new RangeError("Bounding box must contain four finite numbers");
  }
  const [west, south, east, north] = coordinates;
  if (
    west < -180 ||
    east > 180 ||
    south < -90 ||
    north > 90 ||
    west >= east ||
    south >= north
  ) {
    throw new RangeError("Bounding box must be ordered within WGS84 bounds");
  }
  return [west, south, east, north];
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

function parsePoint(value: unknown): [number, number] | null {
  const geometry = asRecord(value);
  const coordinates = geometry?.coordinates;
  if (geometry?.type !== "Point" || !Array.isArray(coordinates)) return null;
  const longitude = Number(coordinates[0]);
  const latitude = Number(coordinates[1]);
  if (
    !Number.isFinite(longitude) ||
    longitude < -180 ||
    longitude > 180 ||
    !Number.isFinite(latitude) ||
    latitude < -90 ||
    latitude > 90
  ) {
    return null;
  }
  return [longitude, latitude];
}

function finiteNumber(value: unknown): number | null {
  if (
    (typeof value !== "number" && typeof value !== "string") ||
    (typeof value === "string" && value.trim() === "")
  ) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** Reads bounded fire observations already accepted into the platform store. */
export async function getPublishedFireDetections(
  bbox?: string,
  dayRange = 1
): Promise<GeoJSON.FeatureCollection<GeoJSON.Point>> {
  const area = bbox ? parseBbox(bbox) : null;
  const since = new Date(Date.now() - Math.min(10, Math.max(1, dayRange)) * 86_400_000);
  const rows = await db
    .select({ properties: features.properties })
    .from(features)
    .innerJoin(layers, eq(features.layerId, layers.id))
    .where(
      and(
        eq(layers.name, process.env.FIRMS_LAYER_ID ?? "fire-detections"),
        eq(features.status, "published"),
        gte(features.createdAt, since),
        area ? gte(sql<number>`ST_X(${features.geom})`, area[0]) : undefined,
        area ? lte(sql<number>`ST_X(${features.geom})`, area[2]) : undefined,
        area ? gte(sql<number>`ST_Y(${features.geom})`, area[1]) : undefined,
        area ? lte(sql<number>`ST_Y(${features.geom})`, area[3]) : undefined
      )
    )
    .orderBy(desc(features.createdAt))
    .limit(MAX_ROWS);

  const published: GeoJSON.Feature<GeoJSON.Point>[] = [];
  for (const row of rows) {
    const properties = asRecord(row.properties);
    const point = parsePoint(properties?.geometry);
    const observedAt = properties
      ? parseFirmsObservationTime(properties)
      : null;
    if (
      !properties ||
      !point ||
      !observedAt ||
      !isFreshObservation(observedAt, Math.min(10, Math.max(1, dayRange)) * 86_400_000)
    ) {
      continue;
    }
    const { geometry: _geometry, ...safeProperties } = properties;
    published.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: point },
      properties: { ...safeProperties, observedAt },
    });
  }
  return { type: "FeatureCollection", features: published };
}

/** Reads the latest warehouse-backed streamflow observations in a viewport. */
export async function getPublishedStreamflowGauges(
  bbox: string
): Promise<WaterGauge[]> {
  const [west, south, east, north] = parseBbox(bbox);
  const rows = await db
    .select({ properties: features.properties })
    .from(features)
    .innerJoin(layers, eq(features.layerId, layers.id))
    .where(
      and(
        eq(layers.name, process.env.WATER_GAUGES_LAYER_ID ?? "water-gauges"),
        eq(features.status, "published"),
        gte(sql<number>`ST_X(${features.geom})`, west),
        lte(sql<number>`ST_X(${features.geom})`, east),
        gte(sql<number>`ST_Y(${features.geom})`, south),
        lte(sql<number>`ST_Y(${features.geom})`, north)
      )
    )
    .orderBy(desc(features.createdAt))
    .limit(MAX_ROWS);

  const gauges = new Map<string, WaterGauge>();
  for (const row of rows) {
    const value = asRecord(row.properties);
    if (!value) continue;
    const siteNo = typeof value.siteNo === "string" ? value.siteNo : "";
    const point = parsePoint(value.geometry);
    const updatedAt = parseZonedObservationTime(value.updatedAt);
    if (
      !siteNo ||
      !point ||
      !updatedAt ||
      !isFreshObservation(updatedAt, STREAMFLOW_MAX_AGE_MS) ||
      gauges.has(siteNo)
    ) {
      continue;
    }

    const condition = value?.condition;
    const trend = value?.trend;
    gauges.set(siteNo, {
      siteNo,
      siteName: typeof value.siteName === "string" ? value.siteName : siteNo,
      lat: point[1],
      lon: point[0],
      flowCfs: finiteNumber(value.flowCfs),
      percentile: finiteNumber(value.percentile),
      condition:
        condition === "above_normal" ||
        condition === "normal" ||
        condition === "below_normal" ||
        condition === "low" ||
        condition === "critically_low"
          ? condition
          : "unknown",
      trend:
        trend === "rising" || trend === "stable" || trend === "declining"
          ? trend
          : null,
      updatedAt,
    });
  }
  return [...gauges.values()];
}

/** Reads the last accepted USDM publication; never fetches upstream on request. */
export async function getPublishedDroughtClassification(): Promise<
  GeoJSON.FeatureCollection & {
    availability: "published" | "unavailable";
    observedAt: string | null;
    reason?: "not_published" | "invalid_observation_time" | "stale";
  }
> {
  const [latest] = await db
    .select({
      geojson: droughtData.geojson,
      weekDate: droughtData.weekDate,
    })
    .from(droughtData)
    .orderBy(desc(droughtData.fetchedAt))
    .limit(1);
  const collection = asRecord(latest?.geojson);
  if (
    !latest ||
    collection?.type !== "FeatureCollection" ||
    !Array.isArray(collection.features)
  ) {
    return {
      type: "FeatureCollection",
      features: [],
      availability: "unavailable",
      observedAt: null,
      reason: "not_published",
    };
  }
  const observedAt = parseZonedObservationTime(
    latest.weekDate.includes("T") ? latest.weekDate : `${latest.weekDate}T00:00:00Z`
  );
  if (!observedAt) {
    return {
      type: "FeatureCollection",
      features: [],
      availability: "unavailable",
      observedAt: null,
      reason: "invalid_observation_time",
    };
  }
  if (!isFreshObservation(observedAt, DROUGHT_MAX_AGE_MS)) {
    return {
      type: "FeatureCollection",
      features: [],
      availability: "unavailable",
      observedAt,
      reason: "stale",
    };
  }
  return {
    type: "FeatureCollection",
    features: collection.features as GeoJSON.Feature[],
    availability: "published",
    observedAt,
  };
}

/** Reserved read model for a future versioned groundwater layer. */
export async function getPublishedGroundwaterWells(
  bbox: string
): Promise<GroundwaterWell[]> {
  parseBbox(bbox);
  return [];
}
