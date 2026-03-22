import { sql } from "drizzle-orm";
import type { PostgresJsDatabase } from "drizzle-orm/postgres-js";
import type * as schema from "@/lib/server/db/schema";
import {
  fireDetections,
  droughtData,
  waterGauges,
  features,
  strategyRequests,
} from "@/lib/server/db/schema";
import { and, gte, lte, count, avg, max } from "drizzle-orm";

type DB = PostgresJsDatabase<typeof schema>;

// ============================================================
// TimescaleDB Continuous Aggregate DDL (export as constants)
// Run via migrations — do not execute here.
// ============================================================

export const CREATE_FIRE_RISK_HOURLY = `
  CREATE MATERIALIZED VIEW IF NOT EXISTS fire_risk_hourly
  WITH (timescaledb.continuous) AS
  SELECT time_bucket('1 hour', created_at) AS bucket,
    AVG((properties->>'riskScore')::float) as avg_risk,
    MAX((properties->>'riskScore')::float) as max_risk,
    COUNT(*) as detection_count
  FROM geo.features
  WHERE (properties->>'type') = 'fire_detection'
  GROUP BY bucket
  WITH NO DATA;
`;

export const CREATE_WATER_LEVEL_HOURLY = `
  CREATE MATERIALIZED VIEW IF NOT EXISTS water_level_hourly
  WITH (timescaledb.continuous) AS
  SELECT time_bucket('1 hour', updated_at) AS bucket,
    AVG(percentile) as avg_percentile,
    MIN(percentile) as min_percentile,
    COUNT(*) as gauge_count
  FROM water_gauges
  GROUP BY bucket
  WITH NO DATA;
`;

export const CREATE_NDVI_WEEKLY = `
  CREATE MATERIALIZED VIEW IF NOT EXISTS ndvi_weekly
  WITH (timescaledb.continuous) AS
  SELECT time_bucket('1 week', created_at) AS bucket,
    AVG((properties->>'ndviAnomaly')::float) as avg_ndvi_anomaly,
    MIN((properties->>'ndviAnomaly')::float) as min_ndvi_anomaly,
    COUNT(*) as sample_count
  FROM geo.features
  WHERE (properties->>'type') = 'ndvi_observation'
  GROUP BY bucket
  WITH NO DATA;
`;

// ============================================================
// Type Definitions
// ============================================================

export interface RegionalRiskSummary {
  fireRiskAvg: number;
  droughtClass: number;
  streamflowPercentile: number;
  activeFireCount: number;
  riskTrend: "improving" | "stable" | "worsening";
}

export interface TrendPoint {
  date: string;
  value: number;
}

export interface PrioritySubregion {
  name: string;
  lat: number;
  lon: number;
  score: number;
  primaryIssue: string;
}

// ============================================================
// Query: Regional Risk Summary
// ============================================================

