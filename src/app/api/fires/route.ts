import { NextResponse } from "next/server";
import { createHash } from "crypto";
import {
  getPublishedFireDetections,
  serverCurrentDate,
} from "@/lib/server/services/environmental-read-model";
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
 * is what an absent day has always meant and what first paint still asks for. An explicit date
 * equal to the server's own today answers the identical window -- `getPublishedFireDetections`'s
 * own day resolver already collapses the two into the same `{ kind: "live" }` branch, which is
 * what every other slider-day reader in this codebase relies on to keep `undefined` and "today"
 * interchangeable (see `useDebouncedLayerDay` in `lib/map/layer-toggle-context.ts`). This route
 * now honours that collapse for its CACHE headers too, not only for the query -- see
 * `isLiveWindow` below -- so a caller is free to use either convention and gets identical bytes
 * AND identical cache behaviour either way.
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
 *
 * Two disclosed, one-time public-cache consequences of the ETag/cache-control rework below:
 * every ETag's SHAPE changed (count-based -> content-fingerprint-based), so the first request
 * per URL x edge PoP after this deploys is a full 200 even for a day whose content is unchanged
 * -- self-healing, not a standing cost. And a caller that starts sending `?date=<today>`
 * explicitly (rather than omitting it) now gets the LIVE cache lifetime, ~288x shorter than the
 * HISTORICAL one it would have gotten before (`s-maxage` 300 vs 86400) -- correct per
 * `isLiveWindow` below, but a real drop in edge TTL for that specific URL shape.
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
    // Read ONCE and threaded through, rather than a second, independent `serverCurrentDate()`
    // call after the `await` below: `getPublishedFireDetections` also reads "today" internally
    // (to resolve `date` against it), and two separate clock reads straddling a DB round trip
    // can disagree if a day boundary lands mid-request -- see the `today` param doc on that
    // function. That would have shipped a live, minutes-old payload under the HISTORICAL cache
    // lifetime (up to 24h stale-while-revalidate) under a URL whose meaning is one calendar day.
    const today = serverCurrentDate();
    const data = await getPublishedFireDetections(undefined, firmsDayRange(), date, today);

    // The one place this route decides "is this the live FIRMS window" for caching purposes --
    // and it has to be this RESOLVED fact, not the raw presence of `?date=`. Before this, a
    // caller that sent today's own date explicitly (any future one, sight unseen -- the
    // offline-sync lane is a candidate) would have its rolling, minutes-old window cached under
    // the HISTORICAL lifetime instead of the LIVE one, even though `getPublishedFireDetections`
    // answers both with byte-identical data. `revisionKey` is computed from the day check
    // directly (not from a separate boolean) so its else-branch narrows `date` to a real string
    // -- "live" can never collide with it, since `date` here is either undefined or already
    // validated against CALENDAR_DATE_PATTERN.
    const revisionKey = date === undefined || date === today ? "live" : date;
    const isLiveWindow = revisionKey === "live";
    const cacheControl = isLiveWindow ? LIVE_WINDOW_CACHE_CONTROL : HISTORICAL_DAY_CACHE_CONTROL;

    // Serialized ONCE and reused for both the fingerprint and (on a 200) the response body,
    // rather than `JSON.stringify` for hashing and a second, independent stringify inside
    // `NextResponse.json(data)` for the wire. Both queries now carry a deterministic tiebreaker
    // (`environmental-read-model.ts`), so identical underlying rows always serialize identically
    // -- this is a CONTENT fingerprint, not the feature count: a corrective re-ingest that
    // preserves row count (a geometry fix, a confidence re-grade, a replaced FIRMS batch) must
    // still change it, or the conditional check below 304s forever.
    const serializedBody = JSON.stringify(data);
    const fingerprint = createHash("sha1").update(serializedBody).digest("hex").slice(0, 16);
    const etag = `W/"fire-${revisionKey}-${fingerprint}"`;
    const dataRevision = `rev-fire-${revisionKey}-${fingerprint}`;

    const ifNoneMatch = request.headers.get("if-none-match");
    if (ifNoneMatch && (ifNoneMatch === etag || ifNoneMatch.includes(etag))) {
      return new NextResponse(null, {
        status: 304,
        headers: {
          ETag: etag,
          "x-data-revision": dataRevision,
          "Cache-Control": cacheControl,
        },
      });
    }

    return new NextResponse(serializedBody, {
      headers: {
        "Content-Type": "application/json",
        ETag: etag,
        "x-data-revision": dataRevision,
        "Cache-Control": cacheControl,
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
