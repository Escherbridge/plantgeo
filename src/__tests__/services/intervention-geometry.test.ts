import { describe, expect, it } from "vitest";
import {
  computeInterventionAreaAcres,
  getInterventionAreaCapIssue,
  LAND_INTERVENTION_AREA_CAP_ACRES,
  AIR_INTERVENTION_AREA_CAP_ACRES,
} from "@/lib/server/services/intervention-geometry";
import type { InterventionGeometry } from "@/lib/server/services/intervention-geometry";

const POINT: InterventionGeometry = {
  type: "Point",
  coordinates: [-122.6784, 45.5152],
};

/**
 * A near-equator square ring with `sideMeters` sides, using the small-angle
 * approximation (1 degree of longitude/latitude at the equator is ~111,320m)
 * so the expected planar area is easy to reason about; `polygonArea`'s
 * spherical-excess formula converges to the planar answer at this scale.
 */
function squareRing(sideMeters: number): number[][] {
  const metersPerDegree = 111_320;
  const d = sideMeters / metersPerDegree;
  return [
    [0, 0],
    [d, 0],
    [d, d],
    [0, d],
    [0, 0],
  ];
}

function squarePolygon(sideMeters: number): InterventionGeometry {
  return { type: "Polygon", coordinates: [squareRing(sideMeters)] };
}

describe("computeInterventionAreaAcres", () => {
  it("returns 0 for a Point", () => {
    expect(computeInterventionAreaAcres(POINT)).toBe(0);
  });

  it("converts a square Polygon of known side length to acres within tolerance", () => {
    const sideMeters = 1000;
    const expectedAcres = (sideMeters * sideMeters) / 4046.8564224;
    const actual = computeInterventionAreaAcres(squarePolygon(sideMeters));
    expect(actual).toBeGreaterThan(expectedAcres * 0.9);
    expect(actual).toBeLessThan(expectedAcres * 1.1);
  });

  it("sums the parts of a MultiPolygon", () => {
    const sideMeters = 1000;
    const singleAcres = computeInterventionAreaAcres(squarePolygon(sideMeters));
    const multi: InterventionGeometry = {
      type: "MultiPolygon",
      coordinates: [[squareRing(sideMeters)], [squareRing(sideMeters)]],
    };
    const actual = computeInterventionAreaAcres(multi);
    expect(actual).toBeGreaterThan(singleAcres * 2 * 0.9);
    expect(actual).toBeLessThan(singleAcres * 2 * 1.1);
  });
});

describe("getInterventionAreaCapIssue", () => {
  it("passes a land polygon under the 500 acre cap", () => {
    // ~100m side ≈ 2.47 acres
    expect(getInterventionAreaCapIssue(squarePolygon(100), "land")).toBeNull();
  });

  it("fails a land polygon over the 500 acre cap, naming the cap and computed area", () => {
    // ~3000m side ≈ 2224 acres
    const issue = getInterventionAreaCapIssue(squarePolygon(3000), "land");
    expect(issue).not.toBeNull();
    expect(issue).toContain(String(LAND_INTERVENTION_AREA_CAP_ACRES));
    expect(issue).toMatch(/\d+(\.\d+)? acres/);
  });

  it("passes an air polygon at 10,000 acres (under the air ceiling)", () => {
    const tenThousandAcresInSquareMeters = 10_000 * 4046.8564224;
    const sideMeters = Math.sqrt(tenThousandAcresInSquareMeters);
    expect(
      getInterventionAreaCapIssue(squarePolygon(sideMeters), "air")
    ).toBeNull();
    expect(AIR_INTERVENTION_AREA_CAP_ACRES).toBeGreaterThan(10_000);
  });

  it("always passes a Point regardless of category", () => {
    expect(getInterventionAreaCapIssue(POINT, "land")).toBeNull();
    expect(getInterventionAreaCapIssue(POINT, "air")).toBeNull();
  });
});
