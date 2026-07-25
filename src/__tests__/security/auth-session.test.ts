import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getServerSession: vi.fn(),
  authOptions: { marker: "shared-options" },
}));

vi.mock("next-auth", () => ({
  getServerSession: mocks.getServerSession,
}));

vi.mock("@/lib/server/auth-options", () => ({
  authOptions: mocks.authOptions,
}));

import { getServerSession } from "@/lib/server/auth";

describe("server session configuration", () => {
  beforeEach(() => {
    mocks.getServerSession.mockReset();
  });

  it("always passes the shared NextAuth options", async () => {
    mocks.getServerSession.mockResolvedValue(null);
    await getServerSession();
    expect(mocks.getServerSession).toHaveBeenCalledWith(mocks.authOptions);
  });
});
