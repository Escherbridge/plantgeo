export type BoundaryPoint = [number, number];
export type BoundaryMode = "polygon" | "rectangle" | "point";
export type BoundaryGeometry = GeoJSON.Point | GeoJSON.Polygon;
export type InterventionSiteGeometry = BoundaryGeometry | GeoJSON.MultiPolygon;

export const MAX_BOUNDARY_VERTICES = 4_095;
export const MAX_BOUNDARY_BYTES = 900_000;

function cross(a: BoundaryPoint, b: BoundaryPoint, c: BoundaryPoint) {
  return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
}

function intersects(a: BoundaryPoint, b: BoundaryPoint, c: BoundaryPoint, d: BoundaryPoint) {
  const on = (p: BoundaryPoint, q: BoundaryPoint, r: BoundaryPoint) =>
    cross(p, q, r) === 0 && r[0] >= Math.min(p[0], q[0]) && r[0] <= Math.max(p[0], q[0]) &&
    r[1] >= Math.min(p[1], q[1]) && r[1] <= Math.max(p[1], q[1]);
  return (cross(a, b, c) * cross(a, b, d) < 0 && cross(c, d, a) * cross(c, d, b) < 0) ||
    on(a, b, c) || on(a, b, d) || on(c, d, a) || on(c, d, b);
}

/** Spherical area in square metres for a ring that does not cross the antimeridian. */
export function boundaryArea(points: BoundaryPoint[]): number {
  const radians = Math.PI / 180;
  const sum = points.reduce((total, a, i) => {
    const b = points[(i + 1) % points.length];
    return total + (b[0] - a[0]) * radians * (2 + Math.sin(a[1] * radians) + Math.sin(b[1] * radians));
  }, 0);
  return Math.abs(sum) * 6_371_008.8 ** 2 / 2;
}

export function boundaryVertices(mode: BoundaryMode, points: BoundaryPoint[]): BoundaryPoint[] {
  if (mode !== "rectangle" || points.length < 2) return points;
  const [a, b] = points;
  return [a, [b[0], a[1]], b, [a[0], b[1]]];
}

/** Validate a hand-authored single ring before creating its closed GeoJSON geometry. */
export function finishBoundary(mode: BoundaryMode, points: BoundaryPoint[]): BoundaryGeometry {
  if (points.length > MAX_BOUNDARY_VERTICES) throw new Error(`Use at most ${MAX_BOUNDARY_VERTICES} vertices.`);
  if (points.some(([lng, lat]) => !Number.isFinite(lng) || !Number.isFinite(lat) || lng < -180 || lng > 180 || lat <= -90 || lat >= 90)) {
    throw new Error("Choose finite longitude and latitude coordinates away from the poles.");
  }
  if (mode === "point") {
    if (points.length !== 1) throw new Error("Choose one point on the map.");
    return { type: "Point", coordinates: points[0] };
  }
  if (mode === "rectangle" && points.length !== 2) throw new Error("Choose two opposite rectangle corners.");
  const vertices = boundaryVertices(mode, points);
  if (vertices.length < 3) throw new Error("Choose at least three vertices.");
  if (new Set(vertices.map((p) => p.join(","))).size !== vertices.length) throw new Error("Remove repeated vertices before finishing.");
  if (Math.max(...vertices.map((p) => p[0])) - Math.min(...vertices.map((p) => p[0])) >= 180) {
    throw new Error("Draw a local site on one side of the antimeridian.");
  }
  for (let i = 0; i < vertices.length; i++) {
    for (let j = i + 2; j < vertices.length; j++) {
      if (i === 0 && j === vertices.length - 1) continue;
      if (intersects(vertices[i], vertices[(i + 1) % vertices.length], vertices[j], vertices[(j + 1) % vertices.length])) {
        throw new Error("Boundary edges cross or touch. Undo a vertex and redraw.");
      }
    }
  }
  if (boundaryArea(vertices) < 1) throw new Error("Draw a boundary enclosing at least one square metre.");
  const signedArea = vertices.reduce((sum, a, i) => {
    const b = vertices[(i + 1) % vertices.length];
    return sum + a[0] * b[1] - b[0] * a[1];
  }, 0);
  const oriented = signedArea < 0 ? [...vertices].reverse() : vertices;
  const geometry: GeoJSON.Polygon = { type: "Polygon", coordinates: [[...oriented, oriented[0]]] };
  if (new TextEncoder().encode(JSON.stringify(geometry)).byteLength > MAX_BOUNDARY_BYTES) throw new Error("Boundary is too large. Remove vertices and try again.");
  return geometry;
}

export function boundaryPreview(mode: BoundaryMode, points: BoundaryPoint[], next: BoundaryPoint | null): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = points.map((point, i) => ({ type: "Feature", properties: { first: i === 0 }, geometry: { type: "Point", coordinates: point } }));
  const preview = next && (mode === "polygon" || points.length < (mode === "point" ? 1 : 2)) ? [...points, next] : points;
  const vertices = boundaryVertices(mode, preview);
  if (mode !== "point" && vertices.length >= 2) features.unshift({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: vertices.length >= 3 ? [...vertices, vertices[0]] : vertices } });
  if (mode !== "point" && vertices.length >= 3) features.unshift({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [[...vertices, vertices[0]]] } });
  if (next) features.push({ type: "Feature", properties: { next: true }, geometry: { type: "Point", coordinates: next } });
  return { type: "FeatureCollection", features };
}
