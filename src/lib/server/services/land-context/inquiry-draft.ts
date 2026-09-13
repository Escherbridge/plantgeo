/**
 * User-reviewed inquiry draft assembly.
 *
 * Per the contact-experience spec's "User-reviewed inquiry drafts" section:
 * assembles editable text for the user to review and copy. Never sends
 * anything, never infers ownership/representation, never invents project
 * facts or private names, and never claims a permission that was not
 * established. This module has no network/IO calls — it is pure string
 * assembly over already-resolved reader results.
 */

import type { LandContextResult, ParcelKey } from "./types";

export interface DraftInquiryInput {
  parcelKey: ParcelKey | null;
  county: string | null;
  state: string;
  /** The user's own stated idea/question — never fabricated on their behalf. */
  userProvidedIdea: string;
  /** The reader result identifying the proposed public recipient/route. */
  contact: LandContextResult;
}

export interface DraftInquiryResult {
  /** Editable draft text. Never sent by this function or any caller in this module. */
  draftText: string;
  /** True only when the contact's route explicitly documents that capability. */
  impliesIntroductionOrForwarding: false;
  sourceLinks: string[];
  unresolvedGaps: string[];
}

/**
 * Builds a plain-text inquiry draft. This function performs no IO: it
 * returns a string for the caller (UI or agent tool) to display for review.
 * Nothing here sends an email, submits a form, or contacts anyone.
 */
export function draftInquiry(input: DraftInquiryInput): DraftInquiryResult {
  const { parcelKey, county, state, userProvidedIdea, contact } = input;

  const parcelLine = parcelKey
    ? `Parcel/tract reference: ${parcelKey.sourceNamespace}/${parcelKey.originalId} (${parcelKey.state})`
    : `Location: ${county ? `${county} County, ` : ""}${state} (no resolved parcel reference)`;

  const recipientName = contact.organizationOffice?.officialPublicName ?? "unresolved office";
  const routeMeaning = contact.roleOrRouteType ?? "unknown route type";

  const lines: string[] = [];
  lines.push(`Subject: Inquiry regarding ${parcelLine}`);
  lines.push("");
  lines.push(parcelLine);
  lines.push(`Proposed recipient: ${recipientName} (${routeMeaning})`);
  lines.push("");
  lines.push("Message:");
  lines.push(userProvidedIdea.trim().length > 0 ? userProvidedIdea.trim() : "(add your question here)");
  lines.push("");

  // Route-meaning-specific question framing, per the "Contact and help
  // claims" table — never implies a capability beyond what the route
  // documents.
  switch (contact.roleOrRouteType) {
    case "records_assistance":
      lines.push("Question: Which records or office handle this type of inquiry?");
      break;
    case "contact_process_inquiry":
      lines.push(
        "Question: Does an established contact process exist for this location, and if so, what is it?"
      );
      break;
    case "responsible_agency_program":
      lines.push("Question: Can you confirm your office's role and the appropriate next step for this inquiry?");
      break;
    case "advisory_sme":
      lines.push("Question: Given your documented topic area, what guidance or referral can you offer here?");
      break;
    case "documented_introduction_forwarding":
      lines.push(
        "Question: What is your documented introduction or forwarding process for this kind of request, and what are its stated limits?"
      );
      break;
    default:
      lines.push("Question: What is the appropriate next step for this inquiry?");
  }

  lines.push("");
  lines.push(
    "This message does not assume the recipient will identify an owner, disclose personal details, or forward this request."
  );

  const sourceLinks: string[] = [];
  if (contact.publicContactUrl) sourceLinks.push(contact.publicContactUrl);

  const unresolvedGaps = [...contact.unresolvedGaps];
  if (!contact.organizationOffice) {
    unresolvedGaps.push("no resolved office; draft recipient is a placeholder, not a confirmed contact");
  }
  if (!contact.route?.supportsIntroductionOrForwarding) {
    unresolvedGaps.push(
      "introduction/forwarding capability is not documented for this route and is not claimed by this draft"
    );
  }

  return {
    draftText: lines.join("\n"),
    impliesIntroductionOrForwarding: false,
    sourceLinks,
    unresolvedGaps,
  };
}
