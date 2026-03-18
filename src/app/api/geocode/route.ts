import { NextRequest, NextResponse } from "next/server";
import { forwardGeocode, normalizeResults } from "@/lib/server/services/geocoding";

export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const q = params.get("q");
  if (!q) return NextResponse.json({ results: [] });

  const result = await forwardGeocode(q, {
    limit: Number(params.get("limit")) || 5,
    lang: params.get("lang") || undefined,
    lat: params.has("lat") ? Number(params.get("lat")) : undefined,
    lon: params.has("lon") ? Number(params.get("lon")) : undefined,
  });

  return NextResponse.json({ results: normalizeResults(result) });
}
