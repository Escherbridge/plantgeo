import { describe, expect, it } from "vitest";
import {
  AFFILIATE_COPY_LINK_LABEL,
  AFFILIATE_DASHBOARD_BODY,
  AFFILIATE_DASHBOARD_HEADING,
  affiliateDisplayName,
  affiliateInviteLine,
  affiliateInviteUrl,
  buildAffiliateCode,
  inviteLineForAffiliate,
} from "@/lib/affiliate/invite-copy";
import { normalizeRefCode } from "@/lib/plans/ref-code";

describe("affiliate invite copy", () => {
  it("uses the recorded name, then the organization, and invents neither", () => {
    expect(affiliateDisplayName({ name: "Ada Planner", organization: "Ada Gardens" })).toBe(
      "Ada Planner"
    );
    expect(affiliateDisplayName({ name: "  ", organization: "Ada Gardens" })).toBe("Ada Gardens");
    expect(affiliateDisplayName({ name: " ", organization: "  " })).toBeNull();
    expect(inviteLineForAffiliate({ name: " ", organization: null })).toBeNull();
  });

  it("fills the invite line and the production link from the issued code", () => {
    expect(affiliateInviteLine("Ada Planner")).toBe(
      "Ada Planner invited you to plan a site. They can see that you started a plan, but not what's in it."
    );
    expect(AFFILIATE_DASHBOARD_HEADING).toBe("Your invite link");
    expect(AFFILIATE_DASHBOARD_BODY).toBe(
      "Send this link to a landowner, a grower, a school, or a homestead. When they start a plan from it, it is counted on this page. A referral share has not been set, so this page does not show a rate or a payout."
    );
    expect(AFFILIATE_COPY_LINK_LABEL).toBe("Copy link");
    expect(affiliateInviteUrl("ada-planner-ab12cd")).toBe(
      "https://plantgeo.aevani.com/plan/new?ref=ada-planner-ab12cd"
    );
  });

  it("issues a code the plan ref parser will accept", () => {
    const code = buildAffiliateCode("Ada Planner", null, "AB12CD");
    expect(code).toBe("ada-planner-ab12cd");
    expect(normalizeRefCode(code)).toBe(code);
    expect(buildAffiliateCode("***", null, "ab12cd")).toBe("affiliate-ab12cd");
    expect(normalizeRefCode(buildAffiliateCode("José Gardens", "Ignored", "a1b2c3"))).toBe(
      "jose-gardens-a1b2c3"
    );
  });
});
