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
 * The PILOT's subdivision codes, as the compile-time literal tuple storage schemas need.
 *
 * Read from the region's declared admin-code tuple, not restated: this was the twin of
 * `PNW_STATE_CODES` that step 2 left behind, and the Parquet reader's row schema binds to it
 * (STYLE-REVIEW-W2 S2). One canonical definition means a next region changing `admin_codes` cannot
 * leave the manifest, the alias and the reader disagreeing -- and because
 * `REGION_SUBDIVISION_CODES` is derived from a compile-time tuple rather than from `getRegion()`,
 * importing this module no longer reads a region manifest at import time (W4 S1).
 *
 * NOT the request vocabulary any more (STYLE-REVIEW-W8 B1). Every surface that validates a
 * caller-supplied state — the tRPC router, the bounded readers, the agent tools — admits
 * `admittedSubdivisionCodes()` from the SELECTED manifest at call time, because this tuple is the
 * pilot's and would have offered WA/OR/ID to a deployment covering somewhere else. What remains
 * here is the pilot's PHYSICAL land-context plane: `parquet-reader.ts`'s row schema and Drizzle's
 * `pgEnum` both need a literal tuple at module scope, and a second region could only gain those
 * columns through a migration of its own.
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
