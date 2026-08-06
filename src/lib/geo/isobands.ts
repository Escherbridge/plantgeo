/**
 * Marching squares over a regular lattice of scalar samples, producing dissolved isobands.
 *
 * Runs on the SERVER, over the small node grid `geo.soil_moisture_field` already averaged
 * and Gaussian-smoothed in SQL -- see `src/lib/geo/AGENTS.md` §isobands for why the
 * contouring is here rather than in PostGIS (`ST_Contour` needs `postgis_raster`, which is
 * available but not installed) and why it is not in the browser (the repo rule is that
 * geospatial aggregation goes through PostGIS, and shipping the grid would defeat the point).
 */

/** One sample of the aggregated field, at a lattice node. */
export interface FieldSample {
  lon: number;
  lat: number;
  value: number;
}

/** A half-open value interval and the dissolved geometry whose samples fall inside it. */
export interface Isoband {
  bandIndex: number;
  /** Inclusive lower bound; null for the open tail below the first break. */
  minimum: number | null;
  /** Inclusive upper bound; null for the open tail above the last break. */
  maximum: number | null;
  /** GeoJSON Polygon coordinate arrays: `[exteriorRing, ...holes]` each, closed. */
  polygons: number[][][][];
}

/** A lattice node, addressed by integer grid indices so edges have a canonical order. */
interface LatticeNode {
  /** Unique and monotonic; the tie-break that makes an edge's two endpoints ordered. */
  index: number;
  column: number;
  row: number;
  lon: number;
  lat: number;
  value: number;
}

/** An edge of the triangulation, always stored lower-index-first. */
interface LatticeEdge {
  from: LatticeNode;
  to: LatticeNode;
  key: string;
}

/**
 * A vertex of a clipped triangle. It is either a lattice node (`corner`) or a crossing that
 * lies on one original triangle edge (`edge`). Carrying that provenance is what makes the
 * dissolve exact: two triangles sharing an edge compute their crossing on it from the SAME
 * ordered endpoints, so the two coordinates are bit-identical and cancel.
 */
interface ClipVertex {
  lon: number;
  lat: number;
  value: number;
  corner: LatticeNode | null;
  edge: LatticeEdge | null;
}

/** A boundary segment of a band, oriented so the filled area is on its left. */
interface DirectedEdge {
  fromKey: string;
  toKey: string;
  fromLon: number;
  fromLat: number;
  toLon: number;
  toLat: number;
}

/** Rings smaller than this in square degrees are numerical dust, not geometry. */
const MINIMUM_RING_AREA_SQUARE_DEGREES = 1e-12;

/** A ring that has not closed after this many steps is malformed; the caller falls back. */
const MAX_RING_LENGTH = 100_000;

/** Exact key for a coordinate pair. Shared crossings are bit-identical, so no rounding. */
function pointKey(lon: number, lat: number): string {
  return `${lon},${lat}`;
}

function edgeBetween(a: LatticeNode, b: LatticeNode): LatticeEdge {
  const [from, to] = a.index < b.index ? [a, b] : [b, a];
  return { from, to, key: `${from.index}-${to.index}` };
}

/** Where an edge crosses `threshold`. Deterministic per (edge, threshold) by construction. */
function crossingVertex(edge: LatticeEdge, threshold: number): ClipVertex {
  const span = edge.to.value - edge.from.value;
  const fraction = span === 0 ? 0 : (threshold - edge.from.value) / span;
  return {
    lon: edge.from.lon + fraction * (edge.to.lon - edge.from.lon),
    lat: edge.from.lat + fraction * (edge.to.lat - edge.from.lat),
    value: threshold,
    corner: null,
    edge,
  };
}

/**
 * The original triangle edge a clipped segment lies along, or null when the segment runs
 * along a clip line through the triangle's interior.
 *
 * Only the first case can be crossed again by the second clip, and only that case has to
 * agree with the neighbouring triangle -- which is exactly why it is resolved back to the
 * original edge instead of interpolating between the two clipped vertices.
 */
function segmentOriginalEdge(first: ClipVertex, second: ClipVertex): LatticeEdge | null {
  if (first.corner !== null && second.corner !== null) {
    return edgeBetween(first.corner, second.corner);
  }
  if (first.corner !== null && second.edge !== null) {
    const onEdge =
      second.edge.from.index === first.corner.index || second.edge.to.index === first.corner.index;
    return onEdge ? second.edge : null;
  }
  if (first.edge !== null && second.corner !== null) {
    const onEdge =
      first.edge.from.index === second.corner.index || first.edge.to.index === second.corner.index;
    return onEdge ? first.edge : null;
  }
  if (first.edge !== null && second.edge !== null && first.edge.key === second.edge.key) {
    return first.edge;
  }
  return null;
}

