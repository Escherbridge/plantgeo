/**
 * Bounded readers for the land-context reference plane.
 *
 * Implements the reference-plane spec's "Bounded readers and agent contract"
 * section: point containment, bounded bbox/AOI intersection, and bounded
 * relationship/contact lookup by validated identifier/topic. Every function
 * takes explicit numeric limits and returns a typed `budget_exceeded`
 * response instead of silently truncating — see `./budgets.ts` for the
 * frozen defaults and `./parquet-reader.ts` for the (placeholder) storage
 * layer this module reads through.
 *
 * Two-phase spatial read, per the spec: bbox/row-group pruning first, exact
 * intersection second. Intersection finds candidate reported features, not
 * legal proof — never reduce a selected area to its centroid or the nearest
 * office, and always return every intersecting feature/office found.
 */

import {
  MAX_AOI_AREA_SQUARE_DEGREES,
  MAX_AOI_GEOMETRY_VERTICES,
  MAX_FEATURES_RETURNED,
  MAX_RESPONSE_BYTES,
  PILOT_STATES,
  estimateResponseBytes,
  isWithinAoiAreaBudget,
  isWithinFeatureCountBudget,
  isWithinVertexBudget,
} from "./budgets";
import {
  exactIntersectCandidates,
  findBoundaryByParcelKey,
  findContainingFeatures,
  findRelationshipsAndRoutes,
  pruneCandidatesByBbox,
  readCoverageStatus,
  type BboxDegrees,
} from "./parquet-reader";
import type {
  BoundedResponse,
  BudgetExceededResult,
  LandContextResult,
  ParcelKey,
  PilotState,
} from "./types";

function isPilotState(state: string): state is PilotState {
  return (PILOT_STATES as readonly string[]).includes(state);
}

function budgetExceeded(
  reason: BudgetExceededResult["reason"],
  limit: number,
  requested: number | null
): BudgetExceededResult {
  return { status: "budget_exceeded", reason, limit, requested };
}

function bboxAreaSquareDegrees(bbox: BboxDegrees): number {
  return Math.max(0, bbox.east - bbox.west) * Math.max(0, bbox.north - bbox.south);
}

function emptyResult(
  coverageState: LandContextResult["coverageState"],
  gap: string
): LandContextResult {
  return {
    coverageState,
    sourceFeature: null,
    sourceRelease: null,
    matchedRegionOrOverlap: null,
    organizationOffice: null,
    route: null,
    roleOrRouteType: null,
    assignmentEvidence: null,
    publicContactUrl: null,
    verificationTime: null,
    documentedHelp: null,
    unresolvedGaps: [gap],
    isCurrentReferenceOnly: true,
  };
}

/**
 * Point containment: which admitted boundary/territory features contain a
 * given lon/lat. Returns every containing feature, never just the nearest
 * or a single "winning" one — overlapping jurisdictions are a real, expected
 * outcome the spec requires surfacing, not collapsing.
 */
export async function readPointContainment(
  lon: number,
  lat: number,
  options: { maxFeatures?: number } = {}
): Promise<BoundedResponse<LandContextResult[]>> {
  const maxFeatures = options.maxFeatures ?? MAX_FEATURES_RETURNED;
  if (!isWithinFeatureCountBudget(maxFeatures)) {
    return budgetExceeded("feature_count_would_exceed_limit", MAX_FEATURES_RETURNED, maxFeatures);
  }
  if (lon < -180 || lon > 180 || lat < -90 || lat > 90) {
    return budgetExceeded("aoi_area_exceeds_limit", 0, null);
  }

  const { features, gap } = await findContainingFeatures(lon, lat);

  if (features.length === 0) {
    // The placeholder reader cannot yet distinguish "point is outside every
    // admitted pilot boundary" from "coverage for this point is unknown" —
    // both collapse to unknown_coverage until a real lane is wired in.
    return { status: "ok", data: [emptyResult("unknown_coverage", gap)] };
  }

  const bounded = features.slice(0, maxFeatures);
  if (features.length > maxFeatures) {
    return budgetExceeded("feature_count_would_exceed_limit", maxFeatures, features.length);
  }

  const results: LandContextResult[] = bounded.map((f) => ({
    coverageState: "matched",
    sourceFeature: f.boundary,
    sourceRelease: f.sourceRelease,
    matchedRegionOrOverlap: f.overlapBasis,
    organizationOffice: null,
    route: null,
    roleOrRouteType: null,
    assignmentEvidence: null,
    publicContactUrl: null,
    verificationTime: null,
    documentedHelp: null,
    unresolvedGaps: [],
    isCurrentReferenceOnly: true,
  }));

  const estimatedBytes = estimateResponseBytes(results);
  if (estimatedBytes > MAX_RESPONSE_BYTES) {
    return budgetExceeded("response_bytes_would_exceed_limit", MAX_RESPONSE_BYTES, estimatedBytes);
  }

  return { status: "ok", data: results };
}

