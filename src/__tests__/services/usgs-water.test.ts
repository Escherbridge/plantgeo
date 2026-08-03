import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/http/bounded-upstream", () => ({
  fetchBoundedJson: vi.fn(),
}));

import { fetchBoundedJson } from "@/lib/server/http/bounded-upstream";
import { getGroundwaterWells, getStreamflowGauges } from "@/lib/server/services/usgs-water";

const fetchBoundedJsonMock = vi.mocked(fetchBoundedJson);

/** Build a minimal NWIS time series fixture for one site. */
function timeSeriesFixture(siteNo: string, latitude: number, longitude: number) {
  return {
    sourceInfo: {
      siteName: `Site ${siteNo}`,
      siteCode: [{ value: siteNo }],
      geoLocation: { geogLocation: { latitude, longitude } },
    },
    values: [
      {
        value: [{ value: "42", dateTime: "2026-08-02T00:00:00.000Z" }],
      },
    ],
    variable: { variableCode: [{ value: "00060" }] },
  };
}

/** Build a minimal NWIS response wrapping the given time series. */
function nwisResponse(timeSeries: ReturnType<typeof timeSeriesFixture>[]) {
  return { value: { timeSeries } };
}

/** Extract the bBox query parameter from a fetchBoundedJson call's URL argument. */
function requestedBbox(callArgs: unknown[]): string {
  const url = callArgs[0] as string;
  const match = url.match(/[?&]bBox=([^&]+)/);
  if (!match) throw new Error(`no bBox parameter in url: ${url}`);
  return decodeURIComponent(match[1]);
}

describe("USGS NWIS bbox tiling", () => {
  afterEach(() => {
    fetchBoundedJsonMock.mockReset();
  });

  it("issues exactly one request for a small bbox", async () => {
    fetchBoundedJsonMock.mockResolvedValue(nwisResponse([timeSeriesFixture("001", 45, -110)]));

    const gauges = await getStreamflowGauges("-112,44,-110,46");

    expect(fetchBoundedJsonMock).toHaveBeenCalledTimes(1);
    expect(gauges).toHaveLength(1);
    expect(gauges[0].siteNo).toBe("001");
  });

  it("tiles the PNW bbox into multiple sub-4-degree requests covering the full extent", async () => {
    fetchBoundedJsonMock.mockResolvedValue(nwisResponse([]));

    await getGroundwaterWells("-125,42,-111,49");

    expect(fetchBoundedJsonMock.mock.calls.length).toBeGreaterThan(1);

    let minWest = Infinity;
    let minSouth = Infinity;
    let maxEast = -Infinity;
    let maxNorth = -Infinity;

    for (const call of fetchBoundedJsonMock.mock.calls) {
      const [west, south, east, north] = requestedBbox(call).split(",").map(Number);
      expect(east - west).toBeLessThanOrEqual(4);
      expect(north - south).toBeLessThanOrEqual(4);

      minWest = Math.min(minWest, west);
      minSouth = Math.min(minSouth, south);
      maxEast = Math.max(maxEast, east);
      maxNorth = Math.max(maxNorth, north);
    }

    expect(minWest).toBeCloseTo(-125);
    expect(minSouth).toBeCloseTo(42);
    expect(maxEast).toBeCloseTo(-111);
    expect(maxNorth).toBeCloseTo(49);
  });

  it("merges results from multiple tiles and dedupes by siteNo", async () => {
    fetchBoundedJsonMock
      .mockResolvedValueOnce(
        nwisResponse([
          timeSeriesFixture("001", 45, -124),
          timeSeriesFixture("002", 45, -122),
        ])
      )
      .mockResolvedValue(nwisResponse([timeSeriesFixture("002", 45, -122)]));

    const gauges = await getStreamflowGauges("-125,42,-111,49");

    const siteNumbers = gauges.map((gauge) => gauge.siteNo);
    expect(siteNumbers.filter((siteNo) => siteNo === "002")).toHaveLength(1);
    expect(new Set(siteNumbers).size).toBe(siteNumbers.length);
  });

  it("propagates an error when any tile request fails", async () => {
    fetchBoundedJsonMock
      .mockResolvedValueOnce(nwisResponse([timeSeriesFixture("001", 45, -124)]))
      .mockRejectedValue(new Error("upstream failure"));

    await expect(getGroundwaterWells("-125,42,-111,49")).rejects.toThrow("upstream failure");
  });
});