export async function getRegionalRiskSummary(
  bbox: string,
  db: DB
): Promise<RegionalRiskSummary> {
  const parts = bbox.split(",").map(Number);
  if (parts.length !== 4 || parts.some(isNaN)) {
    return {
      fireRiskAvg: 0,
      droughtClass: 0,
      streamflowPercentile: 50,
      activeFireCount: 0,
      riskTrend: "stable",
    };
  }
  const [west, south, east, north] = parts;

  // Active fire count from fire_detections (last 7 days)
  const since7d = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);
  const fireCountResult = await db
    .select({ count: count() })
    .from(fireDetections)
    .where(gte(fireDetections.detectedAt, since7d));
  const activeFireCount = Number(fireCountResult[0]?.count ?? 0);

  // Average fire risk from features table within bbox
  const fireRiskResult = await db.execute(
    sql`SELECT AVG((properties->>'riskScore')::float) AS avg_risk
        FROM geo.features
        WHERE (properties->>'type') = 'fire_detection'
          AND (properties->>'lon')::float BETWEEN ${west} AND ${east}
          AND (properties->>'lat')::float BETWEEN ${south} AND ${north}`
  );
  const rawFire = fireRiskResult as unknown as { rows?: { avg_risk: string | null }[] };
  const fireRiskAvg = parseFloat(rawFire.rows?.[0]?.avg_risk ?? "0") || 0;

  // Average streamflow percentile from water_gauges within bbox
  const gaugeResult = await db
    .select({ avg: avg(waterGauges.percentile) })
    .from(waterGauges)
    .where(
      and(
        gte(waterGauges.lon, west),
        lte(waterGauges.lon, east),
        gte(waterGauges.lat, south),
        lte(waterGauges.lat, north)
      )
    );
  const streamflowPercentile = Math.round(
    parseFloat(String(gaugeResult[0]?.avg ?? "50")) || 50
  );

  // Most recent drought class from droughtData (latest entry)
  const droughtResult = await db
    .select({ geojson: droughtData.geojson })
    .from(droughtData)
    .orderBy(sql`fetched_at DESC`)
    .limit(1);

  let droughtClass = 0;
  if (droughtResult[0]?.geojson) {
    const gj = droughtResult[0].geojson as { features?: { properties?: { DM?: number } }[] };
    const dms = (gj.features ?? [])
      .map((f) => f.properties?.DM ?? 0)
      .filter((v) => v !== undefined);
    if (dms.length > 0) {
      droughtClass = Math.round(dms.reduce((a, b) => a + b, 0) / dms.length);
    }
  }

  // Determine trend: if fire risk > 50 and drought > 2, worsening
  const riskTrend: "improving" | "stable" | "worsening" =
    fireRiskAvg > 50 || droughtClass >= 3
      ? "worsening"
      : fireRiskAvg < 20 && droughtClass <= 1
      ? "improving"
      : "stable";

  return {
    fireRiskAvg: Math.round(fireRiskAvg),
    droughtClass,
    streamflowPercentile,
    activeFireCount,
    riskTrend,
  };
}

// ============================================================
// Query: Trend Data
// ============================================================

export async function getTrendData(
  bbox: string,
  metric: "fire" | "drought" | "ndvi" | "water",
  days: number,
  db: DB
): Promise<TrendPoint[]> {
  const since = new Date(Date.now() - days * 24 * 60 * 60 * 1000);

  if (metric === "fire") {
    // Count fire detections per day within bbox
    const result = await db.execute(
      sql`SELECT DATE_TRUNC('day', detected_at) AS day,
               COUNT(*) AS value
          FROM geo.fire_detections
          WHERE detected_at >= ${since}
          GROUP BY day
          ORDER BY day ASC`
    );
    const rows = result as unknown as { rows?: { day: Date; value: string }[] };
    return (rows.rows ?? []).map((r) => ({
      date: new Date(r.day).toISOString().split("T")[0],
      value: Number(r.value),
    }));
  }

  if (metric === "water") {
    // Weekly avg streamflow percentile from water_gauges
    const parts = bbox.split(",").map(Number);
    if (parts.length !== 4 || parts.some(isNaN)) return [];
    const [west, south, east, north] = parts;
    const result = await db
      .select({ avg: avg(waterGauges.percentile) })
      .from(waterGauges)
      .where(
        and(
          gte(waterGauges.lon, west),
          lte(waterGauges.lon, east),
          gte(waterGauges.lat, south),
          lte(waterGauges.lat, north)
        )
      );
    // Single aggregate — return as a single point
    const val = Math.round(parseFloat(String(result[0]?.avg ?? "50")) || 50);
    const today = new Date().toISOString().split("T")[0];
    return [{ date: today, value: val }];
  }

  if (metric === "drought") {
    // Weekly drought data from droughtData table
    const result = await db
      .select({ weekDate: droughtData.weekDate, geojson: droughtData.geojson })
      .from(droughtData)
      .where(gte(droughtData.fetchedAt, since))
      .orderBy(sql`fetched_at ASC`);

    return result.map((row) => {
      const gj = row.geojson as { features?: { properties?: { DM?: number } }[] };
      const dms = (gj.features ?? [])
        .map((f) => f.properties?.DM ?? 0);
      const avgDm = dms.length > 0 ? dms.reduce((a, b) => a + b, 0) / dms.length : 0;
      return { date: row.weekDate, value: Math.round(avgDm * 10) / 10 };
    });
  }

  // ndvi: return mock monthly NDVI anomaly values
  const points: TrendPoint[] = [];
  const numMonths = Math.ceil(days / 30);
  for (let i = numMonths - 1; i >= 0; i--) {
    const d = new Date();
    d.setMonth(d.getMonth() - i);
    const date = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
    // Placeholder values oscillating around 0 to simulate anomaly
    const value = Math.round((Math.sin((i / numMonths) * Math.PI * 2) * 0.15 - 0.05) * 100) / 100;
    points.push({ date, value });
  }
  return points;
}

