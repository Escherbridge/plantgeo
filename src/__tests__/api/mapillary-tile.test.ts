import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  enforceRateLimit: vi.fn(),
  fetch: vi.fn(),
}));

vi.mock("@/lib/server/security/public-provider-rate-limit", () => ({
  enforcePublicProviderRateLimit: mocks.enforceRateLimit,
}));

import { GET } from "@/app/api/v1/imagery/mapillary/[z]/[x]/[y]/route";

describe("anonymous Mapillary tile proxy", () => {
  beforeEach(() => {
    vi.stubEnv("MAPILLARY_ACCESS_TOKEN", "test-token");
    vi.stubGlobal("fetch", mocks.fetch);
    mocks.enforceRateLimit.mockReset();
    mocks.fetch.mockReset();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("fails closed before upstream fetch when the production limiter is unavailable", async () => {
    mocks.enforceRateLimit.mockResolvedValue({
      allowed: false,
      status: 503,
      retryAfter: 30,
    });

    const request = new Request(
      "https://plantgeo.test/api/v1/imagery/mapillary/6/32/32",
      { headers: { "x-forwarded-for": "203.0.113.10" } }
    );
    const response = await GET(request, {
      params: Promise.resolve({ z: "6", x: "32", y: "32" }),
    });

    expect(response.status).toBe(503);
    expect(response.headers.get("retry-after")).toBe("30");
    expect(mocks.enforceRateLimit).toHaveBeenCalledWith(
      request,
      "imagery-mapillary-tile",
      180
    );
    expect(mocks.fetch).not.toHaveBeenCalled();
  });
});
