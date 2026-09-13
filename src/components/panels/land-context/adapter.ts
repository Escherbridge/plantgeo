import type { LandContextResult } from "@/lib/server/services/land-context/types";
import type {
  AdviserCardData,
  EvidenceTimeEntry,
  LandContextPanelData,
  NonpersonalOfficeContact,
  OfficeCardData,
  PlaceDetail,
  RelationshipReason,
  RouteMeaning,
} from "./types";

/**
 * Reshapes the reference-plane's rich `LandContextResult[]` (one entry per
 * matched feature, see `src/lib/server/services/land-context/types.ts`) into
 * the panel's `LandContextPanelData`. This is the reconciliation the panel's
 * own `types.ts` asked for: "the store/reader-layer owner should reconcile
 * its actual output against this file rather than the panel importing
 * anything looser."
 *
 * Every field copied here is nonpersonal per the reader contract -- there is
 * no private-owner-name or personal-contact field anywhere in
 * `LandContextResult`, so this adapter cannot leak one even by mistake.
 */

const ROUTE_MEANING_MAP: Record<string, RouteMeaning> = {
  records_assistance: "records_assistance",
  responsible_agency_program: "responsible_agency",
  advisory_sme: "advisory_sme",
  contact_process_inquiry: "contact_process_inquiry",
  documented_introduction_forwarding: "documented_introduction",
};

function toRouteMeaning(meaning: string | undefined): RouteMeaning {
  return (meaning ? ROUTE_MEANING_MAP[meaning] : undefined) ?? "contact_process_inquiry";
}

function toNonpersonalOfficeContact(result: LandContextResult): NonpersonalOfficeContact | null {
  const org = result.organizationOffice;
  const route = result.route;
  if (!org && !route) return null;

  return {
    id: org?.officeId ?? route?.officeId ?? "unknown-office",
    name: org?.officialPublicName ?? "Office name not yet resolved",
    role: org?.officeType,
    topic: route?.documentedTopic ?? undefined,
    publicPhone: route?.publicPhone ?? undefined,
    publicEmail: route?.publicEmail ?? undefined,
    formUrl: route?.routeType === "form" ? (route?.officialInquiryUrl ?? undefined) : undefined,
    officialUrl: route?.officialInquiryUrl ?? result.publicContactUrl ?? undefined,
    // Only ever true when the specific route object documents it -- never inferred.
    introductionCapability: route?.supportsIntroductionOrForwarding === true,
  };
}

function toRelationshipReason(result: LandContextResult, index: number): RelationshipReason | null {
  const office = toNonpersonalOfficeContact(result);
  if (!office) return null;

  return {
    featureId: result.sourceFeature?.nativeFeatureKey ?? `result-${index}`,
    featureLabel: result.sourceFeature?.interestType ?? "Unlabeled feature",
    routeMeaning: toRouteMeaning(result.route?.routeMeaning),
    evidence: {
      matchMethod: result.assignmentEvidence?.assignmentMethod ?? "unresolved",
      relationshipEvidence: result.matchedRegionOrOverlap?.description ?? "No overlap evidence recorded.",
      sourceLabel: result.sourceRelease?.publisher ?? "Unknown source",
      sourceUrl: result.sourceRelease?.canonicalEndpoint,
      unresolvedScope: result.unresolvedGaps.length > 0 ? result.unresolvedGaps.join("; ") : undefined,
    },
    documentedService: result.documentedHelp ?? undefined,
    introductionCapability: result.route?.supportsIntroductionOrForwarding === true,
  };
}

/** Offices dedupe by ID and merge relationships; advisers stay separate per spec. */
function dedupeIntoOfficeCards(results: LandContextResult[]): OfficeCardData[] {
  const byOfficeId = new Map<string, OfficeCardData>();

  results.forEach((result, index) => {
    if (result.route?.routeMeaning === "advisory_sme") return;
    const office = toNonpersonalOfficeContact(result);
    const relationship = toRelationshipReason(result, index);
    if (!office || !relationship) return;

    const existing = byOfficeId.get(office.id);
    if (existing) {
      existing.relationships.push(relationship);
    } else {
      byOfficeId.set(office.id, { office, relationships: [relationship] });
    }
  });

  return Array.from(byOfficeId.values());
}

function toAdviserCards(results: LandContextResult[]): AdviserCardData[] {
  return results
    .filter((result) => result.route?.routeMeaning === "advisory_sme")
    .map((result) => {
      const office = toNonpersonalOfficeContact(result);
      if (!office) return null;
      const adviser: AdviserCardData = {
        adviser: office,
        topic: result.route?.documentedTopic ?? "Topic not documented",
        serviceArea: result.assignmentEvidence?.applicableGeography ?? "Service area not documented",
        programLimits: result.unresolvedGaps.length > 0 ? result.unresolvedGaps.join("; ") : undefined,
      };
      return adviser;
    })
    .filter((adviser): adviser is AdviserCardData => adviser !== null);
}

function toEvidenceEntries(results: LandContextResult[]): EvidenceTimeEntry[] {
  return results.map((result, index) => ({
    label: result.sourceFeature?.nativeFeatureKey ?? `Feature ${index + 1}`,
    sourceVersion: result.sourceRelease?.sourceVersion,
    coverage: result.coverageState,
    contactVerifiedAt: result.verificationTime ?? undefined,
    status:
      result.route?.status === "active"
        ? "current"
        : result.route?.status === "stale"
          ? "stale"
          : result.route?.status === "broken"
            ? "broken_link"
            : "unverified",
    note: result.isCurrentReferenceOnly
      ? "Current reference only -- historical version unsupported for this feature."
      : undefined,
  }));
}

/**
 * `selectionLabel` is the caller's responsibility: this adapter has no
 * access to the map's selection input (point/parcel/area), only the
 * reader's results, so the panel host must pass a description of the
 * selection alongside the results it fetched for it.
 */
export function toLandContextPanelData(
  results: LandContextResult[],
  selectionLabel: string
): LandContextPanelData {
  const matched = results.filter((r) => r.coverageState === "matched");
  const primary = matched[0];

  const place: PlaceDetail = {
    selectionLabel,
    parcelOrTractId: primary?.sourceFeature?.nativeFeatureKey,
    county: primary?.sourceFeature?.county ?? undefined,
    state: primary?.sourceFeature?.state,
    ownershipCategory: primary?.sourceFeature?.interestType,
    officialRecordUrl: primary?.sourceRelease?.canonicalEndpoint,
    officialRecordLabel: primary?.sourceRelease?.publisher,
  };

  return {
    place,
    officeCards: dedupeIntoOfficeCards(matched),
    advisers: toAdviserCards(matched),
    evidence: toEvidenceEntries(matched),
  };
}
