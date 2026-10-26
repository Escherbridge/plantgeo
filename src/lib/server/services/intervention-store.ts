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
  // `lifecycleStage` is moderator-owned (see `routers/interventions.ts` lifecycle transitions); ingress never sets it.
  const { lifecycleStage: _ignoredStage, ...incoming } = input.properties;
  const ingressProperties = { ...incoming, id: input.featureId };

  const written = await db.transaction(async (tx) => {
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
      .limit(1)
      // Row lock: a moderator's lifecycle move waits for (or is seen by) this re-ingest.
      .for("update");

    if (!existing) {
      const [inserted] = await tx
        .insert(features)
        .values({ layerId, properties: ingressProperties })
        .returning({ id: features.id });
      return inserted ? { id: inserted.id, properties: ingressProperties } : null;
    }

    // A re-ingest replaces partner fields but must not reset a moderator's lifecycle move.
    const storedStage = (existing.properties as Record<string, unknown> | null)?.lifecycleStage;
    const properties =
      storedStage === undefined ? ingressProperties : { ...ingressProperties, lifecycleStage: storedStage };

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
    return updated ? { id: updated.id, properties } : null;
  });

  if (!written) return false;
  await publish("layer:interventions", {
    type: "Feature",
    id: written.id,
    properties: written.properties,
    geometry: (input.properties.geometry as object) ?? null,
  });
  return true;
}
