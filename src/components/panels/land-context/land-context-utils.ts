import type {
  DraftInquiryInput,
  EvidenceStatus,
  NonpersonalOfficeContact,
  RouteMeaning,
} from "./types";

/**
 * Re-picks exactly the allowlisted keys from `NonpersonalOfficeContact`. Every rendering
 * component in this directory calls this before spreading an office object into JSX, so a
 * private field on an upstream object (e.g. a raw parcel-owner record the reader layer forgot
 * to strip) cannot reach the DOM even if it slips past that layer's own filtering -- this is
 * the second, structural gate, not a restatement of the first.
 */
export function sanitizeOfficeContact(
  input: NonpersonalOfficeContact,
): NonpersonalOfficeContact {
  return {
    id: input.id,
    name: input.name,
    role: input.role,
    topic: input.topic,
    regionFit: input.regionFit,
    publicPhone: input.publicPhone,
    publicEmail: input.publicEmail,
    formUrl: input.formUrl,
    officialUrl: input.officialUrl,
    introductionCapability: input.introductionCapability === true,
  };
}

/** Fixed, distinct labels for the five route meanings -- spec "Contact and help claims". */
export const ROUTE_MEANING_LABELS: Record<RouteMeaning, string> = {
  records_assistance: "Records assistance",
  responsible_agency: "Responsible agency/program",
  advisory_sme: "Advisory SME",
  contact_process_inquiry: "Contact-process inquiry",
  documented_introduction: "Documented introduction/forwarding",
};

/** The permitted claim text for each route meaning, verbatim from the spec's table. */
export const ROUTE_MEANING_CLAIMS: Record<RouteMeaning, string> = {
  records_assistance:
    "A published records/help office can be asked about its documented property-record or recorded-document services.",
  responsible_agency:
    "Source-backed jurisdiction and task responsibility establish an applicable agency/program inquiry route.",
  advisory_sme:
    "A professional role and published topic/service region establish advisory fit; expertise does not imply approval authority.",
  contact_process_inquiry:
    "A source-backed office route supports asking about the appropriate next contact process; the result is not an identified private owner or representative.",
  documented_introduction:
    "Show this capability only when a cited current source expressly offers that service, including its limits.",
};

/**
 * Returns `false` for `documented_introduction` unless the office/route object carries an
 * explicit `introductionCapability: true`. Every badge/claim renderer must gate on this rather
 * than on the route meaning alone -- default absent/false must never render a forwarding claim.
 */
export function canRenderRouteMeaning(
  routeMeaning: RouteMeaning,
  introductionCapability: boolean | undefined,
): boolean {
  if (routeMeaning === "documented_introduction") {
    return introductionCapability === true;
  }
  return true;
}

/** Icon + text pairs for evidence status -- never color alone (spec accessibility X4). */
export const EVIDENCE_STATUS_PRESENTATION: Record<
  EvidenceStatus,
  { icon: string; label: string }
> = {
  current: { icon: "✓", label: "Current" },
  stale: { icon: "⚠", label: "Stale" },
  unverified: { icon: "?", label: "Unverified" },
  broken_link: { icon: "✕", label: "Broken link" },
};

/**
 * Deduplicate a list of `{ office, relationships }` pairs on `office.id`, merging every
 * relationship into one card while keeping every relationship visible. Callers should feed
 * this the raw per-relationship rows from the reader layer; it does not fabricate or drop a
 * relationship, only merges cards that share an office id.
 */
export function dedupeOfficeCards<
  T extends { office: NonpersonalOfficeContact; relationships: unknown[] },
>(cards: T[]): T[] {
  const byId = new Map<string, T>();
  for (const card of cards) {
    const existing = byId.get(card.office.id);
    if (existing) {
      existing.relationships.push(...card.relationships);
    } else {
      byId.set(card.office.id, { ...card, relationships: [...card.relationships] });
    }
  }
  return Array.from(byId.values());
}

/**
 * Pure client-side string assembly for the Draft inquiry action. No fetch, no submission --
 * this function's only job is to produce text; `DraftInquiry.tsx` is responsible for putting
 * it in a textarea and copying it to the clipboard on explicit user action.
 */
export function buildDraftInquiryText(input: DraftInquiryInput): string {
  const { place, userIdea, recipient, recipientRationale, routeMeaning, suggestedQuestion, sourceLinks } =
    input;

  const lines: string[] = [];

  lines.push("Subject: Inquiry regarding a location of interest");
  lines.push("");
  lines.push(`Location: ${place.selectionLabel}`);
  if (place.parcelOrTractId) {
    lines.push(`Parcel/tract ID: ${place.parcelOrTractId}`);
  }
  if (place.county || place.state) {
    lines.push(`County/state: ${[place.county, place.state].filter(Boolean).join(", ")}`);
  }
  lines.push("");
  lines.push("What I'm looking into:");
  lines.push(userIdea.trim().length > 0 ? userIdea.trim() : "(describe your idea here)");
  lines.push("");
  lines.push(`Proposed recipient: ${recipient.name}${recipient.role ? ` (${recipient.role})` : ""}`);
  lines.push(`Why this recipient: ${recipientRationale}`);
  lines.push(`Route type: ${ROUTE_MEANING_LABELS[routeMeaning]}`);
  lines.push("");
  lines.push("Question:");
  lines.push(suggestedQuestion);
  lines.push("");
  if (sourceLinks.length > 0) {
    lines.push("Source links:");
    for (const link of sourceLinks) {
      lines.push(`- ${link.label}: ${link.url}`);
    }
    lines.push("");
  }
  lines.push(
    "(Draft only -- review before sending. This message does not claim permission, ownership or representation.)",
  );

  return lines.join("\n");
}
