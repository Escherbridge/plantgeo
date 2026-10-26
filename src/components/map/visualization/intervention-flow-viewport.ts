import { canonicalViewportBbox } from "@/lib/map/viewport-bbox";

/** The tuple shape `visualizationRouter`'s `viewportQuerySchema.bbox` accepts: west, south, east, north. */
export type FlowBboxTuple = [number, number, number, number];

/**
 * Parses the "west,south,east,north" string `useViewportBounds` reports into the tuple
 * `visualization.getFlowData` accepts, or null when the viewport cannot be expressed as one.
 *
 * `canonicalViewportBbox` already enforces the same ordering/range refinement the router's
 * `viewportQuerySchema` applies server-side (west < east, south < north, each coordinate in
 * range) and normalizes a wrapped longitude -- so an unparseable or degenerate viewport is
 * caught here, on the client, rather than round-tripping to a BAD_REQUEST.
 */
export function bboxTupleForFlowQuery(bbox: string | null): FlowBboxTuple | null {
  if (bbox === null) return null;
  const canonical = canonicalViewportBbox(bbox);
  if (canonical === null) return null;
  const parts = canonical.split(",").map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isFinite(part))) return null;
  return parts as FlowBboxTuple;
}
