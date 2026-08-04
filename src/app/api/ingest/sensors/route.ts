import { NextRequest, NextResponse } from "next/server";
import { ingestFeature } from "@/lib/server/services/ingest";
import {
  parseBoundedJson,
  authorizeIngressRequest,
} from "@/lib/server/security/ingress";
import { SensorReadingSchema } from "@/lib/server/security/geojson";

export const runtime = "nodejs";

const SENSORS_LAYER_ID = process.env.SENSORS_LAYER_ID ?? "sensors";

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

  const parsed = SensorReadingSchema.safeParse(jsonBody.data);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Validation failed", details: parsed.error.flatten() },
      { status: 422 }
    );
  }

  const { sensor_id, lat, lon, timestamp, readings } = parsed.data;

  const created = await ingestFeature({
    layerId: SENSORS_LAYER_ID,
    featureId: `${sensor_id}:${timestamp}`,
    properties: {
      sensor_id,
      timestamp,
      // Same instant under the key environmental-read-model.ts dates rows by; `timestamp` is
      // not in its COALESCE list, so a row without this is invisible to the time axis. Kept
      // byte-identical to the Python producer's payload so a pushed and a pulled reading for
      // the same observation do not churn each other on refresh.
      observedAt: timestamp,
      readings,
      geometry: {
        type: "Point",
        coordinates: [lon, lat],
      },
    },
    channel: "layer:sensors",
  });

  return NextResponse.json({ ok: true, count: created ? 1 : 0 }, { status: 201 });
}