/** Straight interpolation, used only for interior segments nothing else has to agree with. */
function interiorCrossing(first: ClipVertex, second: ClipVertex, threshold: number): ClipVertex {
  const span = second.value - first.value;
  const fraction = span === 0 ? 0 : (threshold - first.value) / span;
  return {
    lon: first.lon + fraction * (second.lon - first.lon),
    lat: first.lat + fraction * (second.lat - first.lat),
    value: threshold,
    corner: null,
    edge: null,
  };
}

/** Sutherland-Hodgman against one value half-plane, keeping the original-edge provenance. */
function clipByThreshold(
  polygon: ClipVertex[],
  threshold: number,
  keepAtOrAbove: boolean
): ClipVertex[] {
  if (polygon.length === 0) return polygon;
  const isInside = (vertex: ClipVertex) =>
    keepAtOrAbove ? vertex.value >= threshold : vertex.value <= threshold;

  const clipped: ClipVertex[] = [];
  for (let position = 0; position < polygon.length; position += 1) {
    const current = polygon[position];
    const next = polygon[(position + 1) % polygon.length];
    const currentInside = isInside(current);
    if (currentInside) clipped.push(current);
    if (currentInside === isInside(next)) continue;
    const originalEdge = segmentOriginalEdge(current, next);
    clipped.push(
      originalEdge === null
        ? interiorCrossing(current, next, threshold)
        : crossingVertex(originalEdge, threshold)
    );
  }
  return clipped;
}

function signedArea(ring: { lon: number; lat: number }[]): number {
  let total = 0;
  for (let position = 0; position < ring.length; position += 1) {
    const current = ring[position];
    const next = ring[(position + 1) % ring.length];
    total += current.lon * next.lat - next.lon * current.lat;
  }
  return total / 2;
}

/** Directed boundary edges of one clipped piece, oriented counter-clockwise. */
function directedEdgesOf(piece: ClipVertex[]): DirectedEdge[] {
  const ring = signedArea(piece) < 0 ? [...piece].reverse() : piece;
  const edges: DirectedEdge[] = [];
  for (let position = 0; position < ring.length; position += 1) {
    const current = ring[position];
    const next = ring[(position + 1) % ring.length];
    if (current.lon === next.lon && current.lat === next.lat) continue;
    edges.push({
      fromKey: pointKey(current.lon, current.lat),
      toKey: pointKey(next.lon, next.lat),
      fromLon: current.lon,
      fromLat: current.lat,
      toLon: next.lon,
      toLat: next.lat,
    });
  }
  return edges;
}

/**
 * Cancels every pair of opposite directed edges, leaving only the outer boundary of the
 * union. Exact because the pieces are vertex-conforming: an interior edge is traversed once
 * in each direction by the two pieces that share it.
 */
function cancelSharedEdges(edges: DirectedEdge[]): DirectedEdge[] {
  const pending = new Map<string, DirectedEdge[]>();
  const survivors: DirectedEdge[] = [];
  for (const edge of edges) {
    const oppositeKey = `${edge.toKey}|${edge.fromKey}`;
    const waiting = pending.get(oppositeKey);
    if (waiting !== undefined && waiting.length > 0) {
      waiting.pop();
      if (waiting.length === 0) pending.delete(oppositeKey);
      continue;
    }
    const ownKey = `${edge.fromKey}|${edge.toKey}`;
    const bucket = pending.get(ownKey);
    if (bucket === undefined) pending.set(ownKey, [edge]);
    else bucket.push(edge);
  }
  for (const bucket of pending.values()) survivors.push(...bucket);
  return survivors;
}

/** Angle in [0, 2*PI) measured clockwise from `fromAngle` to `toAngle`. */
function clockwiseTurn(fromAngle: number, toAngle: number): number {
  const turn = fromAngle - toAngle;
  const wrapped = turn % (2 * Math.PI);
  return wrapped < 0 ? wrapped + 2 * Math.PI : wrapped;
}

/**
 * Chains surviving boundary edges into closed rings.
 *
 * At an ordinary vertex exactly one edge leaves, and the walk is unambiguous. At a pinch --
 * where the band touches itself at a single point -- several do, and the next edge is the
 * first one clockwise from the way we came, which is the standard planar face traversal and
 * the only choice that cannot produce a self-crossing ring.
 *
 * Returns null rather than a wrong answer if a ring fails to close, so the caller can fall
 * back to the undissolved pieces instead of dropping geometry.
 */
