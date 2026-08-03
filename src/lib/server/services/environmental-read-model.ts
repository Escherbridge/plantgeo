import { and, desc, eq, gte, lte, sql } from "drizzle-orm";
import { db } from "@/lib/server/db";
import { droughtData, features, layers } from "@/lib/server/db/schema";
import { WEATHER_LAYER_ID } from "@/lib/server/layer-ids";
import type { GroundwaterWell, WaterGauge } from "./usgs-water";
import {
  firmsDayRange,
  isFreshObservation,
  parseFirmsObservationTime,
  parseZonedObservationTime,
} from "./environmental-time";

const MAX_ROWS = 2_000;
const STREAMFLOW_MAX_AGE_MS = 6 * 60 * 60 * 1_000;
const DROUGHT_MAX_AGE_MS = 14 * 24 * 60 * 60 * 1_000;
/** Matches the upstream Open-Meteo freshness contract in services/weather.ts. */
const WEATHER_MAX_AGE_MS = 3 * 60 * 60 * 1_000;
/** Nearest-first candidates scanned before giving up on a fresh observation. */
const WEATHER_CANDIDATE_ROWS = 8;
/** Sane upper bound on observations rendered for a single viewport bbox. */
const WEATHER_BBOX_MAX_ROWS = 500;

export function parseBbox(value: string): [number, number, number, number] {
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
  dayRange = firmsDayRange()
): Promise<GeoJSON.FeatureCollection<GeoJSON.Point>> {
  const area = bbox ? parseBbox(bbox) : null;
  const since = new Date(Date.now() - dayRange * 86_400_000);
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
      !isFreshObservation(observedAt, dayRange * 86_400_000)
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

export interface PublishedWeatherObservation {
  lat: number;
  lon: number;
  observedAt: string;
  temperature: number | null;
  humidity: number | null;
  windSpeed: number | null;
  windDirection: number | null;
  precipitation: number | null;
}

/**
 * Reads the nearest fresh warehouse-backed weather observation to a point.
 * runWeatherIngestionJob samples a coarse grid across INGEST_BBOX, so the
 * nearest sample is returned rather than an interpolation; null means no fresh
 * observation has been published near the point.
 */
export async function getPublishedWeatherForPoint(
  lat: number,
  lon: number
): Promise<PublishedWeatherObservation | null> {
  if (
    !Number.isFinite(lat) ||
    lat < -90 ||
    lat > 90 ||
    !Number.isFinite(lon) ||
    lon < -180 ||
    lon > 180
  ) {
    throw new RangeError("Point must be within WGS84 bounds");
  }

  // MATERIALIZED pins the layer/status filter ahead of the KNN sort, so a sparse
  // weather set cannot walk a large slice of the GiST index before finding its
  // candidates. Returned coordinates are read from the same geom column the sort
  // ranks -- never from the properties copy, which can drift from it silently.
  const rows = await db.execute<{
    properties: unknown;
    lon: number | null;
    lat: number | null;
  }>(sql`
    WITH candidates AS MATERIALIZED (
      SELECT f.properties, f.geom
      FROM geo.features f
      JOIN geo.layers l ON l.id = f.layer_id
      WHERE l.name = ${WEATHER_LAYER_ID}
        AND f.status = 'published'
    )
    SELECT
      properties,
      ST_X(geom) AS lon,
      ST_Y(geom) AS lat
    FROM candidates
    ORDER BY geom <-> ST_SetSRID(ST_MakePoint(${lon}, ${lat}), 4326)
    LIMIT ${WEATHER_CANDIDATE_ROWS}
  `);

  for (const row of rows) {
    const value = asRecord(row.properties);
    const observedAt = parseZonedObservationTime(value?.observedAt);
    const rowLat = finiteNumber(row.lat);
    const rowLon = finiteNumber(row.lon);
    if (
      !value ||
      rowLat === null ||
      rowLon === null ||
      !observedAt ||
      !isFreshObservation(observedAt, WEATHER_MAX_AGE_MS)
    ) {
      continue;
    }
    return {
      lat: rowLat,
      lon: rowLon,
      observedAt,
      temperature: finiteNumber(value.temperature),
      humidity: finiteNumber(value.humidity),
      windSpeed: finiteNumber(value.windSpeed),
      windDirection: finiteNumber(value.windDirection),
      precipitation: finiteNumber(value.precipitation),
    };
  }
  return null;
}

/** True when every rendered field was actually measured -- never zero-filled. */
export function isCompleteWeatherObservation(
  observation: Pick<
    PublishedWeatherObservation,
    "windSpeed" | "windDirection" | "temperature" | "humidity"
  >
): boolean {
  return (
    observation.windSpeed !== null &&
    observation.windDirection !== null &&
    observation.temperature !== null &&
    observation.humidity !== null
  );
}

/**
 * Reads every published, fresh, complete weather observation intersecting a
 * viewport bbox. Unlike getPublishedWeatherForPoint's nearest-1 KNN, this
 * powers the wind layer with the full warehouse spread instead of a single
 * sample -- capped at WEATHER_BBOX_MAX_ROWS to bound render cost.
 */
export async function getPublishedWeatherForBbox(
  bbox: string
): Promise<PublishedWeatherObservation[]> {
  const [west, south, east, north] = parseBbox(bbox);
  const rows = await db
    .select({ properties: features.properties })
    .from(features)
    .innerJoin(layers, eq(features.layerId, layers.id))
    .where(
      and(
        eq(layers.name, WEATHER_LAYER_ID),
        eq(features.status, "published"),
        gte(sql<number>`ST_X(${features.geom})`, west),
        lte(sql<number>`ST_X(${features.geom})`, east),
        gte(sql<number>`ST_Y(${features.geom})`, south),
        lte(sql<number>`ST_Y(${features.geom})`, north)
      )
    )
    .orderBy(desc(features.createdAt))
    .limit(WEATHER_BBOX_MAX_ROWS);

  const observations: PublishedWeatherObservation[] = [];
  for (const row of rows) {
    const value = asRecord(row.properties);
    const point = parsePoint(value?.geometry);
    const observedAt = parseZonedObservationTime(value?.observedAt);
    if (
      !value ||
      !point ||
      !observedAt ||
      !isFreshObservation(observedAt, WEATHER_MAX_AGE_MS)
    ) {
      continue;
    }
    const observation: PublishedWeatherObservation = {
      lat: point[1],
      lon: point[0],
      observedAt,
      temperature: finiteNumber(value.temperature),
      humidity: finiteNumber(value.humidity),
      windSpeed: finiteNumber(value.windSpeed),
      windDirection: finiteNumber(value.windDirection),
      precipitation: finiteNumber(value.precipitation),
    };
    if (isCompleteWeatherObservation(observation)) observations.push(observation);
  }
  return observations;
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
