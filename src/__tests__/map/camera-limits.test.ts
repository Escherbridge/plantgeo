import { describe, expect, it } from "vitest";
import { MAP_MAX_ZOOM, MAP_MIN_ZOOM, clampCameraZoom } from "@/lib/map/camera-limits";

/**
 * `clampCameraZoom` is the one seam every untrusted zoom value actually passes through before
 * it reaches a real camera move -- a geocoder's `ZOOM_BY_TYPE` lookup, a `focusZoom` URL param
 * (via `focus-params.ts`'s `readMapFocus`, which clamps with it), and `EmbedMap`'s `postMessage`
 * handler all funnel through it or the ceiling it shares. Table-driven over its real branches
 * (below floor, in range, above ceiling, non-finite) rather than one assertion per case, per
 * the project's "unit tests are for pure logic with real branching" rule -- and each row would
 * go red if the clamp's min/max were swapped or its fallback direction flipped.
 */
describe("clampCameraZoom", () => {
  it.each([
    ["a mid-range zoom", 10, 10],
    ["the ceiling itself", MAP_MAX_ZOOM, MAP_MAX_ZOOM],
    ["the floor itself", MAP_MIN_ZOOM, MAP_MIN_ZOOM],
    ["a pinch-overshoot past the ceiling", 25, MAP_MAX_ZOOM],
    ["a negative zoom below the floor", -5, MAP_MIN_ZOOM],
    ["NaN from a malformed query param or postMessage payload", Number.NaN, MAP_MAX_ZOOM],
    ["Infinity", Number.POSITIVE_INFINITY, MAP_MAX_ZOOM],
    ["-Infinity", Number.NEGATIVE_INFINITY, MAP_MAX_ZOOM],
  ])("%s (%j) resolves to %j", (_description, input, expected) => {
    expect(clampCameraZoom(input)).toBe(expected);
  });

  it("never returns a value outside [MAP_MIN_ZOOM, MAP_MAX_ZOOM] for any finite input", () => {
    for (const probe of [-1000, -1, 0, 0.5, 9, 18.9, 19, 19.1, 100, 1e9]) {
      const result = clampCameraZoom(probe);
      expect(result).toBeGreaterThanOrEqual(MAP_MIN_ZOOM);
      expect(result).toBeLessThanOrEqual(MAP_MAX_ZOOM);
    }
  });
});