/**
 * Bounded bbox/AOI intersection. Prunes by bbox first (row-group/partition
 * level), then runs exact intersection against the pruned candidate set —
 * never an unbounded scan. Returns every intersecting feature and the
 * overlap basis for each; a selected area is never replaced by its centroid.
 */
export async function readBoundedAoiIntersection(
  bbox: BboxDegrees,
  options: { maxFeatures?: number; maxVertices?: number } = {}
): Promise<BoundedResponse<LandContextResult[]>> {
  const maxFeatures = options.maxFeatures ?? MAX_FEATURES_RETURNED;
  const maxVertices = options.maxVertices ?? MAX_AOI_GEOMETRY_VERTICES;

  if (bbox.west >= bbox.east || bbox.south >= bbox.north) {
    return budgetExceeded("aoi_area_exceeds_limit", 0, null);
  }

  const area = bboxAreaSquareDegrees(bbox);
  if (!isWithinAoiAreaBudget(area)) {
    return budgetExceeded("aoi_area_exceeds_limit", MAX_AOI_AREA_SQUARE_DEGREES, area);
  }

  // A bbox has 4 vertices; this check exists for callers that pass a richer
  // polygon AOI in the future through the same budget surface.
  if (!isWithinVertexBudget(4) || maxVertices < 4) {
    return budgetExceeded("geometry_vertices_exceed_limit", MAX_AOI_GEOMETRY_VERTICES, 4);
  }

  const pruned = await pruneCandidatesByBbox(bbox);
  const { features, gap } = await exactIntersectCandidates(pruned.candidateKeys, bbox);

  if (features.length === 0) {
    return {
      status: "ok",
      data: [emptyResult("partial_area_coverage", pruned.gap || gap)],
    };
  }

  if (features.length > maxFeatures) {
    return budgetExceeded("feature_count_would_exceed_limit", maxFeatures, features.length);
  }

  const results: LandContextResult[] = features.map((f) => ({
    coverageState: "matched",
    sourceFeature: f.boundary,
    sourceRelease: f.sourceRelease,
    matchedRegionOrOverlap: f.overlapBasis,
    organizationOffice: null,
    route: null,
    roleOrRouteType: null,
    assignmentEvidence: null,
    publicContactUrl: null,
    verificationTime: null,
    documentedHelp: null,
    unresolvedGaps: [],
    isCurrentReferenceOnly: true,
  }));

  const estimatedBytes = estimateResponseBytes(results);
  if (estimatedBytes > MAX_RESPONSE_BYTES) {
    return budgetExceeded("response_bytes_would_exceed_limit", MAX_RESPONSE_BYTES, estimatedBytes);
  }

  return { status: "ok", data: results };
}

/**
 * Resolves a validated parcel key directly (no spatial pruning required).
 */
export async function readBoundaryByParcelKey(
  key: ParcelKey
): Promise<BoundedResponse<LandContextResult>> {
  if (!isPilotState(key.state)) {
    return budgetExceeded("outside_pilot_states", PILOT_STATES.length, null);
  }
  const { feature, gap } = await findBoundaryByParcelKey(key);
  if (!feature) {
    return { status: "ok", data: emptyResult("no_match_in_proven_coverage", gap) };
  }
  return {
    status: "ok",
    data: {
      coverageState: "matched",
      sourceFeature: feature.boundary,
      sourceRelease: feature.sourceRelease,
      matchedRegionOrOverlap: feature.overlapBasis,
      organizationOffice: null,
      route: null,
      roleOrRouteType: null,
      assignmentEvidence: null,
      publicContactUrl: null,
      verificationTime: null,
      documentedHelp: null,
      unresolvedGaps: [],
      isCurrentReferenceOnly: true,
    },
  };
}

