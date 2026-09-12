import { count, eq, sql } from "drizzle-orm";
import type { PostgresJsDatabase } from "drizzle-orm/postgres-js";
import type * as schema from "@/lib/server/db/schema";
import { layers } from "@/lib/server/db/schema";

type Db = PostgresJsDatabase<typeof schema>;

function toCount(value: unknown): number {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

/** Count public user-authored interventions; environmental products are not relational. */
export async function getFeatureCountByLayer(db: Db) {
  const rows = await db.execute<{ layer_id: string; published_count: string | number }>(sql`
    SELECT l.id AS layer_id, count(f.id) AS published_count
    FROM geo.layers l
    JOIN geo.features f ON f.layer_id = l.id AND f.status = 'published'
    WHERE l.is_public IS TRUE AND l.name = 'interventions'
    GROUP BY l.id
    ORDER BY l.id
  `);
  return rows.map((row) => ({ layerId: row.layer_id, count: toCount(row.published_count) }));
}

export async function getLayerFeatureStats(db: Db) {
  const rows = await db.execute<{
    layer_id: string;
    layer_name: string | null;
    published_count: string | number;
  }>(sql`
    SELECT l.id AS layer_id, l.name AS layer_name, count(f.id) AS published_count
    FROM geo.layers l
    JOIN geo.features f ON f.layer_id = l.id AND f.status = 'published'
    WHERE l.is_public IS TRUE AND l.name = 'interventions'
    GROUP BY l.id, l.name
    ORDER BY l.id
  `);
  return rows.map((row) => ({
    layerId: row.layer_id,
    layerName: row.layer_name ?? "Unknown",
    count: toCount(row.published_count),
  }));
}

export async function getRecentActivity(db: Db, hours = 24) {
  const rows = await db.execute<{ layer_id: string; feature_count: string | number }>(sql`
    SELECT l.id AS layer_id, count(f.id) AS feature_count
    FROM geo.layers l
    JOIN geo.features f ON f.layer_id = l.id
    WHERE l.is_public IS TRUE
      AND l.name = 'interventions'
      AND f.created_at >= now() - (${hours}::integer * interval '1 hour')
    GROUP BY l.id
    ORDER BY l.id
  `);
  return rows.map((row) => ({ layerId: row.layer_id, count: toCount(row.feature_count) }));
}

export async function getSystemStats(db: Db) {
  const [layerResult, featureResult] = await Promise.all([
    db.select({ count: count() }).from(layers).where(eq(layers.isPublic, true)),
    db.execute<{ feature_count: string | number | null }>(sql`
      SELECT count(f.id) AS feature_count
      FROM geo.layers l
      JOIN geo.features f ON f.layer_id = l.id AND f.status = 'published'
      WHERE l.is_public IS TRUE AND l.name = 'interventions'
    `),
  ]);
  return {
    layerCount: layerResult[0]?.count ?? 0,
    featureCount: toCount(featureResult[0]?.feature_count),
    activeStreams: null,
  };
}
