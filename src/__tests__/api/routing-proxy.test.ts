import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const mocks = vi.hoisted(() => ({
  authorizeApiRequest: vi.fn(),
  getRoute: vi.fn(),
}));

vi.mock("@/lib/server/middleware/api-auth", () => ({
  authorizeApiRequest: mocks.authorizeApiRequest,
  apiKeyAuthorizationErrorResponse: vi.fn(),
}));
vi.mock("@/lib/server/services/routing", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/server/services/routing")>();
  return { ...actual, getRoute: mocks.getRoute };
});

import { POST } from "@/app/api/v1/route/route";

function request(body: unknown, contentLength?: number) {
  const serialized = JSON.stringify(body);
  return new NextRequest("http://localhost/api/v1/route", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(contentLength === undefined ? {} : { "Content-Length": String(contentLength) }),
    },
    body: serialized,
  });
}

describe("POST /api/v1/route", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.authorizeApiRequest.mockResolvedValue({ valid: true, keyId: "key" });
    mocks.getRoute.mockResolvedValue({
      routes: [],
      rawResponse: { trip: { shape: "_izlhA~rlgdF_{geC~ywl@", legs: [], summary: {} } },
    });
  });

  it("rejects an oversized request before calling Valhalla", async () => {
    const response = await POST(
      request({ locations: [], costing: "auto" }, 32 * 1024 + 1)
    );

    expect(response.status).toBe(413);
    expect(mocks.getRoute).not.toHaveBeenCalled();
  });

  it("rejects out-of-range coordinates and excess fields", async () => {
    const response = await POST(
      request({
        locations: [
          { lat: 95, lon: 0 },
          { lat: 40, lon: -105 },
        ],
        costing: "auto",
        persist: true,
      })
    );

    expect(response.status).toBe(422);
    expect(mocks.getRoute).not.toHaveBeenCalled();
  });

  it("forwards only a validated ephemeral route request", async () => {
    const body = {
      locations: [
        { lat: 39.7, lon: -105 },
        { lat: 40, lon: -104.8 },
      ],
      costing: "auto",
    };
    const response = await POST(request(body));

    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(mocks.getRoute).toHaveBeenCalledWith(body);
  });
});
