import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { ingestFeatures } from "@/lib/server/services/ingest";
import { publish } from "@/lib/server/services/realtime";

export const runtime = "nodejs";

const GeometrySchema = z.object({
  type: z.string(),
  coordinates: z.unknown(),
});

const FeatureSchema = z.object({
  type: z.literal("Feature"),
  id: z.union([z.string(), z.number()]).optional(),
  geometry: GeometrySchema.nullable(),
  properties: z.record(z.unknown()).nullable(),
});

const FeatureCollectionSchema = z.object({
  type: z.literal("FeatureCollection"),
  features: z.array(FeatureSchema),
});

const FIRMS_LAYER_ID = process.env.FIRMS_LAYER_ID ?? "fire-detections-layer";

export async function POST(request: NextRequest) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const parsed = FeatureCollectionSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Validation failed", details: parsed.error.flatten() },
      { status: 422 }
    );
  }

  const { features } = parsed.data;

  await ingestFeatures(
    features.map((feature) => ({
      layerId: FIRMS_LAYER_ID,
      featureId:
        feature.id !== undefined ? String(feature.id) : undefined,
      properties: {
        ...(feature.properties ?? {}),
        geometry: feature.geometry,
      },
      channel: "layer:fire-detections",
    }))
  );

  // Publish global alert so SSE subscribers receive the update
  await publish("alerts:global", {
    type: "fire-detections",
    count: features.length,
    timestamp: new Date().toISOString(),
  });

  return NextResponse.json({ ok: true, count: features.length }, { status: 201 });
}
