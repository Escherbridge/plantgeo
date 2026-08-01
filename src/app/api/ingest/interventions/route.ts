import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { ingestFeature } from "@/lib/server/services/ingest";
import {
  parseBoundedJson,
  authorizeIngressRequest,
} from "@/lib/server/security/ingress";
import { InterventionGeometrySchema } from "@/lib/server/services/intervention-geometry";

export const runtime = "nodejs";

const INTERVENTIONS_LAYER_ID =
  process.env.INTERVENTIONS_LAYER_ID ?? "interventions";

// Mirrors InterventionType in src/lib/environmental/intervention.ts.
const InterventionTypeSchema = z.enum([
  "reforestation",
  "silvopasture",
  "cover_cropping",
  "biochar",
  "keyline",
]);

const InterventionIngestSchema = z
  .object({
    id: z.string().trim().min(1).max(256),
    geometry: InterventionGeometrySchema,
    properties: z
      .object({
        name: z.string().trim().min(1).max(256),
        type: InterventionTypeSchema,
        status: z.string().trim().min(1).max(64),
      })
      .passthrough(),
  })
  .strict();

export async function POST(request: NextRequest) {
  const authorization = authorizeIngressRequest(request);
  if (!authorization.authorized) {
    return NextResponse.json(
      { error: authorization.error },
      { status: authorization.status }
    );
  }

  const jsonBody = await parseBoundedJson(request);
  if (!jsonBody.ok) {
    return NextResponse.json({ error: jsonBody.error }, { status: jsonBody.status });
  }

  const parsed = InterventionIngestSchema.safeParse(jsonBody.data);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Validation failed", details: parsed.error.flatten() },
      { status: 422 }
    );
  }

  const { id, geometry, properties } = parsed.data;

  const created = await ingestFeature({
    layerId: INTERVENTIONS_LAYER_ID,
    featureId: id,
    properties: {
      ...properties,
      geometry,
    },
    channel: "layer:interventions",
  });

  return NextResponse.json({ ok: true, count: created ? 1 : 0 }, { status: 201 });
}