/**
 * Relationship/contact lookup by validated subject ID and optional topic.
 * Returns every applicable office/route, not a single "best" pick, and
 * carries assignment evidence + review status on every relationship so a
 * caller can distinguish a reviewed crosswalk from an unreviewed guess.
 */
export async function readContactsForSubject(
  subjectId: string,
  topic: string | null,
  options: { maxFeatures?: number } = {}
): Promise<BoundedResponse<LandContextResult[]>> {
  const maxFeatures = options.maxFeatures ?? MAX_FEATURES_RETURNED;
  if (!subjectId || subjectId.length === 0) {
    return budgetExceeded("feature_count_would_exceed_limit", maxFeatures, 0);
  }

  const { relationships, offices, routes, gap } = await findRelationshipsAndRoutes(
    subjectId,
    topic
  );

  if (relationships.length === 0) {
    return { status: "ok", data: [emptyResult("unknown_coverage", gap)] };
  }

  if (relationships.length > maxFeatures) {
    return budgetExceeded("feature_count_would_exceed_limit", maxFeatures, relationships.length);
  }

  const officeById = new Map(offices.map((o) => [o.officeId, o]));
  const routesByOffice = new Map<string, typeof routes[number][]>();
  for (const route of routes) {
    const list = routesByOffice.get(route.officeId) ?? [];
    list.push(route);
    routesByOffice.set(route.officeId, list);
  }

  const results: LandContextResult[] = relationships.map((rel) => {
    const office = officeById.get(rel.objectId) ?? null;
    const officeRoutes = office ? routesByOffice.get(office.officeId) ?? [] : [];
    const primaryRoute = officeRoutes[0] ?? null;
    const gaps: string[] = [];
    if (!office) gaps.push("no admitted office record for this relationship's object ID");
    if (!primaryRoute) gaps.push("no admitted public contact route for this office");
    if (rel.reviewStatus !== "reviewed") {
      gaps.push("assignment method is not reviewed; treat as unverified office assignment");
    }

    return {
      coverageState: "matched",
      sourceFeature: null,
      sourceRelease: null,
      matchedRegionOrOverlap: null,
      organizationOffice: office,
      route: primaryRoute,
      roleOrRouteType: primaryRoute?.routeMeaning ?? rel.relationshipKind,
      assignmentEvidence: rel,
      publicContactUrl: primaryRoute?.officialInquiryUrl ?? null,
      verificationTime: primaryRoute?.verificationTime ?? null,
      documentedHelp: primaryRoute?.documentedHelp ?? null,
      unresolvedGaps: gaps,
      isCurrentReferenceOnly: true,
    };
  });

  const estimatedBytes = estimateResponseBytes(results);
  if (estimatedBytes > MAX_RESPONSE_BYTES) {
    return budgetExceeded("response_bytes_would_exceed_limit", MAX_RESPONSE_BYTES, estimatedBytes);
  }

  return { status: "ok", data: results };
}

/** Coverage status for a state/county, independent of any specific feature lookup. */
export async function readCoverageForRegion(
  state: string,
  county: string | null
): Promise<BoundedResponse<{ state: string; county: string | null; coverageState: LandContextResult["coverageState"]; gap: string }>> {
  if (!isPilotState(state)) {
    return budgetExceeded("outside_pilot_states", PILOT_STATES.length, null);
  }
  const { covered, gap } = await readCoverageStatus(state, county);
  const coverageState: LandContextResult["coverageState"] =
    covered === true ? "matched" : covered === false ? "no_match_in_proven_coverage" : "unknown_coverage";
  return { status: "ok", data: { state, county, coverageState, gap } };
}
