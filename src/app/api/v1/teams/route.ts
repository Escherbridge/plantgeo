import { NextRequest, NextResponse } from "next/server";
import { validateApiKey, checkRateLimit } from "@/lib/server/middleware/api-auth";
import { db } from "@/lib/server/db";
import { teams } from "@/lib/server/db/schema";
import { sql } from "drizzle-orm";

export async function GET(request: NextRequest) {
  // Validate API key
  const authResult = await validateApiKey(request);
  if (!authResult.valid) {
    return NextResponse.json(
      { error: authResult.error ?? "Unauthorized" },
      { status: 401 }
    );
  }

  // Rate limit check
  const { limited, retryAfter } = await checkRateLimit(
    authResult.keyId!,
    authResult.rateLimit ?? 100
  );
  if (limited) {
    return NextResponse.json(
      { error: "Rate limit exceeded" },
      {
        status: 429,
        headers: retryAfter ? { "Retry-After": String(retryAfter) } : {},
      }
    );
  }

  // Parse query params
  const params = request.nextUrl.searchParams;
  const latStr = params.get("lat");
  const lonStr = params.get("lon");
  const strategy = params.get("strategy");
  const radiusKm = Math.min(Number(params.get("radius") || "50"), 500);

  if (!latStr || !lonStr) {
    return NextResponse.json(
      { error: "Missing required query parameters: lat, lon" },
      { status: 400 }
    );
  }

  const lat = Number(latStr);
  const lon = Number(lonStr);

  if (isNaN(lat) || isNaN(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180) {
    return NextResponse.json(
      { error: "Invalid lat/lon values" },
      { status: 400 }
    );
  }

  // Convert radius in km to approximate degree offset (1 degree lat ≈ 111 km)
  const degOffset = radiusKm / 111;

  // Query teams within bounding box; filter by specialty if provided
  const rows = await db
    .select({
      id: teams.id,
      name: teams.name,
      slug: teams.slug,
      description: teams.description,
      orgType: teams.orgType,
      specialties: teams.specialties,
      isVerified: teams.isVerified,
      website: teams.website,
      serviceArea: teams.serviceArea,
    })
    .from(teams)
    .where(
      strategy
        ? sql`
            ${teams.specialties} @> ${JSON.stringify([strategy])}::jsonb
          `
        : undefined
    )
    .limit(100);

  // Post-filter by distance using service area centroid or fallback bbox
  // Since serviceArea is stored as GeoJSON, we do a simple bbox check here.
  // Teams without a serviceArea are included regardless of location.
  const filtered = rows.filter((team) => {
    if (!team.serviceArea) return true;
    const sa = team.serviceArea as { bbox?: number[] } | null;
    if (!sa?.bbox || sa.bbox.length < 4) return true;
    const [minLon, minLat, maxLon, maxLat] = sa.bbox as number[];
    // Check if our point falls within the service area bbox
    return lon >= minLon && lon <= maxLon && lat >= minLat && lat <= maxLat;
  });

  // Build GeoJSON FeatureCollection
  const featureCollection = {
    type: "FeatureCollection" as const,
    features: filtered.map((team) => ({
      type: "Feature" as const,
      geometry: null,
      properties: {
        teamId: team.id,
        teamName: team.name,
        slug: team.slug,
        description: team.description,
        orgType: team.orgType,
        specialties: team.specialties ?? [],
        verified: team.isVerified,
        contactUrl: team.website ?? null,
      },
    })),
    numberReturned: filtered.length,
  };

  return NextResponse.json(featureCollection);
}
