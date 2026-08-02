import { NextResponse } from "next/server";
import { getPublishedFireDetections } from "@/lib/server/services/environmental-read-model";
import { firmsDayRange } from "@/lib/server/services/environmental-time";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** Serves accepted fire observations without fetching an upstream provider. */
export async function GET() {
  try {
    const data = await getPublishedFireDetections(undefined, firmsDayRange());
    return NextResponse.json(data, {
      headers: {
        "Cache-Control": "public, max-age=30, s-maxage=300, stale-while-revalidate=600",
        "X-Fire-Data-Source": "platform-database",
        "X-Fire-Count": String(data.features.length),
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch {
    return NextResponse.json(
      {
        type: "FeatureCollection",
        features: [],
        availability: "unavailable",
        reason: "published_fire_observations_unavailable",
      },
      {
        status: 503,
        headers: {
          "Cache-Control": "no-store",
          "Retry-After": "30",
          "X-Content-Type-Options": "nosniff",
        },
      }
    );
  }
}