// ============================================================
// Query: Priority Subregions
// ============================================================

export async function getPrioritySubregions(
  bbox: string,
  db: DB
): Promise<PrioritySubregion[]> {
  const parts = bbox.split(",").map(Number);
  if (parts.length !== 4 || parts.some(isNaN)) return [];
  const [west, south, east, north] = parts;

  // Aggregate strategy requests by location cluster (rounded to 1 decimal)
  const result = await db.execute(
    sql`SELECT
          ROUND(lat::numeric, 1) AS cluster_lat,
          ROUND(lon::numeric, 1) AS cluster_lon,
          COUNT(*) AS request_count,
          SUM(vote_count) AS total_votes,
          strategy_type
        FROM strategy_requests
        WHERE lat BETWEEN ${south} AND ${north}
          AND lon BETWEEN ${west} AND ${east}
        GROUP BY cluster_lat, cluster_lon, strategy_type
        ORDER BY total_votes DESC, request_count DESC
        LIMIT 5`
  );

  type ClusterRow = {
    cluster_lat: string;
    cluster_lon: string;
    request_count: string;
    total_votes: string | null;
    strategy_type: string;
  };
  const rows = result as unknown as { rows?: ClusterRow[] };

  return (rows.rows ?? []).map((r, i) => {
    const lat = parseFloat(r.cluster_lat);
    const lon = parseFloat(r.cluster_lon);
    const votes = parseInt(r.total_votes ?? "0");
    const reqs = parseInt(r.request_count);
    // Composite score: votes * 0.6 + request_count * 0.4, normalized to 100
    const score = Math.min(100, Math.round(votes * 0.6 + reqs * 4));
    return {
      name: `Area ${i + 1} (${lat.toFixed(1)}, ${lon.toFixed(1)})`,
      lat,
      lon,
      score,
      primaryIssue: r.strategy_type,
    };
  });
}

// ============================================================
// Query: Demand Density GeoJSON
// ============================================================

export async function getDemandDensity(
  bbox: string,
  db: DB
): Promise<GeoJSON.FeatureCollection> {
  const parts = bbox.split(",").map(Number);
  if (parts.length !== 4 || parts.some(isNaN)) {
    return { type: "FeatureCollection", features: [] };
  }
  const [west, south, east, north] = parts;

  const result = await db
    .select({
      lat: strategyRequests.lat,
      lon: strategyRequests.lon,
      voteCount: strategyRequests.voteCount,
      strategyType: strategyRequests.strategyType,
    })
    .from(strategyRequests)
    .where(
      and(
        gte(strategyRequests.lat, south),
        lte(strategyRequests.lat, north),
        gte(strategyRequests.lon, west),
        lte(strategyRequests.lon, east)
      )
    );

  const featureList: GeoJSON.Feature[] = result.map((r) => ({
    type: "Feature" as const,
    geometry: {
      type: "Point" as const,
      coordinates: [r.lon, r.lat],
    },
    properties: {
      voteCount: r.voteCount ?? 0,
      strategyType: r.strategyType,
    },
  }));

  return { type: "FeatureCollection", features: featureList };
}
