import { sql } from "drizzle-orm";
import type { PostgresJsDatabase } from "drizzle-orm/postgres-js";
import type * as schema from "@/lib/server/db/schema";
import { features, layers } from "@/lib/server/db/schema";
import { count, gte } from "drizzle-orm";

type Db = PostgresJsDatabase<typeof schema>;

export async function getFeatureCountByLayer(db: Db) {
  const result = await db
    .select({
      layerId: features.layerId,
      count: count(),
    })
    .from(features)
    .groupBy(features.layerId);
  return result;
}

export async function getFeatureDensity(
  db: Db,
  bbox: { minLng: number; minLat: number; maxLng: number; maxLat: number }
) {
  const result = await db.execute(
    sql`SELECT COUNT(*) AS count
        FROM geo.features f
        WHERE ST_Within(
          (f.properties->>'geometry')::geometry,
          ST_MakeEnvelope(${bbox.minLng}, ${bbox.minLat}, ${bbox.maxLng}, ${bbox.maxLat}, 4326)
        )`
  );
  const rows = (result as unknown as { rows: { count: string }[] }).rows ?? result as unknown as { count: string }[];
  return Number(rows[0]?.count ?? 0);
}

export async function getRecentActivity(db: Db, hours = 24) {
  const since = new Date(Date.now() - hours * 60 * 60 * 1000);
  const result = await db
    .select({
      layerId: features.layerId,
      count: count(),
    })
    .from(features)
    .where(gte(features.createdAt, since))
    .groupBy(features.layerId);
  return result;
}

export async function getSystemStats(db: Db) {
  const [layerResult, featureResult] = await Promise.all([
    db.select({ count: count() }).from(layers),
    db.select({ count: count() }).from(features),
  ]);
  return {
    layerCount: layerResult[0]?.count ?? 0,
    featureCount: featureResult[0]?.count ?? 0,
    activeStreams: 0,
  };
}
