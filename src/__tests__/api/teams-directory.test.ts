import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const mocks = vi.hoisted(() => ({
  authorizeApiRequest: vi.fn(),
}));

vi.mock("@/lib/server/middleware/api-auth", () => ({
  authorizeApiRequest: mocks.authorizeApiRequest,
  apiKeyAuthorizationErrorResponse: vi.fn(),
}));

import { GET } from "@/app/api/v1/teams/route";

describe("GET /api/v1/teams", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.authorizeApiRequest.mockResolvedValue({ valid: true, keyId: "key" });
  });

  it("does not enumerate private partner workspaces through an API key", async () => {
    const response = await GET(
      new NextRequest(
        "https://plantgeo.test/api/v1/teams?lat=39.7392&lon=-104.9903"
      )
    );

    expect(response.status).toBe(503);
    expect(response.headers.get("cache-control")).toBe("no-store");
    await expect(response.json()).resolves.toEqual({
      code: "PARTNER_DIRECTORY_INACTIVE",
      error:
        "Partner discovery is inactive until verified organizations and access rules are published",
      retryable: false,
    });
  });
});
