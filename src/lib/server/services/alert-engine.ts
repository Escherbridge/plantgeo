import { db } from "@/lib/server/db";
import { environmentalAlerts, priorityZones } from "@/lib/server/db/schema";
import { and, eq, gte, sql } from "drizzle-orm";
import {
  getDroughtCategoryAtPoint,
  getPublishedFireDetections,
  getPublishedStreamflowGauges,
} from "@/lib/server/services/environmental-read-model";
import { DROUGHT_CATEGORY_LABELS } from "@/lib/server/services/usdm-drought";

export type AlertSeverity = "info" | "warning" | "critical";

export interface NewAlert {
  userId: string;
  alertType: string;
  severity: AlertSeverity;
  title: string;
  body: string;
  metadata: Record<string, unknown>;
}

/**
 * Haversine distance in km between two lat/lon points.
 */
function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

const DROUGHT_RELEASE_MAX_AGE_MS = 14 * 24 * 60 * 60 * 1000;
const MAX_CLOCK_SKEW_MS = 5 * 60 * 1000;
const PRIORITY_ZONE_ALERTS_STATE: "inactive" | "runnable" = "inactive";

/** Rejects stale or malformed drought observations and release receipts. */
export function isFreshDroughtRelease(
  weekDate: string,
  fetchedAt: Date | null,
  now = new Date()
): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(weekDate) || !fetchedAt) return false;
  const observedAtMs = Date.parse(`${weekDate}T00:00:00.000Z`);
  const fetchedAtMs = fetchedAt.getTime();
  const nowMs = now.getTime();
  if (
    !Number.isFinite(observedAtMs) ||
    !Number.isFinite(fetchedAtMs) ||
    new Date(observedAtMs).toISOString().slice(0, 10) !== weekDate
  ) {
    return false;
  }

  const observationAge = nowMs - observedAtMs;
  const receiptAge = nowMs - fetchedAtMs;
  return (
    observationAge >= -MAX_CLOCK_SKEW_MS &&
    observationAge <= DROUGHT_RELEASE_MAX_AGE_MS &&
    receiptAge >= -MAX_CLOCK_SKEW_MS &&
    receiptAge <= DROUGHT_RELEASE_MAX_AGE_MS
  );
}

function bboxAroundPoint(lat: number, lon: number, radiusKm: number): string | null {
  const latDelta = radiusKm / 111.32;
  const longitudeScale = Math.cos((lat * Math.PI) / 180);
  if (Math.abs(longitudeScale) < 0.01) return null;
  const lonDelta = radiusKm / (111.32 * Math.abs(longitudeScale));
  const west = Math.max(-180, lon - lonDelta);
  const east = Math.min(180, lon + lonDelta);
  const south = Math.max(-90, lat - latDelta);
  const north = Math.min(90, lat + latDelta);
  return west < east && south < north ? `${west},${south},${east},${north}` : null;
}

function isPosition(value: GeoJSON.Position | undefined): value is [number, number] {
  return (
    Array.isArray(value) &&
    value.length >= 2 &&
    Number.isFinite(value[0]) &&
    Number.isFinite(value[1])
  );
}

function pointOnSegment(
  longitude: number,
  latitude: number,
  start: [number, number],
  end: [number, number]
): boolean {
  const cross =
    (latitude - start[1]) * (end[0] - start[0]) -
    (longitude - start[0]) * (end[1] - start[1]);
  if (Math.abs(cross) > 1e-10) return false;
  return (
    longitude >= Math.min(start[0], end[0]) &&
    longitude <= Math.max(start[0], end[0]) &&
    latitude >= Math.min(start[1], end[1]) &&
    latitude <= Math.max(start[1], end[1])
  );
}

function ringContainsPoint(
  ring: GeoJSON.Position[],
  latitude: number,
  longitude: number
): boolean {
  let inside = false;
  for (let index = 0, previous = ring.length - 1; index < ring.length; previous = index++) {
    const currentPosition = ring[index];
    const previousPosition = ring[previous];
    if (!isPosition(currentPosition) || !isPosition(previousPosition)) return false;
    if (pointOnSegment(longitude, latitude, previousPosition, currentPosition)) return true;
    const intersects =
      currentPosition[1] > latitude !== previousPosition[1] > latitude &&
      longitude <
        ((previousPosition[0] - currentPosition[0]) *
          (latitude - currentPosition[1])) /
          (previousPosition[1] - currentPosition[1]) +
          currentPosition[0];
    if (intersects) inside = !inside;
  }
  return inside;
}

