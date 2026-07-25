import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const mocks = vi.hoisted(() => {
  class UnknownTrackingAssetError extends Error {}
  class DuplicateTrackingPositionError extends Error {}
  return {
    DuplicateTrackingPositionError,
    UnknownTrackingAssetError,
    persistVerifiedPosition: vi.fn(),
    publishRequired: vi.fn(),
  };
});

vi.mock("@/lib/server/services/tracking", () => ({
  DuplicateTrackingPositionError: mocks.DuplicateTrackingPositionError,
  UnknownTrackingAssetError: mocks.UnknownTrackingAssetError,
  persistVerifiedPosition: mocks.persistVerifiedPosition,
}));
vi.mock("@/lib/server/services/realtime", () => ({
  publishRequired: mocks.publishRequired,
}));

import { POST } from "@/app/api/ws/route";

const INGEST_SECRET = "tracking-test-secret-with-sufficient-entropy";
const assetId = "11111111-1111-4111-8111-111111111111";

function request(body: unknown, authorized = true) {
  return new NextRequest("http://localhost/api/ws", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(authorized ? { "x-ingest-secret": INGEST_SECRET } : {}),
    },
    body: JSON.stringify(body),
  });
}

function position() {
  return {
    assetId,
    lat: 39.7392,
    lon: -104.9903,
    timestamp: new Date().toISOString(),
  };
}

describe("POST /api/ws", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    process.env.INGEST_SECRET = INGEST_SECRET;
    mocks.persistVerifiedPosition.mockResolvedValue({
      producerTimestamp: new Date("2026-07-20T10:00:00.000Z"),
      receivedAt: new Date("2026-07-20T10:00:01.000Z"),
    });
    mocks.publishRequired.mockResolvedValue(undefined);
  });

  it("rejects an unauthenticated producer before persistence", async () => {
    const response = await POST(request(position(), false));

    expect(response.status).toBe(401);
    expect(mocks.persistVerifiedPosition).not.toHaveBeenCalled();
  });

  it("never publishes or reports success when persistence fails", async () => {
    mocks.persistVerifiedPosition.mockRejectedValueOnce(new Error("database offline"));

    const response = await POST(request(position()));

    expect(response.status).toBe(503);
    expect(mocks.publishRequired).not.toHaveBeenCalled();
    expect(await response.json()).toEqual({ error: "Tracking position could not be persisted" });
  });

  it("persists a verified position before publishing it", async () => {
    const response = await POST(request(position()));

    expect(response.status).toBe(201);
    expect(mocks.persistVerifiedPosition).toHaveBeenCalledOnce();
    expect(mocks.publishRequired).toHaveBeenCalledWith(
      `tracking:${assetId}`,
      expect.objectContaining({ assetId, producerTimestamp: "2026-07-20T10:00:00.000Z" })
    );
    expect(mocks.persistVerifiedPosition.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.publishRequired.mock.invocationCallOrder[0]
    );
  });

  it("reports a durable but degraded result when realtime publish fails", async () => {
    mocks.publishRequired.mockRejectedValueOnce(new Error("redis offline"));

    const response = await POST(request(position()));
    const body = await response.json();

    expect(response.status).toBe(202);
    expect(body).toMatchObject({ ok: true, persisted: true, broadcast: false });
  });
});
