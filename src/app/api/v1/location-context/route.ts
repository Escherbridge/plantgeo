import { NextRequest, NextResponse } from "next/server";
import {
  apiKeyAuthorizationErrorResponse,
  authorizeApiRequest,
} from "@/lib/server/middleware/api-auth";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const LOCATION_CONTEXT_SERVING_STATE: "inactive" | "runnable" = "inactive";
const LOCATION_CONTEXT_INACTIVE_MESSAGE =
  "Location context is inactive until a reviewed, partner-scoped publication is available";

export async function GET(request: NextRequest) {
  const authResult = await authorizeApiRequest(request, "read:context");
  if (!authResult.valid) return apiKeyAuthorizationErrorResponse(authResult);

  if (LOCATION_CONTEXT_SERVING_STATE === "inactive") {
    return NextResponse.json(
      {
        code: "LOCATION_CONTEXT_INACTIVE",
        error: LOCATION_CONTEXT_INACTIVE_MESSAGE,
        retryable: false,
      },
      {
        status: 503,
        headers: {
          "Cache-Control": "no-store",
          "X-Content-Type-Options": "nosniff",
        },
      }
    );
  }

  const latitudeValue = request.nextUrl.searchParams.get("lat");
  const longitudeValue = request.nextUrl.searchParams.get("lon");
  const latitude = Number(latitudeValue);
  const longitude = Number(longitudeValue);
  if (
    latitudeValue === null ||
    latitudeValue.trim() === "" ||
    longitudeValue === null ||
    longitudeValue.trim() === "" ||
    !Number.isFinite(latitude) ||
    latitude < -90 ||
    latitude > 90 ||
    !Number.isFinite(longitude) ||
    longitude < -180 ||
    longitude > 180
  ) {
    return NextResponse.json(
      { error: "lat and lon must be finite WGS84 coordinates" },
      { status: 400, headers: { "Cache-Control": "no-store" } }
    );
  }

  const now = new Date();
  return NextResponse.json(
    {
      lat: latitude,
      lon: longitude,
      fireRisk: null,
      droughtClass: null,
      soilProperties: null,
      ndviTileUrl: null,
      priorityZones: [],
      bbox: [
        Math.max(-180, longitude - 0.5),
        Math.max(-90, latitude - 0.5),
        Math.min(180, longitude + 0.5),
        Math.min(90, latitude + 0.5),
      ],
      availability: {
        fireRisk: "unavailable",
        drought: "unavailable",
        soil: "unavailable",
        vegetationTiles: "unavailable",
        communityPriorityZones: "inactive",
      },
      generatedAt: now.toISOString(),
      dataRevision: null,
    },
    {
      headers: {
        "Cache-Control": "private, max-age=30",
        "X-Content-Type-Options": "nosniff",
      },
    }
  );
}
