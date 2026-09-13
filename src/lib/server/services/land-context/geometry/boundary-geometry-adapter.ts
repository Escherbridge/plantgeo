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
  if (ref.geometryWkb === null) {
    return null;
  }
  return decodeWkb(ref.geometryWkb);
}
