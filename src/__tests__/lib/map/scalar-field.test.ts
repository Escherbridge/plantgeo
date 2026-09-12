import { describe, expect, it } from "vitest";
import { buildScalarFieldMesh, scalarFieldCellSpacing, scalarFieldEnabled } from "@/lib/map/scalar-field";

function cell(x = 0, y = 0, value = -0.4): GeoJSON.Feature<GeoJSON.Polygon> {
  return {
    type: "Feature",
    geometry: { type: "Polygon", coordinates: [[[x, y], [x + 0.25, y], [x + 0.25, y + 0.25], [x, y + 0.25], [x, y]]] },
    properties: { ndvi: value, observedDay: "2026-09-01", gridName: "ndvi-grid", metricUnit: "1", supportId: `${x}:${y}`, supportKind: "tessellated_cell", cellWidthDegrees: 0.25, cellHeightDegrees: 0.25 },
  };
}
const mesh = (...features: GeoJSON.Feature[]) => buildScalarFieldMesh({ type: "FeatureCollection", features }, "ndvi", [-1, 1]);

describe("nearest scalar support mesh", () => {
  it("is opted in by exact comma-separated layer name", () => {
    expect(scalarFieldEnabled("vegetation", undefined)).toBe(false);
    expect(scalarFieldEnabled("vegetation", "vegetation-extra")).toBe(false);
    expect(scalarFieldEnabled("vegetation", "weather, vegetation ")).toBe(true);
  });
  it("preserves negative NDVI and leaves a missing cell uncovered", () => {
    const result = mesh(cell(0), cell(0.5, 0, 0.8));
    expect(result?.cells).toHaveLength(2);
    expect(result?.vertices).toHaveLength(36);
    expect(result?.cells.map((item) => item.value)).toEqual([-0.4, 0.8]);
    expect(result?.cells.some((item) => item.west === 0.25)).toBe(false);
    expect(result?.vertices[2]).toBeCloseTo(-0.4);
  });
  it("deduplicates identical geometry/value without accumulating intensity", () => {
    expect(mesh(cell(), cell())?.cells).toHaveLength(1);
    expect(mesh(cell(), cell(0, 0, 0.8))).toBeNull();
  });
  it.each(["observedDay", "metricUnit", "gridName"])("falls back for incompatible %s", (key) => {
    const other = cell(0.25);
    other.properties![key] = key === "observedDay" ? "2026-09-02" : "other";
    expect(mesh(cell(), other)).toBeNull();
  });
  it("accepts distinct support IDs but not shifted/overlapping grids", () => {
    expect(mesh(cell(), cell(0.25))?.cells).toHaveLength(2);
    expect(mesh(cell(), cell(0.125))).toBeNull();
  });
  it.each([NaN, Infinity, -1.01, 1.01, null, "0.5"])("rejects invalid scalar %s", (value) => {
    const invalid = cell();
    invalid.properties!.ndvi = value;
    expect(mesh(invalid)).toBeNull();
  });
  it("rejects antimeridian wrap, missing support, malformed rectangles and holes", () => {
    expect(mesh(cell(179.9))).toBeNull();
    const missing = cell();
    delete missing.properties!.observedDay;
    expect(mesh(missing)).toBeNull();
    const skew = cell();
    skew.geometry.coordinates[0][1][1] = 0.1;
    expect(mesh(skew)).toBeNull();
    const hole = cell();
    hole.geometry.coordinates.push(hole.geometry.coordinates[0]);
    expect(mesh(hole)).toBeNull();
    const dimension = cell();
    dimension.properties!.cellWidthDegrees = NaN;
    expect(mesh(dimension)).toBeNull();
  });
  it("does not invent footprints for point observations or empty responses", () => {
    expect(mesh({ type: "Feature", geometry: { type: "Point", coordinates: [0, 0] }, properties: cell().properties })).toBeNull();
    expect(mesh()).toBeNull();
  });
  it("uses projected support spacing rather than nominal zoom or degrees", () => {
    const cells = mesh(cell())!.cells;
    expect(scalarFieldCellSpacing(cells, ([x, y]) => ({ x: x * 128, y: y * 256 }))).toBe(32);
    expect(scalarFieldCellSpacing(cells, ([x, y]) => ({ x: x * 256, y: y * 256 }))).toBe(64);
  });
});
