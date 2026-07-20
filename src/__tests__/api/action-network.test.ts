import { describe, expect, it } from "vitest";
import { GET } from "@/app/api/v1/action-network/route";

describe("GET /api/v1/action-network", () => {
  it("fails closed until reviewed partner waypoints are published", async () => {
    const response = await GET();

    expect(response.status).toBe(503);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
    await expect(response.json()).resolves.toEqual({
      error: {
        code: "ACTION_NETWORK_INACTIVE",
        message:
          "Action-network waypoints are inactive until a reviewed partner-scoped warehouse publication is available",
        retryable: false,
      },
    });
  });
});
