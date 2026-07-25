import { describe, expect, it } from "vitest";
import type { Feature, Point } from "geojson";
import {
  getStableFeatureId,
  upsertBoundedFeature,
} from "@/hooks/useLiveLayer";

function pointFeature(
  id: Feature<Point>["id"],
  propertyId?: unknown
): Feature<Point> {
  return {
    type: "Feature",
    id,
    geometry: { type: "Point", coordinates: [-105, 40] },
    properties: propertyId === undefined ? {} : { id: propertyId },
  };
}

describe("getStableFeatureId", () => {
  it("uses a publisher-assigned GeoJSON id", () => {
    expect(getStableFeatureId(pointFeature("fire-1"))).toBe("fire-1");
    expect(getStableFeatureId(pointFeature(42))).toBe("42");
  });

  it("uses a stable property id when the GeoJSON id is absent", () => {
    expect(getStableFeatureId(pointFeature(undefined, "gauge-7"))).toBe("gauge-7");
  });

  it("rejects features without a stable identity", () => {
    expect(getStableFeatureId(pointFeature(undefined))).toBeNull();
    expect(getStableFeatureId(pointFeature(undefined, "  "))).toBeNull();
    expect(getStableFeatureId(pointFeature(undefined, Number.NaN))).toBeNull();
  });

  it("bounds retained features and refreshes replacement recency", () => {
    const features = new Map<string, Feature>();
    upsertBoundedFeature(features, pointFeature("one"), 2);
    upsertBoundedFeature(features, pointFeature("two"), 2);
    upsertBoundedFeature(features, pointFeature("one"), 2);
    upsertBoundedFeature(features, pointFeature("three"), 2);

    expect([...features.keys()]).toEqual(["one", "three"]);
  });
});
