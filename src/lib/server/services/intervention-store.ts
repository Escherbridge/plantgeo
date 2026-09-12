import { and, eq, sql } from "drizzle-orm";
import { db } from "@/lib/server/db";
import { features, layers } from "@/lib/server/db/schema";
import { publish } from "@/lib/server/services/realtime";

export interface InterventionFeatureInput {
  layerId: string;
  featureId: string;
  properties: Record<string, unknown>;
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

async function resolveInterventionLayerId(layerReference: string): Promise<string> {
  const record = await db
    .select({ id: layers.id })
    .from(layers)
    .where(
      UUID_PATTERN.test(layerReference)
        ? eq(layers.id, layerReference)
        : eq(layers.name, layerReference)
    )
    .limit(1);

  if (!record[0]) throw new Error(`Configured intervention layer does not exist: ${layerReference}`);
  return record[0].id;
}

/** Stores one user-authored intervention and emits its realtime invalidation. */
export async function upsertInterventionFeature(input: InterventionFeatureInput): Promise<boolean> {
  const layerId = await resolveInterventionLayerId(input.layerId);
  const properties = { ...input.properties, id: input.featureId };

  const writtenId = await db.transaction(async (tx) => {
    await tx.execute(sql`SELECT pg_advisory_xact_lock(hashtext(${`${layerId}:${input.featureId}`}))`);

    const [existing] = await tx
      .select({ id: features.id, properties: features.properties })
      .from(features)
      .where(
        and(
          eq(features.layerId, layerId),
          eq(sql<string>`${features.properties} ->> 'id'`, input.featureId)
        )
      )
      .limit(1);

    if (!existing) {
      const [inserted] = await tx
        .insert(features)
        .values({ layerId, properties })
        .returning({ id: features.id });
      return inserted?.id ?? null;
    }

    const [updated] = await tx
      .update(features)
      .set({ properties, updatedAt: new Date() })
      .where(
        and(
          eq(features.id, existing.id),
          sql`(${features.properties} - 'geometry' - 'geometry_repaired')
              IS DISTINCT FROM (${JSON.stringify(properties)}::jsonb - 'geometry')`
        )
      )
      .returning({ id: features.id });
    return updated?.id ?? null;
  });

  if (!writtenId) return false;
  await publish("layer:interventions", {
    type: "Feature",
    id: writtenId,
    properties,
    geometry: (input.properties.geometry as object) ?? null,
  });
  return true;
}
