import { NextRequest, NextResponse } from "next/server";
import { ingestFeatures } from "@/lib/server/services/ingest";
import {
  parseBoundedJson,
  authorizeIngressRequest,
} from "@/lib/server/security/ingress";
import { parseFeatureCollection } from "@/lib/server/security/geojson";

export const runtime = "nodejs";

const FIRES_LAYER_ID = process.env.FIRES_LAYER_ID ?? "fire-perimeters";

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

  const parsed = parseFeatureCollection(jsonBody.data);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Validation failed", details: parsed.error.flatten() },
      { status: 422 }
    );
  }

  const { features } = parsed.data;
  if (features.some((feature) => feature.id === undefined)) {
    return NextResponse.json(
      { error: "Every fire perimeter requires a stable source feature id" },
      { status: 422 }
    );
  }

  const createdCount = await ingestFeatures(
    features.map((feature) => ({
      layerId: FIRES_LAYER_ID,
      featureId: String(feature.id),
      properties: {
        ...(feature.properties ?? {}),
        geometry: feature.geometry,
      },
      channel: "layer:fire-perimeters",
    }))
  );

  return NextResponse.json({ ok: true, count: createdCount }, { status: 201 });
}
