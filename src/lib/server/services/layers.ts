import type { PostgresJsDatabase } from "drizzle-orm/postgres-js";
import type * as schema from "@/lib/server/db/schema";
import { layers, features } from "@/lib/server/db/schema";

type Db = PostgresJsDatabase<typeof schema>;

interface LayerCreateData {
  name: string;
  type?: string;
  description?: string;
  style?: Record<string, unknown>;
  isPublic?: boolean;
}

interface FeatureData {
  properties: Record<string, unknown>;
}

export async function createLayer(db: Db, data: LayerCreateData) {
  const result = await db.insert(layers).values(data).returning();
  return result[0];
}

export async function addFeatures(db: Db, layerId: string, featureList: FeatureData[]) {
  if (featureList.length === 0) return [];
  const rows = featureList.map((f) => ({ layerId, properties: f.properties }));
  return db.insert(features).values(rows).returning();
}
