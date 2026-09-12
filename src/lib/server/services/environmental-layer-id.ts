import { eq } from "drizzle-orm";
import { db } from "@/lib/server/db";
import { layers } from "@/lib/server/db/schema";

const LAYER_ID_CACHE_TTL_MS = 60_000;
const LAYER_ID_CACHE_LIMIT = 200;
const layerIdByName = new Map<string, { id: string; expiresAt: number }>();

/** Resolve a control-plane layer name without coupling Parquet readers to the environmental read model. */
export async function resolveCachedLayerId(layerName: string): Promise<string | null> {
  const cached = layerIdByName.get(layerName);
  if (cached !== undefined && cached.expiresAt > Date.now()) return cached.id;
  const [row] = await db
    .select({ id: layers.id })
    .from(layers)
    .where(eq(layers.name, layerName))
    .limit(1);
  if (!row) {
    layerIdByName.delete(layerName);
    return null;
  }
  if (layerIdByName.size >= LAYER_ID_CACHE_LIMIT) layerIdByName.clear();
  layerIdByName.set(layerName, { id: row.id, expiresAt: Date.now() + LAYER_ID_CACHE_TTL_MS });
  return row.id;
}

/** Drop one memoized control-plane layer mapping. */
export function invalidateLayerId(layerName: string): void {
  layerIdByName.delete(layerName);
}

/** Drop all memoized control-plane layer mappings. */
export function clearLayerIdCache(): void {
  layerIdByName.clear();
}

