/**
 * Client-safe types for the land-context bounded readers.
 *
 * Moved out of `src/lib/server/services/land-context/types.ts` (which stays as the module the
 * server-side readers import) because it is pure type declarations with no runtime code, yet
 * browser components need to import from it too -- `useLandContextQuery.ts` and the panel/adapter
 * files reshape `LandContextResult` into the UI-facing store shape. `scripts/check-client-server-imports.mjs`
 * enforces that browser code never reach into `@/lib/server/**`, deliberately without a type-only
 * exemption (see its `TYPE_ONLY_ALLOWLIST`, a narrow, explicit list rather than a blanket rule) --
 * so a shared type used by both sides belongs in a module outside that boundary, not on an
 * allowlist. `src/lib/server/services/land-context/types.ts` re-exports everything here so its five
 * existing server-side importers are unaffected.
 *
 * See `conductor/tracks/pnw_land_context_reference_plane_20260911/spec.md`
 * ("Reference records and identity", "Bounded readers and agent contract")
 * and `conductor/tracks/pnw_land_contact_experience_20260911/spec.md`
 * ("Contact and help claims", "Agent parity") for the contract these types
 * encode. Nothing here claims a capability the source spec did not admit.
 */

// TODO(worker-1): import row shapes from
// `@/lib/server/db/schema/land-context` once that module lands
// (boundary_versions, organizations_offices, public_contact_routes,
// place_office_topic_relationships, source_releases). Until then this file
// declares the minimal reader-facing shape this service depends on, matching
// the five relations documented in the reference-plane spec's "Reference
// records and identity" section.

/** WA/OR/ID only, per the reference-plane spec's admitted scope. */
export type PilotState = "WA" | "OR" | "ID";

/**
 * A parcel/tract identity as the spec requires: county/source namespace plus
 * the original string ID, leading zeros preserved. Never a bare numeric
 * OBJECTID and never fabricated from coordinates or timestamps.
 */
export interface ParcelKey {
  /** e.g. "wa-king-county-assessor" — county/source namespace. */
  sourceNamespace: string;
  /** Original upstream string ID, leading zeros intact. */
  originalId: string;
  state: PilotState;
}

export interface BoundaryVersionRef {
  sourceNamespace: string;
  nativeFeatureKey: string;
  nativeFeatureVersion: string | null;
  familyType: string;
  interestType: string;
  state: PilotState;
  county: string | null;
  /**
   * Hex-encoded WKB/EWKB geometry for this feature, or `null` when no
   * geometry is available (e.g. an unwired reader placeholder). Decode with
   * `decodeBoundaryGeometry` from `./geometry/boundary-geometry-adapter`
   * rather than reading this field directly — see that module for the
   * null-vs-throw contract.
   */
  geometryWkb: string | null;
}

export interface OrganizationOfficeRef {
  organizationId: string;
  officeId: string;
  officialPublicName: string;
  officeType: string;
  parentOrganizationId: string | null;
}

/**
 * Typed route meaning, matching the experience spec's "Contact and help
 * claims" table exactly. Never collapse these into a single "contact" bucket.
 */
export type RouteMeaning =
  | "records_assistance"
  | "responsible_agency_program"
  | "advisory_sme"
  | "contact_process_inquiry"
  | "documented_introduction_forwarding";

export interface PublicContactRouteRef {
  officeId: string;
  routeType: string;
  routeMeaning: RouteMeaning;
  documentedTopic: string | null;
  documentedHelp: string | null;
  officialInquiryUrl: string | null;
  publicPhone: string | null;
  publicEmail: string | null;
  optionalProfessionalName: string | null;
  status: "active" | "stale" | "broken" | "unverified";
  verificationTime: string | null;
  /**
   * True only when a cited current source expressly documents an
   * introduction/forwarding service, per the "Documented
   * introduction/forwarding" row of the contact-claims table. Defaults to
   * false/unknown everywhere else in this module.
   */
  supportsIntroductionOrForwarding: boolean;
}

export interface PlaceOfficeTopicRelationshipRef {
  subjectId: string;
  objectId: string;
  relationshipKind: string;
  applicableGeography: string | null;
  documentedTopic: string | null;
  assignmentMethod: string;
  reviewStatus: "reviewed" | "unreviewed" | "unknown";
  effectiveFrom: string | null;
  effectiveTo: string | null;
  sourceEvidenceUrl: string | null;
}

export interface SourceReleaseRef {
  publisher: string;
  canonicalEndpoint: string;
  sourceVersion: string;
  captureTime: string | null;
  sourceEffectiveTime: string | null;
  sourcePublishedTime: string | null;
  admissionVerdict: "admitted" | "rejected" | "pending";
}

/**
 * Distinguishes "no matching feature in a proven-covered area" from
 * "coverage itself is unknown" from "requested history is unavailable" —
 * three distinct states the spec forbids collapsing into a bare null.
 */
export type CoverageState =
  | "matched"
  | "no_match_in_proven_coverage"
  | "unknown_coverage"
  | "unavailable_history"
  | "outside_pilot"
  | "partial_area_coverage";

/** How a reported feature overlaps the caller's selection. */
export interface OverlapBasis {
  kind: "point_containment" | "bbox_intersection" | "exact_geometry_intersection";
  /** Never "centroid" or "nearest office" — the spec explicitly forbids that reduction. */
  description: string;
}

/**
 * Every reader result the spec requires to carry: source reference, overlap
 * basis, organization/office, route meaning, assignment evidence, public
 * contact URL, verification time, documented help and unresolved gaps.
 */
export interface LandContextResult {
  coverageState: CoverageState;
  sourceFeature: BoundaryVersionRef | null;
  sourceRelease: SourceReleaseRef | null;
  matchedRegionOrOverlap: OverlapBasis | null;
  organizationOffice: OrganizationOfficeRef | null;
  route: PublicContactRouteRef | null;
  roleOrRouteType: string | null;
  assignmentEvidence: PlaceOfficeTopicRelationshipRef | null;
  publicContactUrl: string | null;
  verificationTime: string | null;
  documentedHelp: string | null;
  /** Non-empty whenever any dimension above is unresolved. */
  unresolvedGaps: string[];
  /** True when this reference is current-vs-selected-day, disclosed per the time contract. */
  isCurrentReferenceOnly: boolean;
}

/** A typed "budget exceeded" response — never a silent truncation. */
export interface BudgetExceededResult {
  status: "budget_exceeded";
  reason:
    | "aoi_area_exceeds_limit"
    | "geometry_vertices_exceed_limit"
    | "feature_count_would_exceed_limit"
    | "response_bytes_would_exceed_limit"
    | "outside_pilot_states";
  limit: number;
  requested: number | null;
}

export interface BoundedOkResult<T> {
  status: "ok";
  data: T;
}

export type BoundedResponse<T> = BoundedOkResult<T> | BudgetExceededResult;
