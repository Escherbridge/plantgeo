import { db } from "@/lib/server/db";
import { priorityZones } from "@/lib/server/db/schema";
import { sql } from "drizzle-orm";

const STRATEGY_TYPES = [
  "keyline",
  "silvopasture",
  "reforestation",
  "biochar",
  "water_harvesting",
  "cover_cropping",
] as const;

interface ClusterRow extends Record<string, unknown> {
  strategy_type: string;
  cid: number;
  request_count: number;
  total_votes: number;
  geojson: string;
  centroid_lat: number;
  centroid_lon: number;
}

/**
 * Recompute priority zones for all strategy types using PostGIS ST_ClusterDBSCAN.
 * Clusters open/in-progress requests within ~5km (0.05 degrees) with >= 3 requests.
 * Convex hull polygons are stored as GeoJSON in the priority_zones table.
 */
export async function recomputePriorityZones(): Promise<void> {
  // Clear existing priority zones before recomputing
  await db.delete(priorityZones);

  const rows = await db.execute<ClusterRow>(sql`
    WITH clusters AS (
      SELECT
        strategy_type,
        ST_ClusterDBSCAN(
          ST_SetSRID(ST_MakePoint(lon, lat), 4326),
          eps := 0.05,
          minpoints := 3
        ) OVER (PARTITION BY strategy_type) AS cid,
        id,
        vote_count,
        lat,
        lon
      FROM strategy_requests
      WHERE status != 'completed'
    ),
    hulls AS (
      SELECT
        strategy_type,
        cid,
        COUNT(*)::int AS request_count,
        SUM(vote_count)::int AS total_votes,
        ST_AsGeoJSON(
          ST_ConvexHull(
            ST_Collect(ST_SetSRID(ST_MakePoint(lon, lat), 4326))
          )
        ) AS geojson,
        AVG(lat) AS centroid_lat,
        AVG(lon) AS centroid_lon
      FROM clusters
      WHERE cid IS NOT NULL
      GROUP BY strategy_type, cid
    )
    SELECT * FROM hulls
  `) as ClusterRow[];

  if (rows.length === 0) {
    return;
  }

  await db.insert(priorityZones).values(
    rows.map((row) => ({
      strategyType: row.strategy_type,
      requestCount: row.request_count,
      totalVotes: row.total_votes,
      centroidLat: row.centroid_lat,
      centroidLon: row.centroid_lon,
      geojson: row.geojson ? JSON.parse(row.geojson) : null,
      computedAt: new Date(),
    }))
  );
}
