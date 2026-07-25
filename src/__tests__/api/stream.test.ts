import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const mocks = vi.hoisted(() => ({
  getServerSession: vi.fn(),
  select: vi.fn(),
  subscribe: vi.fn(),
  unsubscribe: vi.fn(),
}));

vi.mock("@/lib/server/auth", () => ({
  getServerSession: mocks.getServerSession,
}));
vi.mock("@/lib/server/db", () => ({
  db: { select: mocks.select },
}));
vi.mock("@/lib/server/services/realtime", () => ({
  subscribe: mocks.subscribe,
  unsubscribe: mocks.unsubscribe,
}));

import {
  GET,
  alertStreamChannel,
} from "@/app/api/stream/[layerId]/route";

function layerQuery(rows: unknown[]) {
  return {
    from: () => ({
      where: () => ({ limit: () => Promise.resolve(rows) }),
    }),
  };
}

describe("authenticated live streams", () => {
  beforeEach(() => {
    mocks.getServerSession.mockReset();
    mocks.select.mockReset();
    mocks.subscribe.mockReset().mockResolvedValue(undefined);
    mocks.unsubscribe.mockReset().mockResolvedValue(undefined);
  });

  it("rejects unauthenticated connections", async () => {
    mocks.getServerSession.mockResolvedValue(null);
    const response = await GET(
      new NextRequest("http://localhost/api/stream/fire-detections"),
      { params: Promise.resolve({ layerId: "fire-detections" }) }
    );
    expect(response.status).toBe(401);
    expect(mocks.subscribe).not.toHaveBeenCalled();
  });

  it("maps personal and global alerts to their publisher channels", () => {
    expect(alertStreamChannel("alerts", "user-7")).toBe("alerts:user-7");
    expect(alertStreamChannel("alerts:global", "user-7")).toBe("alerts:global");
  });

  it("authorizes a public layer and subscribes by its canonical name", async () => {
    mocks.getServerSession.mockResolvedValue({ user: { id: "user-7" } });
    mocks.select.mockReturnValue(
      layerQuery([
        {
          id: "3c9fe4b6-a77c-4ae8-9fe3-aeef3f33ec11",
          name: "fire-detections",
          isPublic: true,
          teamId: null,
        },
      ])
    );

    const response = await GET(
      new NextRequest("http://localhost/api/stream/fire-detections"),
      { params: Promise.resolve({ layerId: "fire-detections" }) }
    );
    expect(response.status).toBe(200);
    expect(mocks.subscribe).toHaveBeenCalledWith(
      "layer:fire-detections",
      expect.any(Function)
    );
    await response.body?.cancel();
  });

  it("declares streams live-only instead of claiming replay from Last-Event-ID", async () => {
    mocks.getServerSession.mockResolvedValue({ user: { id: "user-7" } });
    mocks.select.mockReturnValue(
      layerQuery([
        {
          id: "3c9fe4b6-a77c-4ae8-9fe3-aeef3f33ec11",
          name: "fire-detections",
          isPublic: true,
          teamId: null,
        },
      ])
    );

    const response = await GET(
      new NextRequest("http://localhost/api/stream/fire-detections", {
        headers: { "Last-Event-ID": "99" },
      }),
      { params: Promise.resolve({ layerId: "fire-detections" }) }
    );
    const reader = response.body?.getReader();
    const first = await reader?.read();

    expect(new TextDecoder().decode(first?.value)).toContain(
      'data: {"stream":"fire-detections","resumable":false}'
    );
    await reader?.cancel();
  });

  it("returns unavailable without announcing a connection when subscribe fails", async () => {
    mocks.getServerSession.mockResolvedValue({ user: { id: "user-7" } });
    mocks.select.mockReturnValue(
      layerQuery([
        {
          id: "3c9fe4b6-a77c-4ae8-9fe3-aeef3f33ec11",
          name: "fire-detections",
          isPublic: true,
          teamId: null,
        },
      ])
    );
    mocks.subscribe.mockRejectedValueOnce(new Error("Redis unavailable"));

    const response = await GET(
      new NextRequest("http://localhost/api/stream/fire-detections"),
      { params: Promise.resolve({ layerId: "fire-detections" }) }
    );

    expect(response.status).toBe(503);
    await expect(response.text()).resolves.not.toContain("event: connected");
    expect(mocks.unsubscribe).not.toHaveBeenCalled();
  });

  it("denies a private team layer to a non-member", async () => {
    mocks.getServerSession.mockResolvedValue({ user: { id: "user-7" } });
    mocks.select
      .mockReturnValueOnce(
        layerQuery([
          {
            id: "3c9fe4b6-a77c-4ae8-9fe3-aeef3f33ec11",
            name: "private-observations",
            isPublic: false,
            teamId: "c78bbe92-6b52-41e5-b5f5-3272f818438b",
          },
        ])
      )
      .mockReturnValueOnce(layerQuery([]));

    const response = await GET(
      new NextRequest("http://localhost/api/stream/private-observations"),
      { params: Promise.resolve({ layerId: "private-observations" }) }
    );
    expect(response.status).toBe(403);
    expect(mocks.subscribe).not.toHaveBeenCalled();
  });
});
