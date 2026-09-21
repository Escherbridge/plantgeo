type Polygonal = GeoJSON.Polygon | GeoJSON.MultiPolygon;
type Bbox = { west: number; south: number; east: number; north: number };

function onSegment(point: GeoJSON.Position, a: GeoJSON.Position, b: GeoJSON.Position): boolean {
  const cross = (point[0] - a[0]) * (b[1] - a[1]) - (point[1] - a[1]) * (b[0] - a[0]);
  return Math.abs(cross) < 1e-12 && point[0] >= Math.min(a[0], b[0]) &&
    point[0] <= Math.max(a[0], b[0]) && point[1] >= Math.min(a[1], b[1]) && point[1] <= Math.max(a[1], b[1]);
}

// Even-odd ray casting, with boundary points handled before parity.
function inRing(point: GeoJSON.Position, ring: GeoJSON.Position[]): "inside" | "outside" | "boundary" {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[j];
    const b = ring[i];
    if (onSegment(point, a, b)) return "boundary";
    if ((a[1] > point[1]) !== (b[1] > point[1]) &&
        point[0] < (b[0] - a[0]) * (point[1] - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside;
  }
  return inside ? "inside" : "outside";
}

function inPolygon(point: GeoJSON.Position, rings: GeoJSON.Position[][]): boolean {
  if (!rings.length) return false;
  const outer = inRing(point, rings[0]);
  if (outer === "outside") return false;
  if (outer === "boundary") return true;
  for (const hole of rings.slice(1)) {
    const location = inRing(point, hole);
    if (location === "boundary") return true;
    if (location === "inside") return false;
  }
  return true;
}

function polygons(geometry: Polygonal): GeoJSON.Position[][][] {
  return geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
}

export function containsBoundaryPoint(geometry: GeoJSON.Geometry | null, point: GeoJSON.Position): boolean | null {
  if (!geometry || (geometry.type !== "Polygon" && geometry.type !== "MultiPolygon")) return null;
  return polygons(geometry).some((rings) => inPolygon(point, rings));
}

// Liang-Barsky clipping: https://en.wikipedia.org/wiki/Liang%E2%80%93Barsky_algorithm
function segmentIntersectsBbox(a: GeoJSON.Position, b: GeoJSON.Position, bbox: Bbox): boolean {
  let low = 0;
  let high = 1;
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const p = [-dx, dx, -dy, dy];
  const q = [a[0] - bbox.west, bbox.east - a[0], a[1] - bbox.south, bbox.north - a[1]];
  for (let i = 0; i < 4; i++) {
    if (p[i] === 0) {
      if (q[i] < 0) return false;
    } else if (p[i] < 0) {
      low = Math.max(low, q[i] / p[i]);
    } else {
      high = Math.min(high, q[i] / p[i]);
    }
    if (low > high) return false;
  }
  return true;
}

export function boundaryIntersectsBbox(geometry: GeoJSON.Geometry | null, bbox: Bbox): boolean | null {
  if (!geometry || (geometry.type !== "Polygon" && geometry.type !== "MultiPolygon")) return null;
  const corners = [[bbox.west, bbox.south], [bbox.east, bbox.south], [bbox.east, bbox.north], [bbox.west, bbox.north]];
  return polygons(geometry).some((rings) => corners.some((point) => inPolygon(point, rings)) ||
    rings.some((ring) => ring.some((point, i) => segmentIntersectsBbox(point, ring[(i + 1) % ring.length], bbox))));
}
