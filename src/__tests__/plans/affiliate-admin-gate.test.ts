// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: vi.fn() }));

import type { Context } from "@/lib/server/trpc/init";
import { affiliatesRouter } from "@/lib/server/trpc/routers/affiliates";

const APPLICATION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

function caller(role: string) {
  return affiliatesRouter.createCaller({
    db: {} as Context["db"],
    session: {
      expires: "2099-01-01T00:00:00.000Z",
      user: { id: "user-1", platformRole: role },
    },
  } as Context);
}

describe("affiliate admin gate", () => {
  it("refuses approve, decline, and the review list unless the role is admin", async () => {
    const affiliate = caller("affiliate");
    await expect(affiliate.listApplications()).rejects.toMatchObject({ code: "FORBIDDEN" });
    await expect(affiliate.approve({ applicationId: APPLICATION_ID })).rejects.toMatchObject({
      code: "FORBIDDEN",
    });
    await expect(affiliate.decline({ applicationId: APPLICATION_ID })).rejects.toMatchObject({
      code: "FORBIDDEN",
    });
  });
});