function polygonContainsPoint(
  polygon: GeoJSON.Position[][],
  latitude: number,
  longitude: number
): boolean {
  const [outer, ...holes] = polygon;
  return Boolean(
    outer &&
      ringContainsPoint(outer, latitude, longitude) &&
      !holes.some((hole) => ringContainsPoint(hole, latitude, longitude))
  );
}

/** Returns the highest drought level whose accepted polygon contains the point. */
export function droughtLevelAtPoint(
  collection: GeoJSON.FeatureCollection,
  latitude: number,
  longitude: number
): number | null {
  let highest: number | null = null;
  for (const feature of collection.features) {
    const level = Number(feature.properties?.DM);
    if (!Number.isInteger(level) || level < 0 || level > 4) continue;
    const geometry = feature.geometry;
    const contains =
      geometry?.type === "Polygon"
        ? polygonContainsPoint(geometry.coordinates, latitude, longitude)
        : geometry?.type === "MultiPolygon"
          ? geometry.coordinates.some((polygon) =>
              polygonContainsPoint(polygon, latitude, longitude)
            )
          : false;
    if (contains && (highest === null || level > highest)) highest = level;
  }
  return highest;
}

/**
 * Returns true if an alert of the same type+user+location exists within the past 24 hours.
 * Uses metadata.watchedLocationId for location deduplication.
 */
export async function deduplicateAlert(
  userId: string,
  alertType: string,
  locationId: string
): Promise<boolean> {
  const cutoff = new Date(Date.now() - 24 * 60 * 60 * 1000);
  const existing = await db
    .select({ id: environmentalAlerts.id })
    .from(environmentalAlerts)
    .where(
      and(
        eq(environmentalAlerts.userId, userId),
        eq(environmentalAlerts.alertType, alertType),
        gte(environmentalAlerts.createdAt, cutoff),
        sql`${environmentalAlerts.metadata}->>'watchedLocationId' = ${locationId}`
      )
    )
    .limit(1);
  return existing.length > 0;
}

/**
 * Check for active fires within radiusKm of the given location.
 * Severity: <10km = critical, <30km = warning, <50km = info
 */
export async function checkFireProximityAlerts(
  userId: string,
  locationId: string,
  lat: number,
  lon: number,
  radiusKm: number
): Promise<NewAlert[]> {
  const isDuplicate = await deduplicateAlert(userId, "fire_proximity", locationId);
  if (isDuplicate) return [];

  const bbox = bboxAroundPoint(lat, lon, radiusKm);
  if (!bbox) return [];
  const fires = await getPublishedFireDetections(bbox, 1);

  let closestDistKm = Infinity;
  let closestFire: { lat: number; lon: number } | null = null;

  for (const feature of fires.features) {
    const [fireLon, fireLat] = feature.geometry.coordinates;
    const distKm = haversineKm(lat, lon, fireLat, fireLon);
    if (distKm < closestDistKm) {
      closestDistKm = distKm;
      closestFire = { lat: fireLat, lon: fireLon };
    }
  }

  if (!closestFire || closestDistKm > radiusKm) return [];

  let severity: AlertSeverity;
  let distLabel: string;
  if (closestDistKm < 10) {
    severity = "critical";
    distLabel = `${Math.round(closestDistKm)} km`;
  } else if (closestDistKm < 30) {
    severity = "warning";
    distLabel = `${Math.round(closestDistKm)} km`;
  } else {
    severity = "info";
    distLabel = `${Math.round(closestDistKm)} km`;
  }

  return [
    {
      userId,
      alertType: "fire_proximity",
      severity,
      title: `Active fire detected ${distLabel} away`,
      body: `A NASA FIRMS fire detection was recorded approximately ${distLabel} from your watched location. Monitor conditions closely.`,
      metadata: {
        watchedLocationId: locationId,
        source: "warehouse:fire-detections",
        fireLat: closestFire.lat,
        fireLon: closestFire.lon,
        distanceKm: Math.round(closestDistKm),
      },
    },
  ];
}

/**
 * Check for extreme drought (D3/D4) near the given location.
 * Creates a critical alert if latest USDM data contains D3 or D4 polygon overlapping the point.
 */
