import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { ingestFeature } from "@/lib/server/services/ingest";

export const runtime = "nodejs";

const SensorReadingSchema = z.object({
  sensor_id: z.string().min(1),
  lat: z.number().min(-90).max(90),
  lon: z.number().min(-180).max(180),
  timestamp: z.string().datetime(),
  readings: z.record(z.unknown()),
});

const SENSORS_LAYER_ID = process.env.SENSORS_LAYER_ID ?? "sensors-layer";

export async function POST(request: NextRequest) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const parsed = SensorReadingSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Validation failed", details: parsed.error.flatten() },
      { status: 422 }
    );
  }

  const { sensor_id, lat, lon, timestamp, readings } = parsed.data;

  await ingestFeature({
    layerId: SENSORS_LAYER_ID,
    featureId: `${sensor_id}:${timestamp}`,
    properties: {
      sensor_id,
      timestamp,
      readings,
      geometry: {
        type: "Point",
        coordinates: [lon, lat],
      },
    },
    channel: "layer:sensors",
  });

  return NextResponse.json({ ok: true }, { status: 201 });
}
