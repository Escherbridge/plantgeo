// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const getToken = vi.hoisted(() => vi.fn());
vi.mock("next-auth/jwt", () => ({ getToken }));

import { middleware } from "@/middleware";

describe("dashboard authentication with optional organizations", () => {
  beforeEach(() => getToken.mockReset());

  it.each(["/dashboard", "/dashboard/conversations", "/dashboard/org", "/dashboard/org/settings", "/onboarding"])(
    "still requires authentication for %s even with an old skip cookie",
    async (path) => {
      getToken.mockResolvedValue(null);
      const response = await middleware(new NextRequest(`https://plantgeo.example${path}`, {
        headers: { cookie: "pg_onboarding_skipped=1" },
      }));
      const destination = new URL(response.headers.get("location")!);
      expect(destination.pathname).toBe("/login");
      expect(destination.searchParams.get("callbackUrl")).toBe(path);
    }
  );

  it.each(["/dashboard", "/dashboard/conversations", "/dashboard/org", "/onboarding"])(
    "admits an authenticated individual to %s without a skip cookie",
    async (path) => {
      getToken.mockResolvedValue({ sub: "individual-user", activeTeamId: null });
      const response = await middleware(new NextRequest(`https://plantgeo.example${path}`));
      expect(response.headers.get("location")).toBeNull();
      expect(response.headers.get("x-middleware-next")).toBe("1");
    }
  );
});
