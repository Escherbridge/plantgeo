import { describe, expect, it } from "vitest";
import { ipRateLimitKey } from "@/lib/server/security/registration";
import {
  AFFILIATE_HONEYPOT_FIELD,
  affiliateApplicationRateLimitKey,
  decideAffiliateApplication,
  honeypotTripped,
} from "@/lib/server/security/affiliate-application";

const allowed = { available: true, limited: false } as const;

function application(extra: Record<string, unknown> = {}) {
  return {
    name: "Ada Planner",
    email: "Ada@Example.com",
    organization: "",
    role: "designer",
    website: "",
    message: "I teach site planning.",
    [AFFILIATE_HONEYPOT_FIELD]: "",
    ...extra,
  };
}

describe("affiliate application honeypot", () => {
  it("treats a filled honeypot as silent and stores nothing", () => {
    expect(honeypotTripped("")).toBe(false);
    expect(honeypotTripped("  ")).toBe(false);
    expect(honeypotTripped("bot")).toBe(true);
    expect(honeypotTripped(1)).toBe(true);

    const decision = decideAffiliateApplication({
      payload: application({ [AFFILIATE_HONEYPOT_FIELD]: "filled" }),
      rate: allowed,
    });
    expect(decision).toEqual({ outcome: "silent" });
  });

  it("rejects a bad form only after the honeypot is empty", () => {
    expect(
      decideAffiliateApplication({
        payload: application({ email: "not-an-email" }),
        rate: allowed,
      })
    ).toEqual({ outcome: "invalid" });
    expect(
      decideAffiliateApplication({
        payload: application({ website: "not a url" }),
        rate: allowed,
      })
    ).toEqual({ outcome: "invalid" });
  });

  it("stores a valid application as pending and drops the honeypot", () => {
    const decision = decideAffiliateApplication({
      payload: application({ website: "https://example.org/garden" }),
      rate: allowed,
    });
    expect(decision.outcome).toBe("store");
    if (decision.outcome !== "store") return;
    expect(decision.row).toEqual({
      name: "Ada Planner",
      email: "ada@example.com",
      organization: null,
      role: "designer",
      website: "https://example.org/garden",
      message: "I teach site planning.",
      status: "pending",
    });
    expect(decision.row).not.toHaveProperty(AFFILIATE_HONEYPOT_FIELD);
  });
});

describe("affiliate application rate limit", () => {
  it("keys the limiter on a digest of the caller address", () => {
    const request = new Request("https://plantgeo.aevani.com/api/affiliate/apply", {
      headers: { "x-forwarded-for": "203.0.113.10, 10.0.0.1" },
    });
    const key = affiliateApplicationRateLimitKey(request);
    expect(key).toBe(ipRateLimitKey(request, "affiliate:apply"));
    expect(key.startsWith("affiliate:apply:")).toBe(true);
    expect(key).not.toContain("203.0.113.10");
  });

  it("stops before the honeypot when the limiter is closed or exhausted", () => {
    const payload = application({ [AFFILIATE_HONEYPOT_FIELD]: "filled" });
    expect(
      decideAffiliateApplication({ payload, rate: { available: false } })
    ).toEqual({ outcome: "unavailable" });
    expect(
      decideAffiliateApplication({
        payload,
        rate: { available: true, limited: true, retryAfter: 12 },
      })
    ).toEqual({ outcome: "rate_limited", retryAfter: 12 });
  });
});
