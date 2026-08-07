import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getPublishedFireDetections: vi.fn(),
  firmsDayRange: vi.fn(),
}));

vi.mock("@/lib/server/services/environmental-read-model", () => ({
  getPublishedFireDetections: mocks.getPublishedFireDetections,
}));

vi.mock("@/lib/server/services/environmental-time", () => ({
  firmsDayRange: mocks.firmsDayRange,
}));

import { GET } from "@/app/api/fires/route";

const LIVE_WINDOW = { start: "2026-08-04", end: "2026-08-06" };

function firesRequest(query = "") {
  return GET(new Request(`https://plantgeo.test/api/fires${query}`));
}

function emptyCollection() {
  return { type: "FeatureCollection", features: [] };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.firmsDayRange.mockReturnValue(LIVE_WINDOW);
  mocks.getPublishedFireDetections.mockResolvedValue(emptyCollection());
});

describe("GET /api/fires date handling", () => {
  it("answers the live window when no day is requested", async () => {
    const response = await firesRequest();

    expect(response.status).toBe(200);
    expect(mocks.getPublishedFireDetections).toHaveBeenCalledWith(
      undefined,
      LIVE_WINDOW,
      undefined
    );
    expect(response.headers.get("Cache-Control")).toContain("max-age=30");
  });

  it("forwards a well-formed day and caches it as historical", async () => {
    const response = await firesRequest("?date=2023-06-01");

    expect(response.status).toBe(200);
    expect(mocks.getPublishedFireDetections).toHaveBeenCalledWith(
      undefined,
      LIVE_WINDOW,
      "2023-06-01"
    );
    expect(response.headers.get("Cache-Control")).toContain("max-age=3600");
  });

  it.each([
    ["a non-padded day", "?date=2023-6-1"],
    ["a slashed day", "?date=2023%2F06%2F01"],
    ["an instant", "?date=2023-06-01T00:00:00Z"],
    ["a word", "?date=yesterday"],
    ["an empty value", "?date="],
    ["whitespace only", "?date=%20%20"],
  ])("refuses %s rather than substituting the live window", async (_label, query) => {
    const response = await firesRequest(query);

    // The whole point: a malformed day must never be answered with a DIFFERENT day's
    // detections, which is what silently dropping the parameter used to do.
    expect(response.status).toBe(400);
    expect(mocks.getPublishedFireDetections).not.toHaveBeenCalled();

    const body = await response.json();
    expect(body).toMatchObject({
      type: "FeatureCollection",
      features: [],
      availability: "unavailable",
      reason: "invalid_date_parameter",
    });
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(response.headers.get("X-Content-Type-Options")).toBe("nosniff");
  });

  it("keeps the stable error shape when the read model is unavailable", async () => {
    mocks.getPublishedFireDetections.mockRejectedValue(new Error("database is down"));

    const response = await firesRequest("?date=2023-06-01");

    expect(response.status).toBe(503);
    expect(await response.json()).toMatchObject({
      availability: "unavailable",
      reason: "published_fire_observations_unavailable",
    });
    expect(response.headers.get("Retry-After")).toBe("30");
  });
});
