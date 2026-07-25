import { NextRequest, NextResponse } from "next/server";
import { ingestFeatures } from "@/lib/server/services/ingest";
import { publish } from "@/lib/server/services/realtime";
import {
  parseBoundedJson,
  authorizeIngressRequest,
} from "@/lib/server/security/ingress";
import { parseNullableGeometryFeatureCollection } from "@/lib/server/security/geojson";
import {
  isFreshObservation,
  parseFirmsObservationTime,
} from "@/lib/server/services/environmental-time";

export const runtime = "nodejs";

const FIRMS_LAYER_ID = process.env.FIRMS_LAYER_ID ?? "fire-detections";

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

  const parsed = parseNullableGeometryFeatureCollection(jsonBody.data);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Validation failed", details: parsed.error.flatten() },
      { status: 422 }
    );
  }

  const { features } = parsed.data;
  if (features.some((feature) => feature.id === undefined)) {
    return NextResponse.json(
      { error: "Every FIRMS feature requires a stable source feature id" },
      { status: 422 }
    );
  }
  const normalized: Array<{
    feature: (typeof features)[number];
    properties: Record<string, unknown>;
    observedAt: string;
  }> = [];
  for (const feature of features) {
    const properties = feature.properties ?? {};
    const observedAt = parseFirmsObservationTime(properties);
    if (!observedAt || !isFreshObservation(observedAt, 10 * 24 * 60 * 60 * 1_000)) {
      return NextResponse.json(
        {
          error:
            "Every FIRMS feature requires a valid recent observedAt or acqDate/acqTime",
        },
        { status: 422 }
      );
    }
    normalized.push({ feature, properties, observedAt });
  }

  const createdCount = await ingestFeatures(
    normalized.map((item) => {
      const { feature, properties, observedAt } = item;
      return {
        layerId: FIRMS_LAYER_ID,
        featureId: String(feature.id),
        properties: {
          ...properties,
          observedAt,
          geometry: feature.geometry,
        },
        channel: "layer:fire-detections",
      };
    })
  );

  if (createdCount > 0) {
    await publish("alerts:global", {
      type: "fire-detections",
      count: createdCount,
      timestamp: new Date().toISOString(),
    });
  }

  return NextResponse.json({ ok: true, count: createdCount }, { status: 201 });
}
