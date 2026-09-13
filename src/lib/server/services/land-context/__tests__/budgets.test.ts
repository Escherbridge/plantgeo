/**
 * Requirement 1: budget enforcement never silently truncates.
 *
 * Exercises `readBoundedAoiIntersection` with an AOI area that exceeds
 * `MAX_AOI_AREA_SQUARE_DEGREES` and asserts the typed `budget_exceeded`
 * response, per reference-plane spec "Bounded readers and agent contract":
 * "A request outside the pilot or over budget gets a typed response; no
 * silent truncation."
 */
import { describe, expect, it } from "vitest";
import { readBoundedAoiIntersection } from "@/lib/server/services/land-context/reader";
import { MAX_AOI_AREA_SQUARE_DEGREES } from "@/lib/server/services/land-context/budgets";

describe("readBoundedAoiIntersection budget enforcement", () => {
  it("returns a typed budget_exceeded response, never a truncated ok, when AOI area exceeds the limit", async () => {
    // MAX_AOI_AREA_SQUARE_DEGREES is 1; build a bbox with area well beyond it.
    const oversizedBbox = { west: -125, south: 40, east: -110, north: 55 }; // 15 x 15 = 225 sq deg
    const area = (oversizedBbox.east - oversizedBbox.west) * (oversizedBbox.north - oversizedBbox.south);
    expect(area).toBeGreaterThan(MAX_AOI_AREA_SQUARE_DEGREES);

    const result = await readBoundedAoiIntersection(oversizedBbox);

    expect(result.status).toBe("budget_exceeded");
    if (result.status === "budget_exceeded") {
      expect(result.reason).toBe("aoi_area_exceeds_limit");
      expect(result.limit).toBe(MAX_AOI_AREA_SQUARE_DEGREES);
      expect(result.requested).toBe(area);
    }
    // Never a truncated ok with fewer features than requested.
    expect(result).not.toMatchObject({ status: "ok" });
  });

  it("does not silently truncate: a marginally over-budget AOI is rejected, not clipped to the limit", async () => {
    const side = Math.sqrt(MAX_AOI_AREA_SQUARE_DEGREES) + 0.01;
    const bbox = { west: 0, south: 0, east: side, north: side };
    const result = await readBoundedAoiIntersection(bbox);
    expect(result.status).toBe("budget_exceeded");
  });

  it("accepts an AOI within budget without rejecting for area (may still resolve ok/no-match, never budget_exceeded for area reasons)", async () => {
    const bbox = { west: -122.5, south: 47.5, east: -122.0, north: 48.0 }; // 0.5 x 0.5 = 0.25 sq deg
    const result = await readBoundedAoiIntersection(bbox);
    if (result.status === "budget_exceeded") {
      expect(result.reason).not.toBe("aoi_area_exceeds_limit");
    } else {
      expect(result.status).toBe("ok");
    }
  });
});
