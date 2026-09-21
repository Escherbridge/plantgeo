/**
 * Adapter between the land-context reader's `BoundaryVersionRef` shape and
 * the pure WKB decoder in `./decode-wkb.ts`.
 *
 * `BoundaryVersionRef` (in `../types.ts`) carries `geometryWkb: string |
 * null` — a hex-encoded WKB/EWKB string mirroring how `boundaryVersions.geom`
 * (`src/lib/server/db/schema/land-context/boundary-versions.ts`) round-trips
 * through the `spatialGeometry` customType, or `null` when no geometry is
 * available for this reference (e.g. the current Parquet-reader placeholder
 * in `../parquet-reader.ts`, which has no lane wired in yet).
 */

import { decodeWkb } from "./decode-wkb";
import type { BoundaryVersionRef } from "../types";
import { z } from "zod";

const position = z.array(z.number().finite()).min(2);
const rings = z.array(z.array(position).min(4));
const polygonal = z.discriminatedUnion("type", [
  z.object({ type: z.literal("Polygon"), coordinates: rings }),
  z.object({ type: z.literal("MultiPolygon"), coordinates: z.array(rings) }),
]);

/** Decode the warehouse's spatial projection without replacing its clipped geometry. */
export function decodePublishedGeometry(value: unknown): GeoJSON.Polygon | GeoJSON.MultiPolygon | null {
  if (value == null) return null;
  return polygonal.parse(typeof value === "string" ? JSON.parse(value) : value);
}

/**
 * Decodes `ref.geometryWkb` into a GeoJSON geometry.
 *
 * Returns `null` — never throws — when `geometryWkb` is `null`, matching
 * this codebase's gap-not-silent-failure convention (a stated "no data"
 * gap upstream, not a fabricated empty geometry). Malformed-but-present WKB
 * still throws `WkbDecodeError` from `decodeWkb`, since that is a real data
 * defect distinct from "no data".
 */
export function decodeBoundaryGeometry(ref: BoundaryVersionRef): GeoJSON.Geometry | null {
  if (ref.geometry !== undefined) return ref.geometry;
  if (ref.geometryWkb === null) {
    return null;
  }
  return decodeWkb(ref.geometryWkb);
}
