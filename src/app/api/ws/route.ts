import { NextRequest, NextResponse } from "next/server";
import { publishRequired } from "@/lib/server/services/realtime";
import {
  parseBoundedJson,
  authorizeTrackingIngressRequest,
} from "@/lib/server/security/ingress";
import { TrackingPositionUpdateSchema } from "@/lib/server/security/geojson";
import {
  DuplicateTrackingPositionError,
  persistVerifiedPosition,
  UnknownTrackingAssetError,
} from "@/lib/server/services/tracking";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const RESPONSE_HEADERS = { "Cache-Control": "no-store" } as const;
const MAX_CLOCK_SKEW_MS = 5 * 60 * 1_000;
const MAX_POSITION_AGE_MS = 24 * 60 * 60 * 1_000;

export async function POST(request: NextRequest) {
  const authorization = authorizeTrackingIngressRequest(request);
  if (!authorization.authorized) {
    return NextResponse.json(
      { error: authorization.error },
      { status: authorization.status, headers: RESPONSE_HEADERS }
    );
  }

  const jsonBody = await parseBoundedJson(request, 16 * 1024);
  if (!jsonBody.ok) {
    return NextResponse.json(
      { error: jsonBody.error },
      { status: jsonBody.status, headers: RESPONSE_HEADERS }
    );
  }

  const parsed = TrackingPositionUpdateSchema.safeParse(jsonBody.data);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Validation failed", details: parsed.error.flatten() },
      { status: 422, headers: RESPONSE_HEADERS }
    );
  }

  const update = parsed.data;
  const producerTimestamp = new Date(update.timestamp);
  const ageMs = Date.now() - producerTimestamp.getTime();
  if (ageMs < -MAX_CLOCK_SKEW_MS || ageMs > MAX_POSITION_AGE_MS) {
    return NextResponse.json(
      { error: "Producer timestamp is outside the accepted tracking window" },
      { status: 422, headers: RESPONSE_HEADERS }
    );
  }

  let persisted: { producerTimestamp: Date; receivedAt: Date };
  try {
    persisted = await persistVerifiedPosition({
      assetId: update.assetId,
      lat: update.lat,
      lon: update.lon,
      heading: update.heading,
      speed: update.speed,
      altitude: update.altitude,
      producerTimestamp,
    });
  } catch (error) {
    if (error instanceof UnknownTrackingAssetError) {
      return NextResponse.json(
        { error: "Tracking asset was not found" },
        { status: 404, headers: RESPONSE_HEADERS }
      );
    }
    if (error instanceof DuplicateTrackingPositionError) {
      return NextResponse.json(
        { error: "Tracking position was already accepted" },
        { status: 409, headers: RESPONSE_HEADERS }
      );
    }
    return NextResponse.json(
      { error: "Tracking position could not be persisted" },
      { status: 503, headers: { ...RESPONSE_HEADERS, "Retry-After": "10" } }
    );
  }

  const event = {
    assetId: update.assetId,
    lat: update.lat,
    lon: update.lon,
    heading: update.heading ?? null,
    speed: update.speed ?? null,
    altitude: update.altitude ?? null,
    producerTimestamp: persisted.producerTimestamp.toISOString(),
    receivedAt: persisted.receivedAt.toISOString(),
  };

  try {
    await publishRequired(`tracking:${update.assetId}`, event);
  } catch {
    return NextResponse.json(
      { ok: true, persisted: true, broadcast: false, ...event },
      { status: 202, headers: RESPONSE_HEADERS }
    );
  }

  return NextResponse.json(
    { ok: true, persisted: true, broadcast: true, ...event },
    { status: 201, headers: RESPONSE_HEADERS }
  );
}
