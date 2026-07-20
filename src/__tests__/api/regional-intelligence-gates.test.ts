import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const mocks = vi.hoisted(() => ({ getServerSession: vi.fn() }));

vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({
  getServerSession: mocks.getServerSession,
}));

import { POST } from "@/app/api/ai/regional-intelligence/route";
import { regionalEvidenceFreshnessState } from "@/lib/regional-intelligence";

describe("regional intelligence fail-closed gates", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("classifies future regional evidence timestamps as unavailable", () => {
    const now = Date.parse("2026-07-20T12:00:00.000Z");
    const futureTimestamp = new Date(now + 60_000).toISOString();

    expect(
      regionalEvidenceFreshnessState("drought", futureTimestamp, now)
    ).toBe("unavailable");
  });

  it("returns a non-retryable, no-store response while serving is inactive", async () => {
    const response = await POST(
      new NextRequest("https://plantgeo.test/api/ai/regional-intelligence", {
        method: "POST",
      })
    );

    expect(response.status).toBe(503);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
    await expect(response.json()).resolves.toEqual({
      error:
        "Regional intelligence is paused until warehouse-backed evidence and its provenance contract are published",
      retryable: false,
    });
    expect(mocks.getServerSession).not.toHaveBeenCalled();
  });
});
