import { describe, expect, it } from "vitest";
import { boundaryIntersectsBbox, containsBoundaryPoint } from "../spatial-match";

const polygon: GeoJSON.Polygon = { type: "Polygon", coordinates: [
  [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
  [[3, 3], [7, 3], [7, 7], [3, 7], [3, 3]],
] };

describe("published polygon spatial matching", () => {
  it("rejects points and selections wholly in holes while retaining boundary touches", () => {
    expect(containsBoundaryPoint(polygon, [1, 1])).toBe(true);
    expect(containsBoundaryPoint(polygon, [5, 5])).toBe(false);
    expect(containsBoundaryPoint(polygon, [3, 5])).toBe(true);
    expect(boundaryIntersectsBbox(polygon, { west: 4, south: 4, east: 6, north: 6 })).toBe(false);
    expect(boundaryIntersectsBbox(polygon, { west: 10, south: 2, east: 11, north: 3 })).toBe(true);
  });

  it("rejects bbox-only false positives and accepts edge crossings without contained corners", () => {
    const triangle: GeoJSON.Polygon = { type: "Polygon", coordinates: [[[0, 0], [10, 0], [0, 10], [0, 0]]] };
    expect(boundaryIntersectsBbox(triangle, { west: 8, south: 8, east: 9, north: 9 })).toBe(false);
    expect(boundaryIntersectsBbox(polygon, { west: -1, south: 1, east: 11, north: 2 })).toBe(true);
  });

  it("preserves disjoint polygons and does not infer a polygon from a point or missing geometry", () => {
    const multi: GeoJSON.MultiPolygon = { type: "MultiPolygon", coordinates: [polygon.coordinates,
      [[[20, 20], [21, 20], [21, 21], [20, 21], [20, 20]]]] };
    expect(containsBoundaryPoint(multi, [20.5, 20.5])).toBe(true);
    expect(containsBoundaryPoint(multi, [15, 15])).toBe(false);
    expect(containsBoundaryPoint(null, [0, 0])).toBeNull();
    expect(containsBoundaryPoint({ type: "Point", coordinates: [0, 0] }, [0, 0])).toBeNull();
  });
});
