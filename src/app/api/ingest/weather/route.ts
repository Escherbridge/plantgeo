import { NextRequest, NextResponse } from "next/server";
import { getCurrentWeather } from "@/lib/server/services/weather";
import { authorizeIngressRequest } from "@/lib/server/security/ingress";
import { ingestFeature } from "@/lib/server/services/ingest";

export const runtime = "nodejs";
const WEATHER_LAYER_ID = process.env.WEATHER_LAYER_ID ?? "weather-observations";

function parseBoundingBox(value: string): [number, number, number, number] | null {
  const parts = value.split(",");
  if (parts.length !== 4 || parts.some((part) => part.trim() === "")) return null;

  const coordinates = parts.map(Number);
  if (!coordinates.every(Number.isFinite)) return null;

  const [west, south, east, north] = coordinates;
  if (
    west < -180 ||
    west > 180 ||
    east < -180 ||
    east > 180 ||
    south < -90 ||
    south > 90 ||
    north < -90 ||
    north > 90 ||
    west >= east ||
    south >= north
  ) {
    return null;
  }

  return [west, south, east, north];
}

/**
 * GET /api/ingest/weather?bbox=west,south,east,north
 * Fetches and persists current weather for the center of a bounded region.
 */
export async function GET(request: NextRequest) {
  const authorization = authorizeIngressRequest(request);
  if (!authorization.authorized) {
    return NextResponse.json(
      { error: authorization.error },
      { status: authorization.status }
    );
  }

  const { searchParams } = new URL(request.url);
  const bbox = searchParams.get("bbox");

  if (!bbox) {
    return NextResponse.json({ error: "bbox is required" }, { status: 422 });
  }
  const parsedBoundingBox = parseBoundingBox(bbox);
  if (!parsedBoundingBox) {
    return NextResponse.json({ error: "Invalid bbox" }, { status: 422 });
  }
  const [west, south, east, north] = parsedBoundingBox;
  if (east - west > 5 || north - south > 5) {
    return NextResponse.json({ error: "bbox exceeds the 5 degree ingestion limit" }, { status: 422 });
  }
  const lat = (south + north) / 2;
  const lon = (west + east) / 2;

  try {
    const weather = await getCurrentWeather(lat, lon);
    const created = await ingestFeature({
      layerId: WEATHER_LAYER_ID,
      featureId: `${lat.toFixed(4)}:${lon.toFixed(4)}:${weather.observedAt}`,
      properties: {
        ...weather,
        source: "Open-Meteo",
        geometry: { type: "Point", coordinates: [lon, lat] },
      },
      channel: "layer:weather-observations",
    });
    return NextResponse.json({
      ok: true,
      created,
      observation: { lat, lon, ...weather },
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return NextResponse.json(
      { error: "Failed to fetch weather", details: message },
      { status: 502 }
    );
  }
}
