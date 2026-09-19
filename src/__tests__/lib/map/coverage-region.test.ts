import { describe, expect, it } from "vitest";
import {
  describeCoverageRegion,
  fallbackCoverageBbox,
  formatCoverageBounds,
} from "@/lib/map/coverage-region";

const PACIFIC_NORTHWEST = { west: -125, south: 42, east: -111, north: 49 };

describe("describeCoverageRegion", () => {
  it("names the production PNW ingestion bbox", () => {
    expect(describeCoverageRegion(PACIFIC_NORTHWEST)).toBe("Pacific Northwest");
  });

  it("prefers the tightest containing region over a wider one", () => {
    // Also fully inside "Western United States" and "North America".
    expect(describeCoverageRegion({ west: -124, south: 43, east: -117, north: 48 })).toBe(
      "Pacific Northwest"
    );
  });

  it("widens to the next region when the bbox outgrows the tighter one", () => {
    expect(describeCoverageRegion({ west: -124, south: 33, east: -112, north: 48 })).toBe(
      "Western United States"
    );
  });

  it("falls back to bounds for a region it does not know", () => {
    expect(describeCoverageRegion({ west: 5, south: 45, east: 15, north: 55 })).toBe(
      "45.0°N–55.0°N, 5.0°E–15.0°E"
    );
  });
});

describe("fallbackCoverageBbox", () => {
  it("stays pinned to the pre-manifest MTBS burn envelope, not the wider named-region one", () => {
    // The region-manifest migration (federation.md §5 step 1) must be behaviour-neutral: the
    // opening camera and ServiceAreaLayer's fallback bounds keep this exact box even though
    // `getRegion().envelope` is wider (-126,41,-110,50). See coverage-region.ts's own comment
    // and src/lib/region/AGENTS.md §default_camera_envelope.
    expect(fallbackCoverageBbox()).toEqual({ west: -125, south: 42, east: -111, north: 49 });
  });
});

describe("formatCoverageBounds", () => {
  it("renders hemispheres for the PNW bbox", () => {
    expect(formatCoverageBounds(PACIFIC_NORTHWEST)).toBe(
      "42.0°N–49.0°N, 125.0°W–111.0°W"
    );
  });
});
