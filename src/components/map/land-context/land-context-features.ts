/**
 * The ONE decode from a land-context boundary response to drawable `LandContextFeature`s.
 *
 * Extracted out of `useLandContextQuery.ts` unchanged on 2026-09-18 so the automatic viewport
 * lane (`useLandContextViewportBoundaries`) decodes through the same `toFeature`/`toResults`
 * path the click lane uses. Two lanes reading one response shape must not grow two decoders:
 * a second copy is a second place for "a non-matched result never becomes a feature" to drift.
 *
 * Rationale: see `src/components/map/AGENTS.md` section "The land-context viewport lane".
 */

import type { LandContextResult, CoverageState } from "@/lib/environmental/land-context-contract";
import {
  type LandContextFeature,
  type LandContextGroupId,
  type LandContextResultMeta,
} from "@/stores/land-context-store";

/**
 * What the boundary procedures actually return: the frozen contract result plus the boundary
 * geometry the router decoded server-side (`attachDecodedGeometry` in
 * `src/lib/server/services/land-context/geometry/`).
 *
 * Declared structurally rather than imported, because this file is browser code and may not
 * import from `@/lib/server/**` (`scripts/check-client-server-imports.mjs`); assigning the
 * inferred tRPC output to this type at each call site is what keeps the two in step at compile
 * time.
 */
export type BoundaryResult = LandContextResult & { geometry: GeoJSON.Geometry | null };

/**
 * The store's `geometry` is non-nullable, so a source that carried no geometry is represented by
 * an EMPTY GeometryCollection: MapLibre draws nothing for it, the accessible list and the panel
 * still list it, and no shape the source did not provide is ever fabricated.
 */
export const NO_GEOMETRY: GeoJSON.GeometryCollection = { type: "GeometryCollection", geometries: [] };

const FAMILY_TO_GROUP: Record<string, LandContextGroupId> = {
  parcel: "parcels-land-use",
  land_use: "parcels-land-use",
  electric_service_territory: "electric-utility-territories",
  blm_surface_management: "blm-lands",
  state_managed_land: "state-managed-lands",
};

export function groupForResult(result: LandContextResult): LandContextGroupId | null {
  const familyType = result.sourceFeature?.familyType;
  if (!familyType) return null;
  return FAMILY_TO_GROUP[familyType] ?? null;
}

/** A drawable feature, or null for every result that is a coverage statement rather than a match. */
export function toFeature(result: BoundaryResult, index: number): LandContextFeature | null {
  if (result.coverageState !== "matched" || !result.sourceFeature) return null;
  const group = groupForResult(result);
  if (!group) return null;

  return {
    id: `${result.sourceFeature.sourceNamespace}:${result.sourceFeature.nativeFeatureKey}:${index}`,
    group,
    title: result.organizationOffice?.officialPublicName ?? result.sourceFeature.nativeFeatureKey,
    category: result.sourceFeature.interestType,
    sourceVintage: result.sourceRelease?.sourceVersion,
    contactRouteSummary: result.documentedHelp ?? undefined,
    geometry: result.geometry ?? NO_GEOMETRY,
    contactVerified: result.route?.status === "active",
  };
}

/** Every matched result whose group the reader has switched on, in response order. */
export function toResults(
  results: BoundaryResult[],
  enabledGroups: Record<LandContextGroupId, boolean>
): LandContextFeature[] {
  return results
    .map((result, index) => toFeature(result, index))
    .filter((feature): feature is LandContextFeature => feature !== null && enabledGroups[feature.group]);
}

/** Every non-matched result, kept as a typed coverage statement with its verbatim gap strings. */
export function toCoverageNotices(
  results: BoundaryResult[]
): NonNullable<LandContextResultMeta["coverageNotices"]> {
  return results
    .filter((result) => result.coverageState !== "matched")
    .map((result) => ({ coverageState: result.coverageState, gaps: result.unresolvedGaps }));
}

/** The distinct coverage states a response stated, deduplicated, in first-seen order. */
export function statedCoverageStates(results: BoundaryResult[]): CoverageState[] {
  const seen: CoverageState[] = [];
  for (const result of results) {
    if (!seen.includes(result.coverageState)) seen.push(result.coverageState);
  }
  return seen;
}
