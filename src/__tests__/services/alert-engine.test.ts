import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/services/environmental-read-model", () => ({
  getPublishedFireDetections: vi.fn(),
  getPublishedStreamflowGauges: vi.fn(),
}));

import {
  droughtLevelAtPoint,
  isFreshDroughtRelease,
} from "@/lib/server/services/alert-engine";

const collection: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [
    {
      type: "Feature",
      properties: { DM: 3 },
      geometry: {
        type: "Polygon",
        coordinates: [[[-106, 39], [-104, 39], [-104, 41], [-106, 41], [-106, 39]]],
      },
    },
    {
      type: "Feature",
      properties: { DM: 4 },
      geometry: {
        type: "Polygon",
        coordinates: [[[-101, 34], [-99, 34], [-99, 36], [-101, 36], [-101, 34]]],
      },
    },
  ],
};

describe("droughtLevelAtPoint", () => {
  it("uses polygon containment instead of region-wide presence", () => {
    expect(droughtLevelAtPoint(collection, 40, -105)).toBe(3);
    expect(droughtLevelAtPoint(collection, 35, -100)).toBe(4);
    expect(droughtLevelAtPoint(collection, 45, -110)).toBeNull();
  });
});

describe("isFreshDroughtRelease", () => {
  const now = new Date("2026-07-20T12:00:00.000Z");

  it("accepts a recent observation with a recent release receipt", () => {
    expect(
      isFreshDroughtRelease(
        "2026-07-14",
        new Date("2026-07-15T08:00:00.000Z"),
        now
      )
    ).toBe(true);
  });

  it("rejects stale, malformed, or recently refetched stale observations", () => {
    expect(
      isFreshDroughtRelease(
        "2026-06-01",
        new Date("2026-07-20T08:00:00.000Z"),
        now
      )
    ).toBe(false);
    expect(
      isFreshDroughtRelease(
        "not-a-date",
        new Date("2026-07-20T08:00:00.000Z"),
        now
      )
    ).toBe(false);
    expect(isFreshDroughtRelease("2026-07-14", null, now)).toBe(false);
  });
});
