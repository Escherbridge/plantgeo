import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const mocks = vi.hoisted(() => ({
  getServerSession: vi.fn(),
  reserveRegionalIntelligenceUsage: vi.fn(),
}));

vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({
  getServerSession: mocks.getServerSession,
}));
vi.mock("@/lib/server/security/regional-intelligence-access", async (original) => {
  const actual = await original<
    typeof import("@/lib/server/security/regional-intelligence-access")
  >();
  return {
    ...actual,
    reserveRegionalIntelligenceUsage: mocks.reserveRegionalIntelligenceUsage,
  };
});

import { POST } from "@/app/api/ai/regional-intelligence/route";
import { regionalEvidenceFreshnessState } from "@/lib/regional-intelligence";

function postRequest(body: unknown): NextRequest {
  return new NextRequest("https://plantgeo.test/api/ai/regional-intelligence", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("regional intelligence access gates", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    process.env.ANTHROPIC_API_KEY = "test-key";
  });

  it("classifies future evidence timestamps as unavailable", () => {
    const now = Date.parse("2026-07-20T12:00:00.000Z");
    const futureTimestamp = new Date(now + 60_000).toISOString();

    expect(
      regionalEvidenceFreshnessState("drought", futureTimestamp, now)
    ).toBe("unavailable");
  });

  it("treats a fire detection inside its max age as available", () => {
    const now = Date.parse("2026-08-02T12:00:00.000Z");
    const observedAt = new Date(now - 3_600_000).toISOString();

    expect(
      regionalEvidenceFreshnessState("fireDetections", observedAt, now)
    ).toBe("available");
  });

  it("rejects an anonymous caller before reserving quota", async () => {
    mocks.getServerSession.mockResolvedValue(null);

    const response = await POST(postRequest({ lat: 44, lon: -116 }));

    expect(response.status).toBe(401);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(mocks.reserveRegionalIntelligenceUsage).not.toHaveBeenCalled();
  });

  it("rejects a malformed body before reserving quota", async () => {
    mocks.getServerSession.mockResolvedValue({ user: { id: "user-1" } });

    const response = await POST(postRequest({ lat: 44, lon: -116 }));

    expect(response.status).toBe(400);
    expect(mocks.reserveRegionalIntelligenceUsage).not.toHaveBeenCalled();
  });

  it("returns 429 with Retry-After when the quota is exhausted", async () => {
    mocks.getServerSession.mockResolvedValue({ user: { id: "user-1" } });
    mocks.reserveRegionalIntelligenceUsage.mockResolvedValue({
      allowed: false,
      reason: "quota",
      tier: "signed_in",
      remaining: 0,
      retryAfterSeconds: 42,
      resetAt: null,
    });

    const response = await POST(
      postRequest({
        lat: 44,
        lon: -116,
        locationConsent: { precision: "approximate", confirmed: true },
      })
    );

    expect(response.status).toBe(429);
    expect(response.headers.get("retry-after")).toBe("42");
  });
});
