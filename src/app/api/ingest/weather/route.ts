import { NextRequest, NextResponse } from "next/server";
import { getCurrentWeather } from "@/lib/server/services/weather";

export const runtime = "nodejs";

/**
 * GET /api/ingest/weather?bbox=west,south,east,north
 * Fetches current weather for the center of the provided bbox (or a default location).
 * Returns typed weather JSON from Open-Meteo.
 */
export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const bbox = searchParams.get("bbox");

  let lat = 37.7749;
  let lon = -122.4194;

  if (bbox) {
    const parts = bbox.split(",").map(Number);
    if (parts.length === 4 && parts.every((n) => !isNaN(n))) {
      const [west, south, east, north] = parts;
      lat = (south + north) / 2;
      lon = (west + east) / 2;
    }
  }

  try {
    const weather = await getCurrentWeather(lat, lon);
    return NextResponse.json({ ok: true, lat, lon, weather });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return NextResponse.json(
      { error: "Failed to fetch weather", details: message },
      { status: 502 }
    );
  }
}
