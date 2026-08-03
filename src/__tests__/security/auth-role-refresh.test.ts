import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
  const state = {
    // One row per membership; the identity columns repeat across the join.
    rows: [
      {
        platformRole: "contributor" as string | null,
        activeTeamId: null as string | null,
        memberTeamId: null as string | null,
        memberRole: null as string | null,
      },
    ],
  };

  function query() {
    const builder: Record<string, unknown> = {
      from: () => builder,
      leftJoin: () => builder,
      where: () => builder,
      orderBy: () => builder,
      limit: () => builder,
      then(
        resolve: (rows: unknown[]) => unknown,
        reject?: (reason: unknown) => unknown
      ) {
        return Promise.resolve(state.rows).then(resolve, reject);
      },
    };
    return builder;
  }

  return { state, query };
});

vi.mock("@auth/drizzle-adapter", () => ({
  DrizzleAdapter: vi.fn(() => ({})),
}));

vi.mock("@/lib/server/db", () => ({
  db: { select: vi.fn(() => mocks.query()) },
}));

vi.mock("@/lib/server/password", () => ({
  verifyPassword: vi.fn(),
}));

import { authOptions } from "@/lib/server/auth-options";

const USER_ID = "11111111-1111-4111-8111-111111111111";

describe("session role refresh", () => {
  it("replaces a stale privileged claim with the current database role", async () => {
    const callback = authOptions.callbacks?.jwt;
    expect(callback).toBeDefined();
    if (!callback) throw new Error("JWT callback is not configured");

    const token = await callback({
      token: {
        id: USER_ID,
        sub: USER_ID,
        platformRole: "admin",
        activeTeamId: null,
        activeTeamRole: null,
      },
      user: { id: USER_ID, email: "user@example.com", name: "User" },
      account: null,
    });

    expect(token).toMatchObject({
      id: USER_ID,
      platformRole: "contributor",
    });
  });
});
