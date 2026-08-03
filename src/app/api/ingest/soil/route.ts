import { NextRequest, NextResponse } from "next/server";
import { authorizeIngressRequest } from "@/lib/server/security/ingress";
import {
  getSoilProperties,
  SoilEvidenceUnavailableError,
  SoilUpstreamUnavailableError,
} from "@/lib/server/services/soilgrids";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 300;

/** ISRIC throttles around 5 requests/minute per client; stay well under it. */
const REQUEST_SPACING_MS = 13_000;
const MAX_POINTS = 16;
const DEFAULT_POINTS = 4;

interface WarmedCell {
  lat: number;
  lon: number;
  status: "cached" | "no_data" | "upstream_unavailable";
}

function parseBoundingBox(value: string): [number, number, number, number] | null {
  const parts = value.split(",");
  if (parts.length !== 4 || parts.some((part) => part.trim() === "")) return null;

  const coordinates = parts.map(Number);
  if (!coordinates.every(Number.isFinite)) return null;

  const [west, south, east, north] = coordinates;
  if (
    west < -180 ||
    east > 180 ||
    south < -90 ||
    north > 90 ||
    west >= east ||
    south >= north
  ) {
    return null;
  }
  return [west, south, east, north];
}

/** Centers of an approximately square grid covering the bbox. */
function samplePoints(
  [west, south, east, north]: [number, number, number, number],
  count: number
): Array<{ lat: number; lon: number }> {
  const side = Math.max(1, Math.floor(Math.sqrt(count)));
  const points: Array<{ lat: number; lon: number }> = [];
  for (let column = 0; column < side; column++) {
    for (let row = 0; row < side; row++) {
      points.push({
        lon: west + ((east - west) * (column + 0.5)) / side,
        lat: south + ((north - south) * (row + 0.5)) / side,
      });
    }
  }
  return points;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * GET /api/ingest/soil?bbox=west,south,east,north[&points=N]
 * Warms the SoilGrids cache for a bounded region.
 *
 * Requests are issued serially and paced under ISRIC's rate limit, so this is
 * deliberately slow and small. A cell SoilGrids has no data for is recorded as
 * a verified coverage gap, not skipped, so it is not re-queried later.
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
  const area = parseBoundingBox(bbox);
  if (!area) {
    return NextResponse.json({ error: "Invalid bbox" }, { status: 422 });
  }
  if (area[2] - area[0] > 5 || area[3] - area[1] > 5) {
    return NextResponse.json(
      { error: "bbox exceeds the 5 degree ingestion limit" },
      { status: 422 }
    );
  }

  const requestedPoints = Number(searchParams.get("points") ?? DEFAULT_POINTS);
  if (!Number.isFinite(requestedPoints) || requestedPoints < 1) {
    return NextResponse.json({ error: "points must be a positive number" }, { status: 422 });
  }
  const points = samplePoints(area, Math.min(MAX_POINTS, Math.trunc(requestedPoints)));

  const warmed: WarmedCell[] = [];
  for (const [index, point] of points.entries()) {
    if (index > 0) await sleep(REQUEST_SPACING_MS);
    try {
      await getSoilProperties(point.lat, point.lon);
      warmed.push({ ...point, status: "cached" });
    } catch (err) {
      if (err instanceof SoilEvidenceUnavailableError) {
        warmed.push({ ...point, status: "no_data" });
        continue;
      }
      if (err instanceof SoilUpstreamUnavailableError) {
        // Throttled or upstream fault: stop rather than keep pushing requests.
        warmed.push({ ...point, status: "upstream_unavailable" });
        break;
      }
      const message = err instanceof Error ? err.message : "Unknown error";
      return NextResponse.json(
        { error: "Failed to warm soil cache", details: message, warmed },
        { status: 502 }
      );
    }
  }

  return NextResponse.json({
    ok: true,
    requested: points.length,
    cached: warmed.filter((cell) => cell.status === "cached").length,
    noData: warmed.filter((cell) => cell.status === "no_data").length,
    warmed,
  });
}
