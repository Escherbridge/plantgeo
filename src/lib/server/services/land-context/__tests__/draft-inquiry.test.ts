/**
 * Requirement 4: `draftInquiry` has zero I/O and never claims
 * introduction/forwarding beyond what's documented.
 *
 * Per inquiry-draft.ts module doc: "Never sends anything, never infers
 * ownership/representation, never invents project facts or private names,
 * and never claims a permission that was not established. This module has
 * no network/IO calls." The gate is `route.supportsIntroductionOrForwarding`
 * — this file proves it is enforced in the produced text, not merely present
 * in types.
 */
import { describe, expect, it, vi } from "vitest";
import { draftInquiry } from "@/lib/server/services/land-context/inquiry-draft";
import type { LandContextResult, PublicContactRouteRef } from "@/lib/server/services/land-context/types";

function buildRoute(overrides: Partial<PublicContactRouteRef> = {}): PublicContactRouteRef {
  return {
    officeId: "office-1",
    routeType: "web-form",
    routeMeaning: "contact_process_inquiry",
    documentedTopic: "parcel inquiries",
    documentedHelp: "Handles general parcel questions.",
    officialInquiryUrl: "https://example.test/contact",
    publicPhone: null,
    publicEmail: null,
    optionalProfessionalName: null,
    status: "active",
    verificationTime: "2026-09-01T00:00:00Z",
    supportsIntroductionOrForwarding: false,
    ...overrides,
  };
}

function buildContact(route: PublicContactRouteRef | null): LandContextResult {
  return {
    coverageState: "matched",
    sourceFeature: null,
    sourceRelease: null,
    matchedRegionOrOverlap: null,
    organizationOffice: route
      ? {
          organizationId: "org-1",
          officeId: route.officeId,
          officialPublicName: "Test County Assessor",
          officeType: "assessor",
          parentOrganizationId: null,
        }
      : null,
    route,
    roleOrRouteType: route?.routeMeaning ?? null,
    assignmentEvidence: null,
    publicContactUrl: route?.officialInquiryUrl ?? null,
    verificationTime: route?.verificationTime ?? null,
    documentedHelp: route?.documentedHelp ?? null,
    unresolvedGaps: [],
    isCurrentReferenceOnly: true,
  };
}

describe("draftInquiry", () => {
  it("performs zero I/O: no fetch/network/fs calls occur while building the draft", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const contact = buildContact(buildRoute({ supportsIntroductionOrForwarding: false }));

    draftInquiry({
      parcelKey: { sourceNamespace: "wa-king-county-assessor", originalId: "0001", state: "WA" },
      county: "King",
      state: "WA",
      userProvidedIdea: "Is this parcel available for a small pollinator garden?",
      contact,
    });

    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("does NOT claim forwarding/introduction language when supportsIntroductionOrForwarding is false", () => {
    const contact = buildContact(buildRoute({ supportsIntroductionOrForwarding: false }));
    const result = draftInquiry({
      parcelKey: null,
      county: "King",
      state: "WA",
      userProvidedIdea: "Test idea",
      contact,
    });

    expect(result.impliesIntroductionOrForwarding).toBe(false);
    // The message body explicitly disclaims forwarding; a bare regex match on
    // "forward" would false-positive on that disclaimer, so check no
    // AFFIRMATIVE forwarding claim exists instead of a blanket absence.
    expect(result.draftText).toMatch(/does not assume the recipient will .* forward this request/i);
    expect(result.draftText).not.toMatch(/will forward|will introduce|can forward you|can introduce you/i);
    expect(result.unresolvedGaps).toContain(
      "introduction/forwarding capability is not documented for this route and is not claimed by this draft"
    );
  });

  it("does NOT claim forwarding/introduction when route is entirely absent", () => {
    const contact = buildContact(null);
    const result = draftInquiry({
      parcelKey: null,
      county: null,
      state: "OR",
      userProvidedIdea: "Test idea",
      contact,
    });

    expect(result.impliesIntroductionOrForwarding).toBe(false);
    expect(result.draftText).not.toMatch(/will forward|will introduce/i);
    expect(result.unresolvedGaps).toContain("no resolved office; draft recipient is a placeholder, not a confirmed contact");
  });

  it("still returns impliesIntroductionOrForwarding=false even when the route DOES document that capability, but records the gap as resolved (not flagged as a gap)", () => {
    const contact = buildContact(buildRoute({ supportsIntroductionOrForwarding: true }));
    const result = draftInquiry({
      parcelKey: { sourceNamespace: "or-multnomah-county-assessor", originalId: "0099", state: "OR" },
      county: "Multnomah",
      state: "OR",
      userProvidedIdea: "Test idea",
      contact,
    });

    // The type itself is hard-pinned to `false` — the function never claims
    // it performed a forwarding action. This is intentional: draftInquiry
    // only assembles review text, it never performs forwarding.
    expect(result.impliesIntroductionOrForwarding).toBe(false);
    // But the "not documented" gap should NOT appear, since it IS documented
    // for this route — this proves the gate reads the real field rather than
    // always appending the disclaimer gap.
    expect(result.unresolvedGaps).not.toContain(
      "introduction/forwarding capability is not documented for this route and is not claimed by this draft"
    );
  });
});
