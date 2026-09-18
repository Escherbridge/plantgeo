/**
 * Frozen numeric limits for every land-context bounded reader.
 *
 * Per the reference-plane spec's "Bounded readers and agent contract":
 * "Freeze numeric limits for AOI area, geometry vertices, features/
 * relationships, response bytes, time, pagination and rate/concurrency
 * before implementation. A request outside the pilot or over budget gets a
 * typed response; no silent truncation."
 *
 * These are deliberately conservative placeholders sized for a pilot-scale
 * WA/OR/ID sample, not a production tuning pass. Raising any of them is a
 * scoped, reviewed change, not a silent runtime knob.
 */

import { REGION_SUBDIVISION_CODES } from "@/lib/region/region";

/** Largest AOI a single bounded query may cover, in square degrees (EPSG:4326). */
export const MAX_AOI_AREA_SQUARE_DEGREES = 1;

/** Largest polygon vertex count this service will accept in a caller-supplied AOI. */
export const MAX_AOI_GEOMETRY_VERTICES = 2_000;

/** Largest number of intersecting features/relationships a single response may return. */
export const MAX_FEATURES_RETURNED = 200;

/** Largest serialized response payload, in bytes, before a request is refused as over-budget. */
export const MAX_RESPONSE_BYTES = 2_000_000;

/** Largest page size a paginated reader will honor. */
export const MAX_PAGE_SIZE = 50;

/**
 * Pilot states this reader plane admits. Anything else is `outside_pilot`, not a silent miss.
 *
 * Read from the region manifest, not restated: this was the twin of `PNW_STATE_CODES` that step 2
 * left behind, and the Parquet reader's row schema binds to it (STYLE-REVIEW-W2 S2). One canonical
 * definition means a next region changing `admin_codes` cannot leave the manifest, the alias and
 * the reader disagreeing.
 */
export const PILOT_STATES = REGION_SUBDIVISION_CODES;

/**
 * Rough byte-size estimate for a candidate result set, used to decide whether returning it
 * would exceed `MAX_RESPONSE_BYTES` before doing the (potentially expensive) exact-intersection
 * pass. This is deliberately crude (JSON.stringify length) — good enough to reject an
 * over-budget request early, not a precise wire-size prediction.
 */
export function estimateResponseBytes(candidate: unknown): number {
  return JSON.stringify(candidate).length;
}

export function isWithinAoiAreaBudget(areaSquareDegrees: number): boolean {
  return areaSquareDegrees <= MAX_AOI_AREA_SQUARE_DEGREES;
}

export function isWithinVertexBudget(vertexCount: number): boolean {
  return vertexCount <= MAX_AOI_GEOMETRY_VERTICES;
}

export function isWithinFeatureCountBudget(featureCount: number): boolean {
  return featureCount <= MAX_FEATURES_RETURNED;
}
