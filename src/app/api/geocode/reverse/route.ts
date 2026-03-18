import { NextRequest, NextResponse } from "next/server";
import { reverseGeocode, normalizeResults } from "@/lib/server/services/geocoding";

export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const lat = params.get("lat");
  const lon = params.get("lon");
  if (!lat || !lon) return NextResponse.json({ results: [] });

  const result = await reverseGeocode(Number(lat), Number(lon), {
    limit: Number(params.get("limit")) || 1,
  });

  return NextResponse.json({ results: normalizeResults(result) });
}
