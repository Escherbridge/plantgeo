import { NextResponse } from "next/server";
import { getPublishedFireDetections } from "@/lib/server/services/environmental-read-model";
import { firmsDayRange } from "@/lib/server/services/environmental-time";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** YYYY-MM-DD, the only shape the read model's day resolver accepts. */
const CALENDAR_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

/**
 * A past day's detections never change, so it may be cached hard; the live window rolls with
 * the FIRMS feed and may not. Both are public -- this collection is already published data.
 */
const LIVE_WINDOW_CACHE_CONTROL =
  "public, max-age=30, s-maxage=300, stale-while-revalidate=600";
const HISTORICAL_DAY_CACHE_CONTROL =
  "public, max-age=3600, s-maxage=86400, stale-while-revalidate=86400";

/**
 * Serves accepted fire observations without fetching an upstream provider.
 *
 * `?date=` is the time slider's day. Omitting it answers the live FIRMS lookback window, which
 * is what an absent day has always meant and what first paint still asks for.
 *
 * That parameter is the whole of the fix for a layer that was not on the slider at all. This
 * route hardcoded `firmsDayRange()` -- a window measured backwards from NOW -- so scrubbing
 * anywhere in the four-year axis left the same two days of detections sitting on the map, and
 * nothing said so. `getPublishedFireDetections` has taken a `date` since it was written; only
 * the caller was missing.
 *
 * A present-but-malformed day is a 400, never a silent fall-back to the live window: answering
 * `?date=2023-6-1` with today's detections renders August-2026 fires as June-2023 with nothing
 * in the body or headers saying the requested day was ignored.
 */
export async function GET(request: Request) {
  const requestedDate = new URL(request.url).searchParams.get("date")?.trim();
  if (requestedDate !== undefined && !CALENDAR_DATE_PATTERN.test(requestedDate)) {
    return NextResponse.json(
      {
        type: "FeatureCollection",
        features: [],
        availability: "unavailable",
        reason: "invalid_date_parameter",
        detail: "The date parameter must be a YYYY-MM-DD calendar day.",
      },
      {
        status: 400,
        headers: {
          "Cache-Control": "no-store",
          "X-Content-Type-Options": "nosniff",
        },
      }
    );
  }
  const date = requestedDate;

  try {
    const data = await getPublishedFireDetections(undefined, firmsDayRange(), date);
    const etag = `W/"fire-${date ?? "live"}-${data.features.length}"`;
    const dataRevision = `rev-fire-${date ?? "live"}-${data.features.length}`;

    const ifNoneMatch = request.headers.get("if-none-match");
    if (ifNoneMatch && (ifNoneMatch === etag || ifNoneMatch.includes(etag))) {
      return new NextResponse(null, {
        status: 304,
        headers: {
          ETag: etag,
          "x-data-revision": dataRevision,
          "Cache-Control":
            date === undefined ? LIVE_WINDOW_CACHE_CONTROL : HISTORICAL_DAY_CACHE_CONTROL,
        },
      });
    }

    return NextResponse.json(data, {
      headers: {
        ETag: etag,
        "x-data-revision": dataRevision,
        "Cache-Control":
          date === undefined ? LIVE_WINDOW_CACHE_CONTROL : HISTORICAL_DAY_CACHE_CONTROL,
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
