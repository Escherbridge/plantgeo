/**
 * Local type contract for the land-context detail panel (Worker 4/5).
 *
 * `src/stores/land-context-store.ts` did not exist when this directory was built. These types
 * are this panel's expected read-side shape; the store/reader-layer owner should reconcile its
 * actual output against this file rather than the panel importing anything looser. Every shape
 * here is an explicit allowlist -- see "Nonpersonal allowlist" below -- so a private field can
 * only leak in if a caller silently widens one of these interfaces, not by passing extra data
 * through a spread.
 */

/** The five route meanings spec section "Contact and help claims" requires staying distinct. */
export type RouteMeaning =
  | "records_assistance"
  | "responsible_agency"
  | "advisory_sme"
  | "contact_process_inquiry"
  | "documented_introduction";

/** Staleness/verification states; always rendered with an icon AND a text label (spec X4). */
export type EvidenceStatus = "current" | "stale" | "unverified" | "broken_link";

/**
 * Nonpersonal allowlist: the ONLY office/adviser contact fields this panel ever accepts or
 * renders. Deliberately excludes any private-owner-name or personal-contact field. A caller
 * cannot smuggle a private field through this shape without editing this file in the open --
 * `sanitizeOfficeContact` in `land-context-utils.ts` re-picks exactly these keys before render,
 * so even a mistakenly wider object upstream is clipped back to this allowlist at the boundary.
 */
export interface NonpersonalOfficeContact {
  id: string;
  /** Published office/program/team name. Never a private individual's name. */
  name: string;
  /** Published role/team context, e.g. "Field Office - Realty" or "Extension Agent". */
  role?: string;
  topic?: string;
  regionFit?: string;
  /** Public business phone, sourced from the office's own published directory. */
  publicPhone?: string;
  /** Public business email, sourced from the office's own published directory. */
  publicEmail?: string;
  formUrl?: string;
  officialUrl?: string;
  /**
   * Only `true` permits rendering a "Documented introduction/forwarding" claim. Absent or
   * `false` must never render a forwarding claim -- see RouteBadge and OfficeCard.
   */
  introductionCapability?: boolean;
}

/** "Why this route applies": jurisdiction/crosswalk evidence behind one relationship. */
export interface RouteEvidence {
  /** Published jurisdiction/native office key, or reviewed crosswalk citation. */
  matchMethod: string;
  /** The relationship evidence itself (e.g. "BLM surface-manager record, parcel intersects field office boundary"). */
  relationshipEvidence: string;
  sourceLabel: string;
  sourceUrl?: string;
  /** Unresolved scope / caveats this match does not cover. */
  unresolvedScope?: string;
}

/** One feature/relationship attaching an office to a place -- offices dedupe, relationships don't. */
export interface RelationshipReason {
  featureId: string;
  featureLabel: string;
  routeMeaning: RouteMeaning;
  evidence: RouteEvidence;
  /** Service actually described by the source for this relationship (spec "Documented help"). */
  documentedService?: string;
  /** Parcel/tract/location references this relationship's help requires. */
  requiredReferences?: string[];
  /** True only if this specific office/route object carries an explicit forwarding capability. */
  introductionCapability?: boolean;
}

/** A deduplicated office card: one office, every relationship that attached it. */
export interface OfficeCardData {
  office: NonpersonalOfficeContact;
  relationships: RelationshipReason[];
}

/** Related adviser: visually distinct from a responsible-authority OfficeCard. */
export interface AdviserCardData {
  adviser: NonpersonalOfficeContact;
  topic: string;
  serviceArea: string;
  programLimits?: string;
}

export interface UseFacet {
  label: string;
  value: string;
  sourceLabel: string;
  sourceUrl?: string;
}

export interface AcreageEvidence {
  value: number;
  unit: string;
  sourceLabel: string;
  method?: string;
}

/** Place-details section content (spec panel item 1). */
export interface PlaceDetail {
  /** Selected point/area label -- coordinates or bounded-area description, never a viewport centre. */
  selectionLabel: string;
  parcelOrTractId?: string;
  county?: string;
  state?: string;
  acreage?: AcreageEvidence;
  /** Published nonpersonal ownership category (e.g. "State agency", "Federal - BLM"), never an owner name. */
  ownershipCategory?: string;
  /** Public ownership vs management distinction, when the source separates them. */
  managementCategory?: string;
  useFacets?: UseFacet[];
  officialRecordUrl?: string;
  officialRecordLabel?: string;
}

/** Evidence-and-time section entry (spec panel item 5). */
export interface EvidenceTimeEntry {
  label: string;
  sourceVersion?: string;
  coverage?: string;
  contactVerifiedAt?: string;
  status: EvidenceStatus;
  note?: string;
}

/** The full read-side payload this panel expects from the store/reader layer. */
export interface LandContextPanelData {
  place: PlaceDetail;
  officeCards: OfficeCardData[];
  advisers: AdviserCardData[];
  evidence: EvidenceTimeEntry[];
}

/** Draft-inquiry assembly input; see `buildDraftInquiryText` in `land-context-utils.ts`. */
export interface DraftInquiryInput {
  place: PlaceDetail;
  /** Free-text idea the user typed; never invented by this panel. */
  userIdea: string;
  recipient: NonpersonalOfficeContact;
  /** Why the recipient is relevant, drawn from the route's evidence -- not asserted authority. */
  recipientRationale: string;
  routeMeaning: RouteMeaning;
  suggestedQuestion: string;
  sourceLinks: { label: string; url: string }[];
}
