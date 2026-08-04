import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/server/db";
import { publicRateLimitFailureResponse } from "@/lib/server/http/provider-response";
import { parseBoundingBox } from "@/lib/server/security/bbox";
import { enforcePublicProviderRateLimit } from "@/lib/server/security/public-provider-rate-limit";
import {
  activityGridToFeatureCollection,
  aggregateActivityGrid,
} from "@/lib/server/services/community-activity";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const MAX_FEATURES = 2_000;

/**
 * Requests per minute per caller. This endpoint is unauthenticated and each call is a full
 * grouped scan of strategy_requests, so an unlimited caller can both spend the database and
 * sweep the whole grid cell by cell; the whole-cell filter in aggregateActivityGrid stops that
 * sweep resolving a point, and this stops it being free.
 */
const ACTIVITY_GRID_REQUESTS_PER_MINUTE = 60;

/** Parses a bounded non-negative integer parameter; null means the caller sent a bad value. */
function parseBoundedInteger(
  value: string | null,
  fallback: number,
  minimum: number,
  maximum: number
): number | null {
  if (value === null) return fallback;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= minimum && parsed <= maximum
    ? parsed
    : null;
}

/**
 * Serves the community activity heatmap as a server-side aggregate.
 * See docs/data-ingestion-and-serving-contract.md §Location and freshness boundaries.
 */
export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;

  const bboxParam = params.get("bbox");
  const boundingBox = bboxParam ? parseBoundingBox(bboxParam) : undefined;
  if (bboxParam && !boundingBox) {
    return NextResponse.json(
      { error: "Invalid bbox. Expected ordered west,south,east,north" },
      { status: 400, headers: { "Cache-Control": "no-store" } }
    );
  }

  const zoom = Number(params.get("zoom") ?? 0);
  const limit = parseBoundedInteger(params.get("limit"), MAX_FEATURES, 1, MAX_FEATURES);
  const minimumVotes = parseBoundedInteger(params.get("min_votes"), 0, 0, 1_000_000);
  const minimumFeatureCount = parseBoundedInteger(
    params.get("min_features"),
    1,
    1,
    1_000_000
  );
  if (
    !Number.isFinite(zoom) ||
    zoom < 0 ||
    zoom > 22 ||
    limit === null ||
    minimumVotes === null ||
    minimumFeatureCount === null
  ) {
    return NextResponse.json(
      { error: "zoom, limit, min_votes and min_features must be bounded numbers" },
      { status: 400, headers: { "Cache-Control": "no-store" } }
    );
  }

  const rateLimit = await enforcePublicProviderRateLimit(
    request,
    "action-network",
    ACTIVITY_GRID_REQUESTS_PER_MINUTE
  );
  if (!rateLimit.allowed) return publicRateLimitFailureResponse(rateLimit);

  const grid = await aggregateActivityGrid(db, {
    boundingBox: boundingBox ?? undefined,
    zoom,
    limit,
    minimumVotes,
    minimumFeatureCount,
  });

  // An absent observed-at header is how a caller learns the aggregate is empty;
  // an empty string would read as a malformed timestamp.
  const headers = new Headers({
    "Content-Type": "application/geo+json",
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Data-Revision": `activity-grid-${grid.cellDegrees}`,
    "X-Data-Freshness": grid.observedAt ? "current" : "no-data",
  });
  if (grid.observedAt) {
    headers.set("X-Data-Observed-At", grid.observedAt.toISOString());
  }

  return new NextResponse(JSON.stringify(activityGridToFeatureCollection(grid)), {
    status: 200,
    headers,
  });
}
