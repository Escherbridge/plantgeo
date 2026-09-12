import {
  LANE_BASE_LATTICES,
  latticeCellIndex,
  latticeCellSpan,
  servedCellLattice,
  type ServedCellLattice,
  type ZoomTier,
} from "@/lib/map/zoom-tiers";

export type ClimateViewportBounds = readonly [west: number, south: number, east: number, north: number];

/** Validate the climate reader's non-wrapping WGS84 viewport. */
export function climateViewportBounds(bbox: string): ClimateViewportBounds {
  const bounds = bbox.split(",").map(Number);
  if (
    bounds.length !== 4 || bounds.some((value) => !Number.isFinite(value)) ||
    bounds[0] < -180 || bounds[2] > 180 || bounds[1] < -90 || bounds[3] > 90 ||
    bounds[0] >= bounds[2] || bounds[1] >= bounds[3]
  ) {
    throw new TypeError('Invalid bbox: expected positive WGS84 "west,south,east,north" bounds');
  }
  return bounds as [number, number, number, number];
}

/** Include overlapping support and adjacent contour samples; see AGENTS.md §climate viewport support. */
export function climateFieldReadBbox(bbox: string, zoomTier: ZoomTier): string {
  const [west, south, east, north] = climateViewportBounds(bbox);
  const halo = servedCellLattice(zoomTier, LANE_BASE_LATTICES["climate-field"]).cellSizeDegrees;
  return [
    Math.max(-180, west - halo), Math.max(-90, south - halo),
    Math.min(180, east + halo), Math.min(90, north + halo),
  ].map((coordinate) => Number(coordinate.toFixed(6))).join(",");
}

/** Positive-area overlap, excluding a neighboring cell that only touches a viewport edge. */
export function climateBoundsOverlap(cell: ClimateViewportBounds, viewport: ClimateViewportBounds): boolean {
  return cell[0] < viewport[2] && cell[2] > viewport[0] &&
    cell[1] < viewport[3] && cell[3] > viewport[1];
}

/** The footprint of one coordinate served by the climate lattice. */
export function climateCellBounds(
  longitude: number,
  latitude: number,
  lattice: ServedCellLattice
): ClimateViewportBounds {
  const [west, east] = latticeCellSpan(latticeCellIndex(longitude, lattice), lattice);
  const [south, north] = latticeCellSpan(latticeCellIndex(latitude, lattice), lattice);
  return [west, south, east, north];
}

/** Area of a ring inside the viewport, used only for visibility, never to change served geometry. */
function clippedRingArea(ring: GeoJSON.Position[], bounds: ClimateViewportBounds): number {
  let vertices = ring;
  const edges = [[0, bounds[0], true], [0, bounds[2], false], [1, bounds[1], true], [1, bounds[3], false]] as const;
  for (const [axis, edge, keepGreater] of edges) {
    const input = vertices;
    vertices = [];
    if (input.length === 0) return 0;
    let previous = input[input.length - 1];
    let previousInside = keepGreater ? previous[axis] >= edge : previous[axis] <= edge;
    for (const current of input) {
      const currentInside = keepGreater ? current[axis] >= edge : current[axis] <= edge;
      if (currentInside !== previousInside) {
        const ratio = (edge - previous[axis]) / (current[axis] - previous[axis]);
        const intersection = [
          previous[0] + ratio * (current[0] - previous[0]),
          previous[1] + ratio * (current[1] - previous[1]),
        ];
        intersection[axis] = edge;
        vertices.push(intersection);
      }
      if (currentInside) vertices.push(current);
      previous = current;
      previousInside = currentInside;
    }
  }
  let doubledArea = 0;
  for (let index = 0; index < vertices.length; index += 1) {
    const current = vertices[index];
    const next = vertices[(index + 1) % vertices.length];
    doubledArea += (current[0] - bounds[0]) * (next[1] - bounds[1])
      - (next[0] - bounds[0]) * (current[1] - bounds[1]);
  }
  return Math.abs(doubledArea) / 2;
}

/** Require positive polygon area in the actual viewport, including holes and multipart bands. */
export function climateGeometryOverlapsViewport(
  geometry: GeoJSON.Polygon | GeoJSON.MultiPolygon | GeoJSON.Point,
  viewport: ClimateViewportBounds
): boolean {
  if (geometry.type === "Point") return false;
  const polygons = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
  return polygons.some(([outer, ...holes]) => outer !== undefined &&
    clippedRingArea(outer, viewport) > holes.reduce((area, ring) => area + clippedRingArea(ring, viewport), 0)
  );
}
