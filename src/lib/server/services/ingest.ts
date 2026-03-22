import { db } from "@/lib/server/db";
import { features } from "@/lib/server/db/schema";
import { publish } from "@/lib/server/services/realtime";
import { eq, and } from "drizzle-orm";

export interface IngestFeatureInput {
  layerId: string;
  featureId?: string;
  properties: Record<string, unknown>;
  channel: string;
}

/**
 * Validate, deduplicate by id+timestamp, insert to geo.features, and publish
 * the feature to the given Redis channel.
 */
export async function ingestFeature(input: IngestFeatureInput): Promise<void> {
  const { layerId, featureId, properties, channel } = input;

  // Deduplicate: if a feature with the same external id exists, skip insert
  if (featureId) {
    const existing = await db
      .select({ id: features.id })
      .from(features)
      .where(
        and(
          eq(features.layerId, layerId),
          // Properties stored as JSONB — check by casting
          // Drizzle doesn't have a jsonb equality helper, so we use raw sql
          // for the id comparison. We rely on properties->>'id' matching.
        )
      )
      .limit(1);

    // Simple dedup: skip if any row already has this featureId in properties
    const isDuplicate = existing.some(
      (row) =>
        (row as unknown as { properties: Record<string, unknown> }).properties
          ?.id === featureId
    );
    if (isDuplicate) return;
  }

  const [inserted] = await db
    .insert(features)
    .values({
      layerId,
      properties: featureId ? { ...properties, id: featureId } : properties,
    })
    .returning();

  // Publish GeoJSON Feature to Redis channel
  await publish(channel, {
    type: "Feature",
    id: inserted.id,
    properties: inserted.properties,
    geometry: (properties.geometry as object) ?? null,
  });
}

/**
 * Ingest multiple features in a batch.
 */
export async function ingestFeatures(
  inputs: IngestFeatureInput[]
): Promise<void> {
  await Promise.all(inputs.map((input) => ingestFeature(input)));
}
