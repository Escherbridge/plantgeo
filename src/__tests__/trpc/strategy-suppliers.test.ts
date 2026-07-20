import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Context } from "@/lib/server/trpc/init";

vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: vi.fn() }));

import { strategyRouter } from "@/lib/server/trpc/routers/strategy";

function authenticatedContext(): Context {
  return {
    db: {} as Context["db"],
    session: {
      expires: "2099-01-01T00:00:00.000Z",
      user: {
        id: "11111111-1111-4111-8111-111111111111",
      } as NonNullable<Context["session"]>["user"],
    },
  };
}

describe("strategy.getStrategySuppliers", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("fails closed before making an external supplier request", async () => {
    const caller = strategyRouter.createCaller(authenticatedContext());

    await expect(
      caller.getStrategySuppliers({
        strategyId: "reforestation",
        lat: 39.7392,
        lon: -104.9903,
      })
    ).rejects.toMatchObject({
      code: "PRECONDITION_FAILED",
      message:
        "Partner supplier matching is inactive until reviewed directory data, entitlement, and outbound-location consent are available",
    });
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});
