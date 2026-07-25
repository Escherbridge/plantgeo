import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/db", () => ({ db: {} }));

import {
  apiKeyAuthorizationErrorResponse,
  applyApiKeyRateLimit,
  type ApiKeyAuthorizationSuccess,
} from "@/lib/server/middleware/api-auth";

const principal: ApiKeyAuthorizationSuccess = {
  valid: true,
  keyId: "key-7",
  userId: "user-7",
  permissions: ["read:layers"],
  rateLimit: 100,
};

describe("API-key limiter authorization", () => {
  it("fails closed with typed 503 semantics while retaining the principal", async () => {
    const result = applyApiKeyRateLimit(principal, { available: false });

    expect(result).toMatchObject({
      valid: false,
      keyId: "key-7",
      userId: "user-7",
      status: 503,
      retryAfter: 30,
    });
    if (result.valid) throw new Error("Expected limiter failure");

    const response = apiKeyAuthorizationErrorResponse(result);
    expect(response.status).toBe(503);
    expect(response.headers.get("retry-after")).toBe("30");
    await expect(response.json()).resolves.toEqual({
      error: "API key request limiter is unavailable",
    });
  });

  it("returns the typed principal when the limiter permits the request", () => {
    expect(
      applyApiKeyRateLimit(principal, {
        available: true,
        limited: false,
      })
    ).toBe(principal);
  });
});
