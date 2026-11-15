// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
  const state = {
    selects: [] as unknown[][],
    updates: [] as Record<string, unknown>[],
  };

  function query() {
    const builder: Record<string, unknown> = {
      from: () => builder,
      leftJoin: () => builder,
      where: () => builder,
      orderBy: () => builder,
      limit: () => builder,
      then(resolve: (rows: unknown[]) => unknown, reject?: (reason: unknown) => unknown) {
        return Promise.resolve(state.selects.shift() ?? []).then(resolve, reject);
      },
    };
    return builder;
  }

  function update() {
    const builder: Record<string, unknown> = {
      set(values: Record<string, unknown>) {
        state.updates.push(values);
        return builder;
      },
      where: () => builder,
      then(resolve: (rows: unknown[]) => unknown, reject?: (reason: unknown) => unknown) {
        return Promise.resolve([]).then(resolve, reject);
      },
    };
    return builder;
  }

  return {
    state,
    db: { select: vi.fn(() => query()), update: vi.fn(() => update()) },
  };
});

vi.mock("@auth/drizzle-adapter", () => ({ DrizzleAdapter: vi.fn(() => ({})) }));
vi.mock("@/lib/server/db", () => ({ db: mocks.db }));
vi.mock("@/lib/server/password", () => ({ verifyPassword: vi.fn() }));

import { authOptions } from "@/lib/server/auth-options";

const USER_ID = "11111111-1111-4111-8111-111111111111";

const tokenFields = {
  id: USER_ID,
  sub: USER_ID,
  platformRole: "contributor",
  activeTeamId: null,
  activeTeamRole: null,
};

describe("affiliate role during sign-in", () => {
  it("writes the affiliate role before the session reads it back", async () => {
    const callback = authOptions.callbacks?.jwt;
    if (!callback) throw new Error("JWT callback is not configured");
    mocks.state.selects = [
      [{ email: "ada@example.com", platformRole: "contributor" }],
      [{ id: "aff-1" }],
      [{ platformRole: "affiliate", activeTeamId: null, memberTeamId: null, memberRole: null }],
    ];
    mocks.state.updates = [];

    const token = await callback({
      token: tokenFields,
      user: { id: USER_ID, email: "ada@example.com", name: "Ada" },
      account: null,
    });

    expect(mocks.state.updates).toEqual([{ userId: USER_ID }, { platformRole: "affiliate" }]);
    expect(token).toMatchObject({ id: USER_ID, platformRole: "affiliate" });
  });

  it("does not grant a role on a later session read", async () => {
    const callback = authOptions.callbacks?.jwt;
    if (!callback) throw new Error("JWT callback is not configured");
    mocks.state.selects = [
      [{ platformRole: "contributor", activeTeamId: null, memberTeamId: null, memberRole: null }],
    ];
    mocks.state.updates = [];

    // A session refresh does not pass `user`. The published callback type still requires it.
    const token = await callback({
      token: tokenFields,
      account: null,
    } as Parameters<typeof callback>[0]);

    expect(mocks.state.updates).toEqual([]);
    expect(token).toMatchObject({ platformRole: "contributor" });
  });
});
