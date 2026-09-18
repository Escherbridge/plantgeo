import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import {
  getBotanicalOccurrences,
  BotanicalOccurrencesContractError,
  BotanicalOccurrencesRequestError,
  BotanicalOccurrencesUnavailableError,
} from "@/lib/server/services/botanical-occurrences-client";
import {
  BOTANICAL_MAX_BBOX_SQUARE_DEGREES,
  BOTANICAL_OCCURRENCE_MAX_LIMIT,
  botanicalServingBandForViewport,
  botanicalServingZoomForBand,
} from "@/lib/botanical-occurrences";
import { PRIVATE_EPHEMERAL_HEADERS } from "@/lib/server/http/provider-response";
import type { BotanicalProxyAnswer } from "@/lib/environmental/botanical-proxy-contract";
import {
  UpstreamAbortedError,
  UpstreamConfigurationError,
  UpstreamHttpError,
  UpstreamPayloadError,
  UpstreamTimeoutError,
} from "@/lib/server/http/bounded-upstream";

/**
 * Browser-facing proxy for the governed `botanical-occurrences` plane.
 *
 * WHY A ROUTE AND NOT JUST THE tRPC PROCEDURE. `environmental.getBotanicalOccurrences` stays; this
 * is the plain-fetch surface the layer hook uses, so a map layer can issue one abortable request
 * per viewport without the query-client machinery. Both call the SAME server client, so neither can
 * reach the plane with inputs the other would have refused.
 *
 * THE UPSTREAM BASE URL NEVER REACHES THE BROWSER. `botanical-occurrences-client.ts` reads
 * `AGRI_PARQUET_SERVICE_URL` server-side; the older direct client in `lib/botanical-occurrences.ts`
 * reads a `NEXT_PUBLIC_` variable and talks to the service from the page. This route is the path
 * that does not require the plane to be publicly reachable.
 *
 * INGRESS BOUNDS ARE THE PLANE'S OWN. The bbox ceilings and the record limit are restated from
 * `planes/botanical_occurrences.py` via `lib/botanical-occurrences.ts` rather than re-chosen here.
 * Refusing at ingress saves a round trip; refusing DIFFERENTLY from the plane would mean this route
 * has an opinion about coverage, which it must not.
 *
 * THE RUNG IS SELECTED HERE, FROM ZOOM AND BBOX SIZE. Owner decision 2026-09-18: a viewport wider
 * than its zoom's own rung admits is served from the next rung OUT, not refused -- see
 * `botanicalServingBandForViewport`. This route is where that happens because the plane derives its
 * rung from `zoom` alone and takes no `support_id` parameter, so selecting a rung IS choosing which
 * zoom to forward. The chosen rung travels back in `servingRung` so a caption can say which
 * evidence the reader is looking at; above the coarse rung's ceiling the existing
 * `bbox_too_large_for_zoom` refusal is unchanged, because there is nothing coarser to fall back to.
 *
 * ONE ERROR SHAPE. Every non-200 answers `{ error, reason, detail? }` so a client branches on
 * `reason` and never on a status code alone: `invalid_request` and `bbox_too_large_for_zoom` are
 * both 400 but mean different things to a reader, and the five §4a pointer failures are all 503 and
 * are not interchangeable at all.
 */

export const dynamic = "force-dynamic";

/** Longest a browser waits before this route gives up on the plane; the client bounds its own read. */
const ROUTE_TIMEOUT_MS = 20_000;

/** `-180,-90,180,90` is 180 characters at full precision; 200 is slack, not a budget. */
const MAX_BBOX_CHARACTERS = 200;

/** MapLibre's own ceiling; nothing below the plane's coarse rung exists above it either. */
const MAX_ZOOM = 24;

const CALENDAR_DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

/** `west,south,east,north` in WGS84, with strictly-ordered ordinates. The plane parses the same. */
const bboxSchema = z
  .string()
  .trim()
  .max(MAX_BBOX_CHARACTERS)
  .superRefine((value, context) => {
    const ordinates = value.split(",").map((part) => Number(part));
    if (ordinates.length !== 4 || ordinates.some((ordinate) => !Number.isFinite(ordinate))) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'bbox must be four numbers, "west,south,east,north"',
      });
      return;
    }
    const [west, south, east, north] = ordinates;
    if (west < -180 || east > 180 || south < -90 || north > 90) {
      context.addIssue({ code: z.ZodIssueCode.custom, message: "bbox must lie inside WGS84 bounds" });
      return;
    }
    if (west >= east || south >= north) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "bbox must have min ordinates strictly below max ordinates",
      });
    }
  });

const querySchema = z.object({
  bbox: bboxSchema,
  // Coerced because a query string has no numbers; `.int()` after coercion still rejects "12.5",
  // which matters -- a fractional zoom would silently truncate into a different answer band.
  zoom: z.coerce.number().int().min(0).max(MAX_ZOOM),
  taxonConceptId: z.string().trim().min(1).max(200).optional(),
  family: z.string().trim().min(1).max(200).optional(),
  collectionKey: z.string().trim().min(1).max(200).optional(),
  eventStart: z.string().regex(CALENDAR_DAY_PATTERN).optional(),
  eventEnd: z.string().regex(CALENDAR_DAY_PATTERN).optional(),
  spatialQuality: z.enum(["confirmed", "possible", "all"]).optional(),
  limit: z.coerce.number().int().min(1).max(BOTANICAL_OCCURRENCE_MAX_LIMIT).optional(),
  cursor: z.string().trim().min(1).max(500).optional(),
});

