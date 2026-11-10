import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { normalizeRefCode, readPlanRefCookie } from "@/lib/plans/ref-code";
import {
  canEditSitePlan,
  guestTokenMatches,
  hashGuestPlanToken,
  issueGuestPlanToken,
} from "@/lib/server/plans/access";

describe("guest plan tokens", () => {
  it("stores a SHA-256 digest and not the raw token", () => {
    const issued = issueGuestPlanToken();
    const digest = createHash("sha256").update(issued.token, "utf8").digest("hex");

    expect(issued.tokenHash).toBe(digest);
    expect(issued.tokenHash).toBe(hashGuestPlanToken(issued.token));
    expect(issued.tokenHash).not.toBe(issued.token);
    expect(guestTokenMatches(issued.token, issued.tokenHash)).toBe(true);
    expect(guestTokenMatches(`${issued.token}x`, issued.tokenHash)).toBe(false);
    expect(guestTokenMatches("", issued.tokenHash)).toBe(false);
    expect(guestTokenMatches(issued.token, null)).toBe(false);
  });

  it("lets the owner or a matching guest token edit, and never the sample", () => {
    const issued = issueGuestPlanToken();
    const base = {
      isSample: false,
      ownerUserId: null as string | null,
      sessionUserId: null as string | null,
      guestToken: issued.token,
      guestTokenHash: issued.tokenHash,
    };

    expect(canEditSitePlan({ ...base, isSample: true, ownerUserId: "user-1", sessionUserId: "user-1" })).toBe(false);
    expect(canEditSitePlan({ ...base, ownerUserId: "user-1", sessionUserId: "user-1", guestToken: undefined, guestTokenHash: null })).toBe(true);
    expect(canEditSitePlan(base)).toBe(true);
    expect(canEditSitePlan({ ...base, guestToken: "not-the-token" })).toBe(false);
    expect(canEditSitePlan({ ...base, ownerUserId: "user-1", sessionUserId: "user-2", guestToken: undefined, guestTokenHash: null })).toBe(false);
  });
});

describe("plan ref codes", () => {
  it("keeps a short invite code and ignores anything else", () => {
    expect(normalizeRefCode("ada-demo")).toBe("ada-demo");
    expect(normalizeRefCode("  ADA-Demo ")).toBe("ada-demo");
    expect(normalizeRefCode("")).toBeNull();
    expect(normalizeRefCode("has space")).toBeNull();
    expect(normalizeRefCode("a")).toBe("a");
    expect(readPlanRefCookie("theme=dark; plantgeo_ref=ada-demo")).toBe("ada-demo");
    expect(readPlanRefCookie("plantgeo_ref=not a code")).toBeNull();
    expect(readPlanRefCookie(null)).toBeNull();
  });
});
