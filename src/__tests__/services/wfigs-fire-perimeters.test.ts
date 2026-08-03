import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchWfigsFirePerimeters } from "@/lib/server/services/wfigs-fire-perimeters";

const mocks = vi.hoisted(() => ({
  fetchBoundedJson: vi.fn(),
}));

vi.mock("@/lib/server/http/bounded-upstream", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/server/http/bounded-upstream")>();
  return { ...actual, fetchBoundedJson: mocks.fetchBoundedJson };
});

import { UpstreamHttpError } from "@/lib/server/http/bounded-upstream";

const BBOX = "-114,44,-113,45";

/** ArcGIS's HTTP-200 throttling payload — the "busy" error shape. */
function busyPayloadFixture() {
  return { error: { code: 429, message: "Unable to perform query. Too many requests." } };
}

/** A minimal valid WFIGS feature collection with one fully-populated feature. */
function featureCollectionFixture() {
  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: {
          type: "Polygon",
          coordinates: [
            [
              [-114, 44],
              [-114, 45],
              [-113, 45],
              [-114, 44],
            ],
          ],
        },
        properties: {
          attr_UniqueFireIdentifier: "2026-IDBOF-000123",
          attr_IrwinID: "irwin-abc-123",
          poly_IncidentName: "Test Ridge Fire",
          attr_FireDiscoveryDateTime: 1_753_000_000_000,
          poly_GISAcres: 1234.5,
          attr_FireCause: "Lightning",
          poly_PolygonDateTime: 1_753_100_000_000,
          attr_IncidentTypeCategory: "WF",
          attr_POOState: "US-ID",
          attr_PercentContained: 42,
        },
      },
    ],
  };
}

describe("WFIGS fire perimeter acquisition retry policy", () => {
  afterEach(() => {
    mocks.fetchBoundedJson.mockReset();
    vi.useRealTimers();
  });

  it("maps a healthy response into the WfigsFirePerimeter shape", async () => {
    mocks.fetchBoundedJson.mockResolvedValueOnce(featureCollectionFixture());

    const collection = await fetchWfigsFirePerimeters(BBOX);

    expect(mocks.fetchBoundedJson).toHaveBeenCalledTimes(1);
    expect(collection.features).toHaveLength(1);
    const [feature] = collection.features;
    expect(feature.uniqueFireIdentifier).toBe("2026-IDBOF-000123");
    expect(feature.percentContained).toBe(42);
    expect(feature.geometry).toEqual({
      type: "Polygon",
      coordinates: [
        [
          [-114, 44],
          [-114, 45],
          [-113, 45],
          [-114, 44],
        ],
      ],
    });
  });

  it("retries once after an ArcGIS busy payload and returns the eventual success", async () => {
    vi.useFakeTimers();
    mocks.fetchBoundedJson
      .mockResolvedValueOnce(busyPayloadFixture())
      .mockResolvedValueOnce(featureCollectionFixture());

    const resultPromise = fetchWfigsFirePerimeters(BBOX);
    await vi.advanceTimersByTimeAsync(5_000);
    const collection = await resultPromise;

    expect(mocks.fetchBoundedJson).toHaveBeenCalledTimes(2);
    expect(collection.features).toHaveLength(1);
  });

  it("gives up after three consecutive busy payloads", async () => {
    vi.useFakeTimers();
    mocks.fetchBoundedJson.mockResolvedValue(busyPayloadFixture());

    const resultPromise = fetchWfigsFirePerimeters(BBOX);
    resultPromise.catch(() => {});
    await vi.advanceTimersByTimeAsync(10_000);

    await expect(resultPromise).rejects.toThrow(/too many requests/i);
    expect(mocks.fetchBoundedJson).toHaveBeenCalledTimes(3);
  });

  it("throws immediately on a schema mismatch without retrying", async () => {
    mocks.fetchBoundedJson.mockResolvedValueOnce({ garbage: true });

    await expect(fetchWfigsFirePerimeters(BBOX)).rejects.toThrow(
      "WFIGS API returned an unexpected feature collection shape"
    );
    expect(mocks.fetchBoundedJson).toHaveBeenCalledTimes(1);
  });

  it("retries an HTTP 429 UpstreamHttpError and succeeds on the next attempt", async () => {
    vi.useFakeTimers();
    mocks.fetchBoundedJson
      .mockRejectedValueOnce(new UpstreamHttpError(429))
      .mockResolvedValueOnce(featureCollectionFixture());

    const resultPromise = fetchWfigsFirePerimeters(BBOX);
    await vi.advanceTimersByTimeAsync(5_000);
    const collection = await resultPromise;

    expect(mocks.fetchBoundedJson).toHaveBeenCalledTimes(2);
    expect(collection.features).toHaveLength(1);
  });

  it("does not retry an HTTP 404 UpstreamHttpError", async () => {
    mocks.fetchBoundedJson.mockRejectedValueOnce(new UpstreamHttpError(404));

    await expect(fetchWfigsFirePerimeters(BBOX)).rejects.toThrow(
      "Upstream request failed with status 404"
    );
    expect(mocks.fetchBoundedJson).toHaveBeenCalledTimes(1);
  });
});
