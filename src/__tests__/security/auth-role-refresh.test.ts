import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  currentRole: "contributor" as string | null,
}));

vi.mock("@auth/drizzle-adapter", () => ({
  DrizzleAdapter: vi.fn(() => ({})),
}));

vi.mock("@/lib/server/db", () => ({
  db: {
    select: vi.fn(() => ({
      from: vi.fn(() => ({
        where: vi.fn(() => ({
          limit: vi.fn(async () => [{ platformRole: mocks.currentRole }]),
        })),
      })),
    })),
  },
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
      token: { id: USER_ID, sub: USER_ID, platformRole: "admin" },
      user: { id: USER_ID, email: "user@example.com", name: "User" },
      account: null,
    });

    expect(token).toMatchObject({
      id: USER_ID,
      platformRole: "contributor",
    });
  });
});
