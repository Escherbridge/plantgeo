/**
 * Attaches a server-decoded GeoJSON geometry to each boundary result the
 * `landContext` tRPC router returns to the map.
 *
 * Why here and not in the map-facing hook: `useLandContextQuery.ts` lives
 * under `src/components/`, which `scripts/check-client-server-imports.mjs`
 * treats as an always-browser surface with no runtime import from
 * `@/lib/server/**` -- so `decodeBoundaryGeometry` cannot be called there.
 * The frozen contract (`src/lib/environmental/land-context-contract.ts`)
 * carries only the hex WKB; this module decodes it once, on the server, and
 * the router ships the result alongside the untouched contract fields.
 *
 * Null-vs-throw: a `null` `geometryWkb` decodes to `null` (a stated "no
 * geometry" gap upstream, never an invented shape). Malformed-but-present WKB
 * throws `WkbDecodeError` from the decoder; that is a real data defect, so it
 * is NOT swallowed silently -- the geometry is nulled and the defect is
 * appended to `unresolvedGaps`, which keeps the feature listable while the
 * response says exactly what went wrong. Any other error propagates.
 *
 * Wire budget: `reader.ts` checks `MAX_RESPONSE_BYTES` against results that
 * carry the hex WKB. Shipping the decoded GeoJSON beside that hex would
 * roughly double the payload AFTER the check, voiding the budget the day a
 * lane lands. So once a decode has been attempted the hex is stripped
 * (`sourceFeature.geometryWkb: null`, legal under the contract's
 * `string | null`): on the wire, geometry travels as GeoJSON only, in the
 * same order of bytes the reader budgeted. The `geometry` field beside it is
 * what says whether a shape existed; a null hex is not, by itself, "no data".
 */

import type { BoundedResponse, LandContextResult } from "../types";
import { decodeBoundaryGeometry } from "./boundary-geometry-adapter";
import { WkbDecodeError } from "./wkb-decode-error";

/** A contract result plus the decoded boundary geometry the map draws (null when the source carried none). */
export type LandContextResultWithGeometry = LandContextResult & {
  geometry: GeoJSON.Geometry | null;
};

export function attachDecodedGeometry(result: LandContextResult): LandContextResultWithGeometry {
  if (!result.sourceFeature) {
    return { ...result, geometry: null };
  }
  const sourceFeature = { ...result.sourceFeature, geometryWkb: null };
  try {
    return { ...result, sourceFeature, geometry: decodeBoundaryGeometry(result.sourceFeature) };
  } catch (error) {
    if (error instanceof WkbDecodeError) {
      return {
        ...result,
        sourceFeature,
        geometry: null,
        unresolvedGaps: [
          ...result.unresolvedGaps,
          `boundary geometry present but undecodable (${error.reason}): ${error.message}`,
        ],
      };
    }
    throw error;
  }
}

export function attachDecodedGeometries(
  response: BoundedResponse<LandContextResult[]>
): BoundedResponse<LandContextResultWithGeometry[]> {
  if (response.status !== "ok") return response;
  return { status: "ok", data: response.data.map(attachDecodedGeometry) };
}

export function attachDecodedGeometryToOne(
  response: BoundedResponse<LandContextResult>
): BoundedResponse<LandContextResultWithGeometry> {
  if (response.status !== "ok") return response;
  return { status: "ok", data: attachDecodedGeometry(response.data) };
}
