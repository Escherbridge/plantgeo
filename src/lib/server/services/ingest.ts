import { and, eq, inArray, sql } from "drizzle-orm";
import { db } from "@/lib/server/db";
import { features, layers } from "@/lib/server/db/schema";
import { publish } from "@/lib/server/services/realtime";

export interface IngestFeatureInput {
  layerId: string;
  featureId: string;
  properties: Record<string, unknown>;
  channel: string;
}

const INSERT_BATCH_SIZE = 100;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/** Resolves an operator-friendly layer name without weakening the UUID foreign key. */
async function resolveLayerId(layerReference: string): Promise<string> {
  const record = await db
    .select({ id: layers.id })
    .from(layers)
    .where(
      UUID_PATTERN.test(layerReference)
        ? eq(layers.id, layerReference)
        : eq(layers.name, layerReference)
    )
    .limit(1);

  if (!record[0]) {
    throw new Error(`Configured ingestion layer does not exist: ${layerReference}`);
  }

  return record[0].id;
}

async function ingestResolvedBatch(
  resolvedLayerId: string,
  inputs: IngestFeatureInput[]
): Promise<Array<{ id: string; input: IngestFeatureInput }>> {
  if (inputs.length === 0) return [];

  return db.transaction(async (tx) => {
    const inputById = new Map<string, IngestFeatureInput>();
    for (const input of inputs) inputById.set(input.featureId, input);
    const uniqueInputs = [...inputById.values()];
    const eventIds = [...inputById.keys()].sort();
    const lockValues = sql.join(
      eventIds.map((eventId) => sql`${`${resolvedLayerId}:${eventId}`}`),
      sql`, `
    );
    await tx.execute(sql`
      SELECT pg_advisory_xact_lock(hashtext(event_key))
      FROM unnest(ARRAY[${lockValues}]::text[]) AS locks(event_key)
      ORDER BY event_key
    `);

    const existing = await tx
      .select({ externalId: sql<string>`${features.properties} ->> 'id'` })
      .from(features)
      .where(
        and(
          eq(features.layerId, resolvedLayerId),
          inArray(sql<string>`${features.properties} ->> 'id'`, eventIds)
        )
      );
    const existingIds = new Set(existing.map((row) => row.externalId));
    const pending = uniqueInputs.filter(
      (input) => !existingIds.has(input.featureId)
    );
    const refreshable = uniqueInputs.filter((input) =>
      existingIds.has(input.featureId)
    );

    const written: Array<{ id: string; input: IngestFeatureInput }> = [];

    if (pending.length > 0) {
      const inserted = await tx
        .insert(features)
        .values(
          pending.map((input) => ({
            layerId: resolvedLayerId,
            properties: { ...input.properties, id: input.featureId },
          }))
        )
        .returning({ id: features.id, properties: features.properties });

      for (const row of inserted) {
        const properties = row.properties as Record<string, unknown>;
        const externalId = properties.id;
        const input =
          typeof externalId === "string" ? inputById.get(externalId) : undefined;
        if (input) written.push({ id: row.id, input });
      }
    }

    // Long-lived source records (notably fire perimeters, keyed on a stable
    // incident id) keep changing upstream after first sight. Refresh them in
    // place, but only when the payload actually differs so an unchanged upstream
    // does not churn rows or replay realtime invalidations.
    //
    // Change detection deliberately ignores "geometry": the geometry-sync
    // trigger (drizzle/0004) rewrites properties.geometry through ST_AsGeoJSON
    // and may add "geometry_repaired", so the stored copy never matches the raw
    // upstream text and a whole-payload comparison would rewrite every row on
    // every run. The scalar payload is a sound change signal for these feeds --
    // WFIGS advances polygonDateTime/percentContained whenever a perimeter is
    // redrawn -- and an accepted update still writes the new geometry through.
    for (const input of refreshable) {
      const nextProperties = { ...input.properties, id: input.featureId };
      const [updated] = await tx
        .update(features)
        .set({ properties: nextProperties, updatedAt: new Date() })
        .where(
          and(
            eq(features.layerId, resolvedLayerId),
            eq(sql<string>`${features.properties} ->> 'id'`, input.featureId),
            sql`(${features.properties} - 'geometry' - 'geometry_repaired')
                IS DISTINCT FROM (${nextProperties}::jsonb - 'geometry')`
          )
        )
        .returning({ id: features.id });
      if (updated) written.push({ id: updated.id, input });
    }

    return written;
  });
}

/** Writes a validated external feature, refreshing it if the payload changed. */
export async function ingestFeature(input: IngestFeatureInput): Promise<boolean> {
  return (await ingestFeatures([input])) === 1;
}

/**
 * Persists bounded batches before emitting best-effort realtime invalidations.
 * Returns the number of rows written -- inserted plus genuinely-changed refreshes.
 */
export async function ingestFeatures(inputs: IngestFeatureInput[]): Promise<number> {
  const byLayer = new Map<string, IngestFeatureInput[]>();
  for (const input of inputs) {
    if (
      input.layerId.length === 0 ||
      input.layerId.length > 100 ||
      input.featureId.trim().length === 0 ||
      input.featureId.length > 500 ||
      !/^layer:[a-z0-9-]{1,100}$/.test(input.channel)
    ) {
      throw new Error("Ingestion identity or channel is outside the bounded contract");
    }
    const layerInputs = byLayer.get(input.layerId) ?? [];
    layerInputs.push(input);
    byLayer.set(input.layerId, layerInputs);
  }

  let created = 0;
  for (const [layerReference, layerInputs] of byLayer) {
    const resolvedLayerId = await resolveLayerId(layerReference);
    for (let offset = 0; offset < layerInputs.length; offset += INSERT_BATCH_SIZE) {
      const inserted = await ingestResolvedBatch(
        resolvedLayerId,
        layerInputs.slice(offset, offset + INSERT_BATCH_SIZE)
      );
      created += inserted.length;
      await Promise.all(
        inserted.map(({ id, input }) =>
          publish(input.channel, {
            type: "Feature",
            id,
            properties: { ...input.properties, id: input.featureId },
            geometry: (input.properties.geometry as object) ?? null,
          })
        )
      );
    }
  }
  return created;
}