export async function checkDroughtAlerts(
  userId: string,
  locationId: string,
  lat: number,
  lon: number
): Promise<NewAlert[]> {
  const isDuplicate = await deduplicateAlert(userId, "drought_escalation", locationId);
  if (isDuplicate) return [];

  // Containment is evaluated in PostGIS against the stored release geometry:
  // the national collection is far too large to load into Node per watched
  // location. getDroughtCategoryAtPoint already rejects a stale release.
  const observed = await getDroughtCategoryAtPoint(lat, lon);
  if (!observed || observed.dmCategory < 3) return [];

  const label = DROUGHT_CATEGORY_LABELS[observed.dmCategory];

  return [
    {
      userId,
      alertType: "drought_escalation",
      severity: "critical",
      title: `${label} detected near watched location`,
      body: `The US Drought Monitor reports ${label} conditions in your region as of the latest weekly update (${observed.validDate}).`,
      metadata: {
        watchedLocationId: locationId,
        source: "warehouse:drought-usdm",
        sourceUrl: observed.sourceUrl,
        observedAt: observed.observedAt,
        droughtClass: observed.dmCategory,
        weekDate: observed.validDate,
        lat,
        lon,
      },
    },
  ];
}

/**
 * Check for critically low streamflow at the nearest USGS gauge.
 */
export async function checkStreamflowAlerts(
  userId: string,
  locationId: string,
  lat: number,
  lon: number
): Promise<NewAlert[]> {
  const isDuplicate = await deduplicateAlert(userId, "streamflow_critical", locationId);
  if (isDuplicate) return [];

  const bbox = bboxAroundPoint(lat, lon, 200);
  if (!bbox) return [];
  const criticalGauges = (await getPublishedStreamflowGauges(bbox)).filter(
    (gauge) => gauge.condition === "critically_low"
  );

  if (criticalGauges.length === 0) return [];

  let nearest = null;
  let nearestDist = Infinity;

  for (const gauge of criticalGauges) {
    const dist = haversineKm(lat, lon, gauge.lat, gauge.lon);
    if (dist < nearestDist) {
      nearestDist = dist;
      nearest = gauge;
    }
  }

  if (!nearest || nearestDist > 200) return [];

  return [
    {
      userId,
      alertType: "streamflow_critical",
      severity: "warning",
      title: `Critically low streamflow at ${nearest.siteName ?? `USGS ${nearest.siteNo}`}`,
      body: `USGS gauge ${nearest.siteName ?? nearest.siteNo} (${Math.round(nearestDist)} km away) is reporting critically low streamflow${nearest.flowCfs !== null ? ` (${nearest.flowCfs?.toFixed(1)} cfs)` : ""}. Drought stress conditions likely.`,
      metadata: {
        watchedLocationId: locationId,
        source: "warehouse:water-gauges",
        siteNo: nearest.siteNo,
        siteName: nearest.siteName,
        flowCfs: nearest.flowCfs,
        percentile: nearest.percentile,
        distanceKm: Math.round(nearestDist),
        gaugeLat: nearest.lat,
        gaugeLon: nearest.lon,
      },
    },
  ];
}

/**
 * Check if new priority zones appeared within radiusKm in the past 24 hours.
 */
export async function checkPriorityZoneAlerts(
  userId: string,
  locationId: string,
  lat: number,
  lon: number,
  radiusKm: number
): Promise<NewAlert[]> {
  if (PRIORITY_ZONE_ALERTS_STATE === "inactive") return [];

  const isDuplicate = await deduplicateAlert(userId, "priority_zone_created", locationId);
  if (isDuplicate) return [];

  const cutoff = new Date(Date.now() - 24 * 60 * 60 * 1000);

  const recentZones = await db
    .select()
    .from(priorityZones)
    .where(gte(priorityZones.computedAt, cutoff))
    .limit(100);

  if (recentZones.length === 0) return [];

  const nearbyZones = recentZones.filter((z) => {
    if (z.centroidLat === null || z.centroidLon === null) return false;
    return haversineKm(lat, lon, z.centroidLat, z.centroidLon) <= radiusKm;
  });

  if (nearbyZones.length === 0) return [];

  const zoneTypes = [...new Set(nearbyZones.map((z) => z.strategyType))].join(", ");

  return [
    {
      userId,
      alertType: "priority_zone_created",
      severity: "info",
      title: `${nearbyZones.length} new priority zone${nearbyZones.length > 1 ? "s" : ""} near your location`,
      body: `Community members have identified ${nearbyZones.length} new priority zone${nearbyZones.length > 1 ? "s" : ""} (${zoneTypes}) within ${radiusKm} km of your watched location.`,
      metadata: {
        watchedLocationId: locationId,
        zoneCount: nearbyZones.length,
        strategyTypes: zoneTypes,
        lat,
        lon,
      },
    },
  ];
}
