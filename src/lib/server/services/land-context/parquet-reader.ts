/**
 * PLACEHOLDER Parquet reader for the land-context reference plane: real pruning shape, always an
 * empty-with-gap-stated result, because no admitted lane is wired in.
 *
 * A `no_match` or `unknown_coverage` from here is NOT a coverage finding.
 * TODO(lane owner): replace these bodies with real Parquet reads, keeping the
 * pruning-then-intersection call shape. See `src/lib/server/services/land-context/AGENTS.md`.
 */

import type {
  BoundaryVersionRef,
  OrganizationOfficeRef,
  OverlapBasis,
  ParcelKey,
  PlaceOfficeTopicRelationshipRef,
  PublicContactRouteRef,
  SourceReleaseRef,
} from "./types";
import { decodeBoundaryGeometry } from "./geometry/boundary-geometry-adapter";

// Re-exported so callers of this reader (and its eventual real Parquet-lane
// replacement) can decode `CandidateBoundaryFeature.boundary.geometryWkb`
// without importing from `./geometry` directly. See
// `./geometry/boundary-geometry-adapter.ts` for the null-vs-throw contract:
// a `null` `geometryWkb` decodes to `null`, malformed-but-present WKB
// throws `WkbDecodeError`.
export { decodeBoundaryGeometry };

export interface BboxDegrees {
  west: number;
  south: number;
  east: number;
  north: number;
}

export interface CandidateBoundaryFeature {
  boundary: BoundaryVersionRef;
  overlapBasis: OverlapBasis;
  sourceRelease: SourceReleaseRef;
}

/**
 * Step 1 of the two-phase spatial read: cheap bbox/row-group pruning against
 * whatever index the underlying Parquet lane exposes (partition pruning,
 * row-group stats, etc). Returns candidate feature keys only — never a
 * legal/authoritative determination on its own, per the spec:
 * "Intersection finds candidate reported features, not legal proof."
 *
 * PLACEHOLDER: always returns no candidates, since no lane is wired in.
 */
export async function pruneCandidatesByBbox(
  _bbox: BboxDegrees
): Promise<{ candidateKeys: string[]; gap: string }> {
  return {
    candidateKeys: [],
    gap: "no Parquet lane wired in yet; reference plane not yet admitted for reads",
  };
}

/**
 * Step 2: exact intersection against the pruned candidate set. Only ever
 * called with candidates already narrowed by `pruneCandidatesByBbox` or a
 * point-containment equivalent; never runs an unbounded full-table scan.
 *
 * PLACEHOLDER: always returns no matches, with the same stated gap.
 */
export async function exactIntersectCandidates(
  _candidateKeys: string[],
  _bbox: BboxDegrees
): Promise<{ features: CandidateBoundaryFeature[]; gap: string }> {
  return {
    features: [],
    gap: "no Parquet lane wired in yet; reference plane not yet admitted for reads",
  };
}

/**
 * Point-containment read. Structured the same two-phase way: bbox pruning
 * of the point's containing cell first, exact polygon-contains second.
 *
 * PLACEHOLDER: always returns no matches, with the stated gap.
 */
export async function findContainingFeatures(
  _lon: number,
  _lat: number
): Promise<{ features: CandidateBoundaryFeature[]; gap: string }> {
  return {
    features: [],
    gap: "no Parquet lane wired in yet; reference plane not yet admitted for reads",
  };
}

/**
 * Looks up a validated parcel key directly (no spatial pruning needed since
 * the key is already resolved).
 *
 * PLACEHOLDER: always returns null with the stated gap.
 */
export async function findBoundaryByParcelKey(
  _key: ParcelKey
): Promise<{ feature: CandidateBoundaryFeature | null; gap: string }> {
  return {
    feature: null,
    gap: "no Parquet lane wired in yet; reference plane not yet admitted for reads",
  };
}

/**
 * Relationship/contact lookup by validated subject ID and/or topic.
 *
 * PLACEHOLDER: always returns no relationships and no routes, with the
 * stated gap.
 */
export async function findRelationshipsAndRoutes(
  _subjectId: string,
  _topic: string | null
): Promise<{
  relationships: PlaceOfficeTopicRelationshipRef[];
  offices: OrganizationOfficeRef[];
  routes: PublicContactRouteRef[];
  gap: string;
}> {
  return {
    relationships: [],
    offices: [],
    routes: [],
    gap: "no Parquet lane wired in yet; reference plane not yet admitted for reads",
  };
}

/**
 * Coverage status for a given state/county, independent of any specific
 * feature lookup.
 *
 * PLACEHOLDER: always reports unknown coverage.
 */
export async function readCoverageStatus(
  _state: string,
  _county: string | null
): Promise<{ covered: boolean | null; gap: string }> {
  return {
    covered: null,
    gap: "no Parquet lane wired in yet; coverage census not yet published for this pilot",
  };
}
