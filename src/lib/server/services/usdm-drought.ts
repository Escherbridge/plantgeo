import { z } from "zod";
import {
  fetchBoundedJson,
  UpstreamHttpError,
  UpstreamPayloadError,
} from "@/lib/server/http/bounded-upstream";

/** US Drought Monitor D0-D4 severity classes, indexed by the upstream DM code. */
export const DROUGHT_CATEGORY_LABELS = [
  "D0 — Abnormally Dry",
  "D1 — Moderate Drought",
  "D2 — Severe Drought",
  "D3 — Extreme Drought",
  "D4 — Exceptional Drought",
] as const;

export interface UsdmDroughtArea {
  dmCategory: number;
  geometry: { type: "MultiPolygon"; coordinates: number[][][][] };
}

export interface UsdmDroughtRelease {
  validDate: string;
  sourceUrl: string;
  areas: UsdmDroughtArea[];
}

const BASE_URL = "https://droughtmonitor.unl.edu/data/json";
/** The national collection is ~19 MB of full-resolution MultiPolygon rings. */
const MAX_RESPONSE_BYTES = 48 * 1024 * 1024;
const REQUEST_TIMEOUT_MS = 60_000;
/** USDM publishes Thursdays for the preceding Tuesday; two misses is a real outage. */
const DEFAULT_CANDIDATE_WEEKS = 3;
const MAX_CANDIDATE_WEEKS = 8;
const TUESDAY = 2;

const RingSchema = z.array(z.array(z.number().finite()).min(2).max(3));

const UsdmFeatureSchema = z.object({
  type: z.literal("Feature"),
  geometry: z.object({
    type: z.literal("MultiPolygon"),
    coordinates: z.array(z.array(RingSchema)),
  }),
  properties: z
    .object({ DM: z.number().int().min(0).max(4) })
    .passthrough(),
});

const UsdmCollectionSchema = z
  .object({
    type: z.literal("FeatureCollection"),
    features: z.array(UsdmFeatureSchema),
  })
  .passthrough();

/** Formats a UTC date as the YYYYMMDD path segment USDM names its files by. */
function toPathDate(date: Date): string {
  return date.toISOString().slice(0, 10).replace(/-/g, "");
}

/** Formats a UTC date as the ISO YYYY-MM-DD stored as valid_date. */
function toIsoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/**
 * Most recent USDM valid dates (Tuesdays), newest first.
 * Exported for the ingestion job's release-walk and its tests.
 */
export function usdmValidDateCandidates(
  now = new Date(),
  weeks = DEFAULT_CANDIDATE_WEEKS
): string[] {
  const bounded = Math.min(MAX_CANDIDATE_WEEKS, Math.max(1, weeks));
  const cursor = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())
  );
  // Step back to the most recent Tuesday (today counts when it is a Tuesday).
  cursor.setUTCDate(cursor.getUTCDate() - ((cursor.getUTCDay() - TUESDAY + 7) % 7));

  const candidates: string[] = [];
  for (let week = 0; week < bounded; week++) {
    candidates.push(toIsoDate(cursor));
    cursor.setUTCDate(cursor.getUTCDate() - 7);
  }
  return candidates;
}

/** The exact upstream file a release was read from; stored as its provenance. */
export function usdmSourceUrl(validDate: string): string {
  return `${BASE_URL}/usdm_${toPathDate(new Date(`${validDate}T00:00:00Z`))}.json`;
}

/**
 * Fetches one dated USDM release.
 *
 * The date is the request parameter, not something parsed out of the payload:
 * the published GeoJSON carries no date field at all, so `usdm_current.json` is
 * deliberately never used. A 200 for `usdm_<date>.json` is USDM's own
 * confirmation that it published that release; an unpublished date 404s.
 *
 * @param validDate ISO `YYYY-MM-DD`, which must be a USDM Tuesday.
 * @returns the release, or null when USDM has not published that date.
 */
export async function fetchUsdmDroughtRelease(
  validDate: string
): Promise<UsdmDroughtRelease | null> {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(validDate)) {
    throw new RangeError("USDM valid date must be an ISO YYYY-MM-DD string");
  }
  const parsedDate = new Date(`${validDate}T00:00:00Z`);
  if (!Number.isFinite(parsedDate.getTime())) {
    throw new RangeError("USDM valid date is not a real calendar date");
  }
  if (parsedDate.getUTCDay() !== TUESDAY) {
    throw new RangeError("USDM releases are only valid for Tuesdays");
  }

  const sourceUrl = usdmSourceUrl(validDate);
  let payload: unknown;
  try {
    payload = await fetchBoundedJson(
      sourceUrl,
      { method: "GET", headers: { Accept: "application/json" } },
      { maxBytes: MAX_RESPONSE_BYTES, timeoutMs: REQUEST_TIMEOUT_MS }
    );
  } catch (error) {
    // 404 is the documented "not published yet" signal, not a failure.
    if (error instanceof UpstreamHttpError && error.status === 404) return null;
    throw error;
  }

  const parsed = UsdmCollectionSchema.safeParse(payload);
  if (!parsed.success) {
    throw new UpstreamPayloadError(
      "USDM returned an unexpected drought feature collection shape"
    );
  }

  const areas = new Map<number, UsdmDroughtArea>();
  for (const feature of parsed.data.features) {
    // A duplicated DM class would make the release ambiguous rather than
    // mergeable, so the whole release is rejected instead of silently picking one.
    if (areas.has(feature.properties.DM)) {
      throw new UpstreamPayloadError(
        `USDM release ${validDate} repeats drought class D${feature.properties.DM}`
      );
    }
    areas.set(feature.properties.DM, {
      dmCategory: feature.properties.DM,
      geometry: feature.geometry,
    });
  }
  if (areas.size === 0) {
    throw new UpstreamPayloadError(
      `USDM release ${validDate} contained no drought classes`
    );
  }

  return {
    validDate,
    sourceUrl,
    areas: [...areas.values()].sort((a, b) => a.dmCategory - b.dmCategory),
  };
}

/**
 * Walks back over recent USDM Tuesdays and returns the newest published release.
 * Returns null when none of the candidate weeks has been published.
 */
export async function fetchLatestUsdmDroughtRelease(
  now = new Date(),
  weeks = DEFAULT_CANDIDATE_WEEKS
): Promise<UsdmDroughtRelease | null> {
  for (const validDate of usdmValidDateCandidates(now, weeks)) {
    const release = await fetchUsdmDroughtRelease(validDate);
    if (release) return release;
  }
  return null;
}
