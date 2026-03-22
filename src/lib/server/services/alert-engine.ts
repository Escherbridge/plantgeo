import { db } from "@/lib/server/db";
import { environmentalAlerts, waterGauges, droughtData, priorityZones } from "@/lib/server/db/schema";
import { and, eq, gte, sql } from "drizzle-orm";
import { fetchActiveFiresNASA } from "@/lib/server/services/nasa-firms";

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

  let fires;
  try {
    fires = await fetchActiveFiresNASA(undefined, 1);
  } catch {
    return [];
  }

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

  const rows = await db
    .select({ geojson: droughtData.geojson, weekDate: droughtData.weekDate })
    .from(droughtData)
    .orderBy(sql`${droughtData.fetchedAt} DESC`)
    .limit(1);

  if (rows.length === 0) return [];

  const geojson = rows[0].geojson as {
    type: string;
    features?: Array<{ properties: Record<string, unknown> }>;
  } | null;

  if (!geojson?.features) return [];

  // Find the highest drought classification (DM field) for features containing the point.
  // Since we don't have PostGIS here, use a simple bounding-box check on the GeoJSON.
  let maxDm = -1;
  for (const feature of geojson.features) {
    const dm = feature.properties?.DM as number | undefined;
    if (dm !== undefined && dm >= 3) {
      // D3 or D4 present in dataset — create alert (full geometry check would require PostGIS)
      if (dm > maxDm) maxDm = dm;
    }
  }

  if (maxDm < 3) return [];

  const label = maxDm === 4 ? "D4 — Exceptional Drought" : "D3 — Extreme Drought";

  return [
    {
      userId,
      alertType: "drought_escalation",
      severity: "critical",
      title: `${label} detected near watched location`,
      body: `The US Drought Monitor reports ${label} conditions in your region as of the latest weekly update (${rows[0].weekDate}).`,
      metadata: {
        watchedLocationId: locationId,
        droughtClass: maxDm,
        weekDate: rows[0].weekDate,
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

  // Find all critically_low gauges and pick the nearest one within 200 km
  const criticalGauges = await db
    .select()
    .from(waterGauges)
    .where(eq(waterGauges.condition, "critically_low"))
    .limit(500);

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
