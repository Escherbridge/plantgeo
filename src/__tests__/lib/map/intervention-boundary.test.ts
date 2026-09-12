import { describe, expect, it } from "vitest";
import { boundaryArea, boundaryPreview, finishBoundary, MAX_BOUNDARY_VERTICES, type BoundaryPoint } from "@/lib/map/intervention-boundary";

describe("intervention boundary geometry", () => {
  it("closes a local rectangle with geographic coordinates and spherical area", () => {
    const geometry = finishBoundary("rectangle", [[-116, 44], [-115.99, 44.01]]);
    expect(geometry.type).toBe("Polygon");
    if (geometry.type !== "Polygon") throw new Error("Expected polygon");
    const ring = geometry.coordinates[0] as BoundaryPoint[];
    expect(ring).toHaveLength(5);
    expect(ring[0]).toEqual(ring[4]);
    expect(boundaryArea(ring)).toBeGreaterThan(800_000);
    expect(boundaryArea(ring)).toBeLessThan(1_000_000);
  });

  it("normalizes exterior winding while preserving a polygon's vertices", () => {
    const geometry = finishBoundary("polygon", [[0, 0], [0, 1], [1, 0]]);
    expect(geometry).toEqual({ type: "Polygon", coordinates: [[[1, 0], [0, 1], [0, 0], [1, 0]]] });
  });

  it.each([
    { mode: "polygon" as const, points: [[0, 0], [1, 1], [0, 1], [1, 0]], message: /cross/ },
    { mode: "polygon" as const, points: [[0, 0], [0, 1], [0, 0]], message: /repeated/ },
    { mode: "polygon" as const, points: [[0, 0], [1, 0], [2, 0]], message: /square metre/ },
    { mode: "rectangle" as const, points: [[0, 0], [0, 1]], message: /repeated/ },
    { mode: "rectangle" as const, points: [[179, 40], [-179, 41]], message: /antimeridian/ },
    { mode: "point" as const, points: [[Number.NaN, 44]], message: /finite/ },
    { mode: "point" as const, points: [[-116, 90]], message: /poles/ },
  ])("rejects invalid $mode geometry", ({ mode, points, message }) => {
    expect(() => finishBoundary(mode, points as BoundaryPoint[])).toThrow(message);
  });

  it("keeps the interactive ring below server ring and total vertex ceilings", () => {
    expect(MAX_BOUNDARY_VERTICES + 1).toBeLessThanOrEqual(4_096);
    expect(() => finishBoundary("polygon", Array.from({ length: MAX_BOUNDARY_VERTICES + 1 }, () => [0, 0] as BoundaryPoint))).toThrow(/at most/);
  });

  it("renders the first vertex and next segment before there is enough geometry to save", () => {
    const preview = boundaryPreview("polygon", [[-116, 44]], [-115.99, 44.01]);
    expect(preview.features.find((feature) => feature.properties?.first)?.geometry).toEqual({ type: "Point", coordinates: [-116, 44] });
    expect(preview.features.find((feature) => feature.properties?.next)?.geometry).toEqual({ type: "Point", coordinates: [-115.99, 44.01] });
    expect(preview.features.find((feature) => feature.geometry.type === "LineString")?.geometry).toEqual({ type: "LineString", coordinates: [[-116, 44], [-115.99, 44.01]] });
    expect(() => finishBoundary("polygon", [[-116, 44]])).toThrow(/three/);
  });
});