function chainRings(edges: DirectedEdge[]): { lon: number; lat: number }[][] | null {
  const outgoing = new Map<string, DirectedEdge[]>();
  for (const edge of edges) {
    const bucket = outgoing.get(edge.fromKey);
    if (bucket === undefined) outgoing.set(edge.fromKey, [edge]);
    else bucket.push(edge);
  }
  const unused = new Set(edges);
  const rings: { lon: number; lat: number }[][] = [];

  for (const seed of edges) {
    if (!unused.has(seed)) continue;
    const ring: { lon: number; lat: number }[] = [{ lon: seed.fromLon, lat: seed.fromLat }];
    let current = seed;
    unused.delete(current);
    let steps = 0;

    while (current.toKey !== seed.fromKey) {
      ring.push({ lon: current.toLon, lat: current.toLat });
      const candidates = (outgoing.get(current.toKey) ?? []).filter((edge) => unused.has(edge));
      if (candidates.length === 0) return null;
      if (steps > MAX_RING_LENGTH) return null;
      let chosen = candidates[0];
      if (candidates.length > 1) {
        const arrivalAngle = Math.atan2(
          current.fromLat - current.toLat,
          current.fromLon - current.toLon
        );
        let smallestTurn = Number.POSITIVE_INFINITY;
        for (const candidate of candidates) {
          const departureAngle = Math.atan2(
            candidate.toLat - candidate.fromLat,
            candidate.toLon - candidate.fromLon
          );
          const turn = clockwiseTurn(arrivalAngle, departureAngle);
          if (turn < smallestTurn) {
            smallestTurn = turn;
            chosen = candidate;
          }
        }
      }
      unused.delete(chosen);
      current = chosen;
      steps += 1;
    }
    if (ring.length >= 3) rings.push(ring);
  }
  return rings;
}

/** Ray-casting containment, used only to nest holes inside the ring that owns them. */
function ringContainsPoint(ring: { lon: number; lat: number }[], lon: number, lat: number): boolean {
  let inside = false;
  for (let position = 0, previous = ring.length - 1; position < ring.length; previous = position, position += 1) {
    const current = ring[position];
    const other = ring[previous];
    const straddles = current.lat > lat !== other.lat > lat;
    if (!straddles) continue;
    const crossingLon =
      ((other.lon - current.lon) * (lat - current.lat)) / (other.lat - current.lat) + current.lon;
    if (lon < crossingLon) inside = !inside;
  }
  return inside;
}

/**
 * Closes a ring and emits it in GeoJSON position order, dropping vertices that are EXACTLY
 * collinear with their neighbours.
 *
 * Exactly, by a zero cross product -- never by a tolerance. A dissolved band runs along
 * lattice nodes wherever its edge is straight (most obviously along the viewport's own
 * boundary), so a plain trace carries a vertex every 0.25 degrees down an edge that needs
 * two. Removing only exact collinearity cannot move the boundary by even one ulp, which a
 * tolerance-based simplifier could.
 */
function closedRing(ring: { lon: number; lat: number }[]): number[][] {
  const kept: { lon: number; lat: number }[] = [];
  for (let position = 0; position < ring.length; position += 1) {
    const previous = ring[(position - 1 + ring.length) % ring.length];
    const current = ring[position];
    const next = ring[(position + 1) % ring.length];
    const cross =
      (current.lon - previous.lon) * (next.lat - previous.lat) -
      (current.lat - previous.lat) * (next.lon - previous.lon);
    if (cross !== 0) kept.push(current);
  }
  // Fewer than three survivors means the whole ring was one straight line, which has no
  // area to draw; keep the traced vertices rather than emitting an unclosable ring.
  const simplified = kept.length >= 3 ? kept : ring;
  const positions = simplified.map((point) => [point.lon, point.lat]);
  positions.push([simplified[0].lon, simplified[0].lat]);
  return positions;
}

/** Groups traced rings into GeoJSON polygons, nesting each hole in its smallest container. */
function assemblePolygons(rings: { lon: number; lat: number }[][]): number[][][][] {
  const outers: { ring: { lon: number; lat: number }[]; area: number; holes: number[][][] }[] = [];
  const holes: { lon: number; lat: number }[][] = [];

  for (const ring of rings) {
    const area = signedArea(ring);
    if (Math.abs(area) < MINIMUM_RING_AREA_SQUARE_DEGREES) continue;
    if (area > 0) outers.push({ ring, area, holes: [] });
    else holes.push(ring);
  }

  for (const hole of holes) {
    let smallest: (typeof outers)[number] | null = null;
    for (const outer of outers) {
      if (!ringContainsPoint(outer.ring, hole[0].lon, hole[0].lat)) continue;
      if (smallest === null || outer.area < smallest.area) smallest = outer;
    }
    // An unattached hole means the trace disagreed with itself; dropping it would silently
    // fill ground the field says is a different band, so it becomes its own ring instead.
    if (smallest === null) outers.push({ ring: [...hole].reverse(), area: -signedArea(hole), holes: [] });
    else smallest.holes.push(closedRing(hole));
  }

  return outers.map((outer) => [closedRing(outer.ring), ...outer.holes]);
}

