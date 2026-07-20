import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const mocks = vi.hoisted(() => ({
  authorizeApiRequest: vi.fn(),
}));

vi.mock("@/lib/server/middleware/api-auth", () => ({
  authorizeApiRequest: mocks.authorizeApiRequest,
  apiKeyAuthorizationErrorResponse: vi.fn(),
}));

import { GET } from "@/app/api/v1/location-context/route";

describe("GET /api/v1/location-context", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.authorizeApiRequest.mockResolvedValue({ valid: true, keyId: "key" });
  });

  it("fails closed without returning legacy priority zones", async () => {
    const response = await GET(
      new NextRequest(
        "https://plantgeo.test/api/v1/location-context?lat=39.7392&lon=-104.9903"
      )
    );

    expect(response.status).toBe(503);
    expect(response.headers.get("cache-control")).toBe("no-store");
    const body = await response.json();
    expect(body).toEqual({
      code: "LOCATION_CONTEXT_INACTIVE",
      error:
        "Location context is inactive until a reviewed, partner-scoped publication is available",
      retryable: false,
    });
    expect(body).not.toHaveProperty("priorityZones");
    expect(body).not.toHaveProperty("communityPriorityZones");
  });
});