function bboxSquareDegrees(bbox: string): number {
  const [west, south, east, north] = bbox.split(",").map((part) => Number(part));
  return (east - west) * (north - south);
}

/** The one error shape this route answers with. `detail` is present whenever there is more to say. */
function failure(status: number, error: string, reason: string, detail?: string): NextResponse {
  return NextResponse.json(
    detail === undefined ? { error, reason } : { error, reason, detail },
    { status, headers: PRIVATE_EPHEMERAL_HEADERS }
  );
}

export async function GET(request: NextRequest) {
  const parsed = querySchema.safeParse(Object.fromEntries(request.nextUrl.searchParams.entries()));
  if (!parsed.success) {
    return failure(
      400,
      "Invalid botanical-occurrences query",
      "invalid_request",
      parsed.error.issues.map((issue) => `${issue.path.join(".") || "query"}: ${issue.message}`).join("; ")
    );
  }

  const { bbox, zoom } = parsed.data;
  const servingRung = botanicalServingBandForViewport(zoom, bboxSquareDegrees(bbox));
  if (servingRung === null) {
    // Refused BEFORE the upstream call and before the pointer is resolved, exactly as the plane
    // refuses before opening a generation: a bound that depended on what is published would leak
    // what is published. Reached only above the COARSEST rung's ceiling now -- every narrower
    // refusal became a rung selection above.
    return failure(
      400,
      "Invalid botanical-occurrences query",
      "bbox_too_large_for_zoom",
      `no published rung answers a bbox wider than ${BOTANICAL_MAX_BBOX_SQUARE_DEGREES} square degrees`
    );
  }
  // The zoom that makes the plane answer from the rung selected above; the caller's own zoom
  // whenever it already selects it.
  const servingZoom = botanicalServingZoomForBand(servingRung, zoom);

  const timeout = AbortSignal.timeout(ROUTE_TIMEOUT_MS);
  // The caller's cancellation AND this route's ceiling: a client that navigates away must not hold
  // an upstream read open, and a wedged upstream must not hold the route open forever.
  const signal =
    request.signal === undefined ? timeout : AbortSignal.any([request.signal, timeout]);

  try {
    const result = await getBotanicalOccurrences({ ...parsed.data, zoom: servingZoom, signal });
    if (result.state === "refused") {
      return failure(400, "The botanical-occurrences plane refused this query", result.reason, result.detail);
    }
    if (result.state === "unavailable") {
      return failure(503, "The botanical-occurrences plane is unavailable", result.reason, result.note);
    }
    // The published contract, checked by the compiler rather than by hope: if the server client's
    // decoded shape ever drifts from `botanicalProxyAnswerSchema`, this assignment stops compiling
    // instead of the hook receiving a field it cannot find.
    // Spread per branch rather than once over the union: a discriminated union assembled by one
    // spread loses its discriminant to TypeScript, and this assignment exists to be checked.
    const body: BotanicalProxyAnswer =
      result.state === "detail" ? { ...result, servingRung } : { ...result, servingRung };
    return NextResponse.json(body, { headers: PRIVATE_EPHEMERAL_HEADERS });
  } catch (error) {
    if (error instanceof BotanicalOccurrencesUnavailableError) {
      // A §4a fail-closed pointer. 503 with the CLOSED reason, never an empty collection: "nothing
      // is published" and "the bytes under the pointer changed" must not arrive as the same answer.
      return failure(
        503,
        "The botanical-occurrences plane is unavailable",
        error.failure ?? "pointer_unresolved",
        error.message
      );
    }
    if (error instanceof BotanicalOccurrencesRequestError) {
      return failure(400, "Invalid botanical-occurrences query", "invalid_request", error.message);
    }
    if (error instanceof BotanicalOccurrencesContractError) {
      // 502, not 503: one side of a published contract is wrong and no retry will fix it until a
      // deploy does. A retryable status would have every client hammer a permanent mismatch.
      return failure(502, "The botanical-occurrences plane broke its contract", "contract_mismatch", error.message);
    }
    if (error instanceof UpstreamAbortedError) {
      // The CALLER walked away (a superseded viewport is the common case). 499 rather than a 5xx:
      // nothing upstream failed, and counting these as outages would misreport a panning user.
      return failure(499, "The botanical-occurrences request was cancelled", "request_cancelled");
    }
    if (error instanceof UpstreamTimeoutError) {
      return failure(504, "The botanical-occurrences plane did not answer in time", "upstream_timeout");
    }
    if (error instanceof UpstreamHttpError) {
      // The plane's own refused/unavailable answers arrive here, not in the branches above:
      // `fetchBoundedJson` throws on any non-2xx, so the 400/409/503 bodies the plane writes never
      // reach the decoder. The status is passed through for 4xx (a caller mistake stays a caller
      // mistake) and collapsed to 502 for anything else.
      const status = error.status >= 400 && error.status < 500 ? error.status : 502;
      return failure(status, "The botanical-occurrences plane refused this query", "plane_rejected", error.message);
    }
    if (error instanceof UpstreamPayloadError) {
      return failure(502, "The botanical-occurrences plane returned an invalid response", "upstream_payload");
    }
    if (error instanceof UpstreamConfigurationError) {
      return failure(503, "The botanical-occurrences plane is not configured", "upstream_not_configured");
    }
    return failure(502, "The botanical-occurrences plane could not be reached", "upstream_unreachable");
  }
}