/** The two triangles of one lattice square, sharing the same diagonal in every square. */
function squareTriangles(
  bottomLeft: LatticeNode,
  bottomRight: LatticeNode,
  topRight: LatticeNode,
  topLeft: LatticeNode
): LatticeNode[][] {
  return [
    [bottomLeft, bottomRight, topRight],
    [bottomLeft, topRight, topLeft],
  ];
}

/**
 * Builds dissolved isobands from an aggregated lattice.
 *
 * `breaks` must be ascending; N breaks produce N+1 bands, the first and last open-ended so
 * no sample can fall outside the classification. A lattice square with any missing corner is
 * skipped entirely: absent coverage is left blank rather than interpolated across, which is
 * the same honest-gap rule the rest of the read model follows.
 */
export function buildIsobands(
  samples: FieldSample[],
  stepDegrees: number,
  breaks: number[]
): Isoband[] {
  if (samples.length === 0 || !(stepDegrees > 0) || breaks.length === 0) return [];

  const originLon = Math.min(...samples.map((sample) => sample.lon));
  const originLat = Math.min(...samples.map((sample) => sample.lat));
  const byGridKey = new Map<string, LatticeNode>();
  let maxColumn = 0;
  let maxRow = 0;
  samples.forEach((sample, ordinal) => {
    const column = Math.round((sample.lon - originLon) / stepDegrees);
    const row = Math.round((sample.lat - originLat) / stepDegrees);
    maxColumn = Math.max(maxColumn, column);
    maxRow = Math.max(maxRow, row);
    byGridKey.set(`${column}:${row}`, {
      index: ordinal,
      column,
      row,
      lon: sample.lon,
      lat: sample.lat,
      value: sample.value,
    });
  });

  const triangles: LatticeNode[][] = [];
  for (let column = 0; column < maxColumn; column += 1) {
    for (let row = 0; row < maxRow; row += 1) {
      const bottomLeft = byGridKey.get(`${column}:${row}`);
      const bottomRight = byGridKey.get(`${column + 1}:${row}`);
      const topRight = byGridKey.get(`${column + 1}:${row + 1}`);
      const topLeft = byGridKey.get(`${column}:${row + 1}`);
      if (!bottomLeft || !bottomRight || !topRight || !topLeft) continue;
      triangles.push(...squareTriangles(bottomLeft, bottomRight, topRight, topLeft));
    }
  }
  if (triangles.length === 0) return [];

  const ascendingBreaks = [...breaks].sort((left, right) => left - right);
  const bands: Isoband[] = [];

  for (let bandIndex = 0; bandIndex <= ascendingBreaks.length; bandIndex += 1) {
    const minimum = bandIndex === 0 ? null : ascendingBreaks[bandIndex - 1];
    const maximum = bandIndex === ascendingBreaks.length ? null : ascendingBreaks[bandIndex];

    const pieces: ClipVertex[][] = [];
    for (const triangle of triangles) {
      let polygon: ClipVertex[] = triangle.map((node) => ({
        lon: node.lon,
        lat: node.lat,
        value: node.value,
        corner: node,
        edge: null,
      }));
      if (minimum !== null) polygon = clipByThreshold(polygon, minimum, true);
      if (maximum !== null) polygon = clipByThreshold(polygon, maximum, false);
      if (polygon.length < 3) continue;
      if (Math.abs(signedArea(polygon)) < MINIMUM_RING_AREA_SQUARE_DEGREES) continue;
      pieces.push(polygon);
    }
    if (pieces.length === 0) continue;

    const traced = chainRings(cancelSharedEdges(pieces.flatMap(directedEdgesOf)));
    const polygons =
      traced === null
        ? // Dissolve failed on this band only. Emitting the undissolved pieces keeps the
          // map truthful at the cost of extra features; losing the band would not.
          pieces.map((piece) => [closedRing(signedArea(piece) < 0 ? [...piece].reverse() : piece)])
        : assemblePolygons(traced);
    if (polygons.length === 0) continue;

    bands.push({ bandIndex, minimum, maximum, polygons });
  }

  return bands;
}
