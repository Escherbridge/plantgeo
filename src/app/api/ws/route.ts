import { NextRequest, NextResponse } from "next/server";
import { publish } from "@/lib/server/services/realtime";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface PositionUpdate {
  assetId: string;
  lat: number;
  lon: number;
  heading?: number;
  speed?: number;
  altitude?: number;
  timestamp: string;
}

/**
 * WebSocket upgrade handler for vehicle/asset tracking.
 *
 * Next.js App Router does not natively support WebSocket upgrades via route
 * handlers, so this endpoint accepts POST requests carrying position updates
 * as a fallback transport, while a custom server (e.g. ws library) handles
 * true WebSocket upgrades at the infrastructure layer.
 *
 * Each message is validated and broadcast to the Redis tracking channel so
 * all connected SSE/WS consumers receive the update.
 */
export async function POST(request: NextRequest) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const update = body as Partial<PositionUpdate>;

  if (
    typeof update.assetId !== "string" ||
    typeof update.lat !== "number" ||
    typeof update.lon !== "number" ||
    typeof update.timestamp !== "string"
  ) {
    return NextResponse.json(
      { error: "Missing required fields: assetId, lat, lon, timestamp" },
      { status: 400 }
    );
  }

  const channel = `tracking:${update.assetId}`;

  await publish(channel, {
    assetId: update.assetId,
    lat: update.lat,
    lon: update.lon,
    heading: update.heading ?? null,
    speed: update.speed ?? null,
    altitude: update.altitude ?? null,
    timestamp: update.timestamp,
  });

  return NextResponse.json({ ok: true });
}
