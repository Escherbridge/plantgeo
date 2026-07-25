import { and, count, eq, gte, sql } from "drizzle-orm";
import type { PostgresJsDatabase } from "drizzle-orm/postgres-js";
import type * as schema from "@/lib/server/db/schema";
import { features, layers } from "@/lib/server/db/schema";

type Db = PostgresJsDatabase<typeof schema>;

export async function getFeatureCountByLayer(db: Db) {
  const result = await db
    .select({
      layerId: features.layerId,
      count: count(),
    })
    .from(features)
    .innerJoin(layers, eq(features.layerId, layers.id))
    .where(and(eq(layers.isPublic, true), eq(features.status, "published")))
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
        INNER JOIN geo.layers l ON l.id = f.layer_id
        WHERE l.is_public IS TRUE
          AND f.status = 'published'
          AND f.geom IS NOT NULL
          AND f.geom && ST_MakeEnvelope(
            ${bbox.minLng}, ${bbox.minLat}, ${bbox.maxLng}, ${bbox.maxLat}, 4326
          )
          AND ST_Intersects(
            f.geom,
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
    .innerJoin(layers, eq(features.layerId, layers.id))
    .where(
      and(
        gte(features.createdAt, since),
        eq(layers.isPublic, true),
        eq(features.status, "published")
      )
    )
    .groupBy(features.layerId);
  return result;
}

export async function getSystemStats(db: Db) {
  const [layerResult, featureResult] = await Promise.all([
    db.select({ count: count() }).from(layers).where(eq(layers.isPublic, true)),
    db
      .select({ count: count() })
      .from(features)
      .innerJoin(layers, eq(features.layerId, layers.id))
      .where(and(eq(layers.isPublic, true), eq(features.status, "published"))),
  ]);
  return {
    layerCount: layerResult[0]?.count ?? 0,
    featureCount: featureResult[0]?.count ?? 0,
    activeStreams: null,
  };
}
