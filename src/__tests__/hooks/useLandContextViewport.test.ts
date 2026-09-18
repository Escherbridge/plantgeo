/**
 * The automatic land-context viewport read's BOUNDS.
 *
 * Exercises the pure rung/ceiling decisions rather than the React hook itself: the decisions are
 * what keep a regional pan from collecting a refusal banner, and they are the part a future edit
 * can silently get wrong. No network is reachable from here -- nothing in this file issues a
 * request, and the constants are asserted against their server-side originals by value.
 */
import { describe, expect, it } from "vitest";
import {
  LAND_CONTEXT_MAX_AOI_SQUARE_DEGREES,
  LAND_CONTEXT_RUNG_MAX_BBOX_SQUARE_DEGREES,
  landContextRungForViewport,
} from "@/hooks/useLandContextViewport";
import { MAX_AOI_AREA_SQUARE_DEGREES } from "@/lib/server/services/land-context/budgets";
import { RUNG_MAX_BBOX_SQUARE_DEGREES } from "@/lib/server/services/land-context/parquet-reader";

describe("the restated server ceilings", () => {
  it("mirrors MAX_AOI_AREA_SQUARE_DEGREES exactly", () => {
    expect(LAND_CONTEXT_MAX_AOI_SQUARE_DEGREES).toBe(MAX_AOI_AREA_SQUARE_DEGREES);
  });

  it("mirrors the server's per-rung bbox ceilings exactly", () => {
    expect(LAND_CONTEXT_RUNG_MAX_BBOX_SQUARE_DEGREES).toEqual(RUNG_MAX_BBOX_SQUARE_DEGREES);
  });
});

describe("landContextRungForViewport", () => {
  it("picks the finest rung at or below the zoom's own tier that admits the area", () => {
    expect(landContextRungForViewport(14, 0.25)).toBe(13);
    expect(landContextRungForViewport(9, 50)).toBe(9);
    expect(landContextRungForViewport(5, 500)).toBe(5);
  });

  it("never selects a rung finer than the zoom resolves to", () => {
    // z8 resolves to the z5 rung, so a tiny bbox must not be answered from z13 or z9.
    expect(landContextRungForViewport(8, 0.01)).toBe(5);
  });

  it("steps down the ladder for a regional viewport instead of refusing on a tight rung", () => {
    // 98 sq deg at z13 exceeds the detail rung's 4, so it drops to z9's 100.
    expect(landContextRungForViewport(13, 98)).toBe(9);
  });

  it("returns null, never a guess, for a zoom the ladder cannot resolve", () => {
    expect(landContextRungForViewport(Number.NaN, 0.25)).toBeNull();
    expect(landContextRungForViewport(-1, 0.25)).toBeNull();
  });

  it("returns null for an area no rung admits", () => {
    expect(landContextRungForViewport(13, 1_000_000)).toBeNull();
  });
});

describe("the AOI budget is what actually gates the automatic read", () => {
  it("admits a rung for a regional viewport that the AOI budget still refuses", () => {
    // Both statements are true at once, and the hook must report `area_over_budget` rather than
    // ask: a rung exists that could serve 98 sq deg, but `readBoundedAoiIntersection` caps the
    // AOI at 1. Raising that cap is an owner decision about server load, not a knob.
    expect(landContextRungForViewport(9, 98)).toBe(9);
    expect(98).toBeGreaterThan(LAND_CONTEXT_MAX_AOI_SQUARE_DEGREES);
  });

  it("admits a detail-rung viewport that is also within the AOI budget", () => {
    expect(landContextRungForViewport(13, 0.25)).toBe(13);
    expect(0.25).toBeLessThanOrEqual(LAND_CONTEXT_MAX_AOI_SQUARE_DEGREES);
  });
});
