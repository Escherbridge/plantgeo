import { describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const aggregateActivityGrid = vi.fn();

vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/services/community-activity", async () => {
  const actual = await vi.importActual<
    typeof import("@/lib/server/services/community-activity")
  >("@/lib/server/services/community-activity");
  return { ...actual, aggregateActivityGrid };
});

const { GET } = await import("@/app/api/v1/action-network/route");

function get(query: string) {
  return GET(new NextRequest(`http://localhost/api/v1/action-network?${query}`));
}

describe("GET /api/v1/action-network", () => {
  it("returns an empty feature collection when no requests exist", async () => {
    aggregateActivityGrid.mockResolvedValue({
      cells: [],
      cellDegrees: 0.01,
      observedAt: null,
    });

    const response = await get("bbox=-125,42,-116,49&zoom=8");

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("application/geo+json");
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
    expect(response.headers.get("x-data-freshness")).toBe("no-data");
    expect(response.headers.get("x-data-observed-at")).toBeNull();
    await expect(response.json()).resolves.toEqual({
      type: "FeatureCollection",
      features: [],
    });
  });

  it("returns aggregated cells, never per-request coordinates", async () => {
    aggregateActivityGrid.mockResolvedValue({
      cells: [{ longitude: -116.2, latitude: 43.6, featureCount: 4, voteCount: 9 }],
      cellDegrees: 0.01,
      observedAt: new Date("2026-08-01T00:00:00.000Z"),
    });

    const response = await get("bbox=-125,42,-116,49&zoom=8");
    const body = (await response.json()) as GeoJSON.FeatureCollection;

    expect(response.headers.get("x-data-observed-at")).toBe(
      "2026-08-01T00:00:00.000Z"
    );
    expect(body.features).toHaveLength(1);
    expect(body.features[0].properties).toEqual({ voteCount: 9, featureCount: 4 });
  });

  it("rejects a malformed bbox", async () => {
    const response = await get("bbox=not-a-bbox&zoom=8");
    expect(response.status).toBe(400);
  });
});
