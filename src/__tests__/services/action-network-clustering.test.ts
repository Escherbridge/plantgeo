import { describe, expect, it } from "vitest";
import {
  clusterActionNetworkPoints,
  type ActionNetworkPoint,
} from "@/lib/action-network-clustering";

function point(
  longitude: number,
  latitude: number,
  voteCount: number,
  featureCount: number
): ActionNetworkPoint {
  return {
    type: "Feature",
    geometry: { type: "Point", coordinates: [longitude, latitude] },
    properties: { voteCount, featureCount },
  };
}

describe("clusterActionNetworkPoints", () => {
  it("does not convert zero-vote centroid weights into votes", () => {
    const result = clusterActionNetworkPoints(
      [point(0.1, 0.1, 0, 1), point(0.2, 0.2, 0, 2)],
      0,
      {}
    );

    expect(result.features).toHaveLength(1);
    expect(result.features[0].properties).toEqual({
      voteCount: 0,
      featureCount: 3,
    });
    expect(result.features[0].geometry.coordinates[0]).toBeCloseTo(1 / 6);
  });

  it("sums actual votes independently of feature counts", () => {
    const result = clusterActionNetworkPoints(
      [point(0.1, 0.1, 2, 7), point(0.2, 0.2, 5, 1)],
      0,
      {}
    );

    expect(result.features[0].properties.voteCount).toBe(7);
    expect(result.features[0].properties.featureCount).toBe(8);
  });
});
