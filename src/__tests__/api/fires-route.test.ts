import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getPublishedFireDetections: vi.fn(),
  firmsDayRange: vi.fn(),
  serverCurrentDate: vi.fn(),
}));

vi.mock("@/lib/server/services/environmental-read-model", () => ({
  getPublishedFireDetections: mocks.getPublishedFireDetections,
  serverCurrentDate: mocks.serverCurrentDate,
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

function pointCollection(featureIds: string[]) {
  return {
    type: "FeatureCollection",
    features: featureIds.map((id) => ({
      type: "Feature",
      properties: { id },
      geometry: { type: "Point", coordinates: [0, 0] },
    })),
  };
}

/** An arbitrary fixed "today", decoupled from the real clock the same way `serverCurrentDate`
 *  is mocked below -- any test asserting live-vs-historical branching pins today to this. */
const TODAY = "2026-08-16";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.firmsDayRange.mockReturnValue(LIVE_WINDOW);
  mocks.getPublishedFireDetections.mockResolvedValue(emptyCollection());
  mocks.serverCurrentDate.mockReturnValue(TODAY);
});

describe("GET /api/fires date handling", () => {
  it("answers the live window when no day is requested", async () => {
    const response = await firesRequest();

    expect(response.status).toBe(200);
    // 4th arg is `today`, threaded through so the route and the read model's OWN day
    // resolution can never disagree about which day is today across the `await` -- see F4.
    expect(mocks.getPublishedFireDetections).toHaveBeenCalledWith(
      undefined,
      LIVE_WINDOW,
      undefined,
      TODAY
    );
    expect(response.headers.get("Cache-Control")).toContain("max-age=30");
  });

  it("forwards a well-formed day and caches it as historical", async () => {
    const response = await firesRequest("?date=2023-06-01");

    expect(response.status).toBe(200);
    expect(mocks.getPublishedFireDetections).toHaveBeenCalledWith(
      undefined,
      LIVE_WINDOW,
      "2023-06-01",
      TODAY
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

// D7: an omitted day and the server's own today must reach the identical cache contract, not
// just the identical query -- see the `isLiveWindow` doc comment in route.ts.
describe("GET /api/fires live-window cache canonicalization", () => {
  it("caches an explicit day equal to the server's today as the LIVE window, not historical", async () => {
    const response = await firesRequest(`?date=${TODAY}`);

    expect(response.status).toBe(200);
    // Still forwarded verbatim -- the read model resolves it, this route does not re-decide it.
    expect(mocks.getPublishedFireDetections).toHaveBeenCalledWith(
      undefined,
      LIVE_WINDOW,
      TODAY,
      TODAY
    );
    expect(response.headers.get("Cache-Control")).toContain("max-age=30");
    expect(response.headers.get("Cache-Control")).not.toContain("max-age=3600");
  });

  it("mints the same ETag/x-data-revision key for an omitted day and an explicit today", async () => {
    mocks.getPublishedFireDetections.mockResolvedValue(pointCollection(["a"]));

    const omitted = await firesRequest();
    const explicitToday = await firesRequest(`?date=${TODAY}`);

    expect(explicitToday.headers.get("ETag")).toBe(omitted.headers.get("ETag"));
    expect(explicitToday.headers.get("x-data-revision")).toBe(
      omitted.headers.get("x-data-revision")
    );
  });

  it("still caches a real past day as historical", async () => {
    const response = await firesRequest("?date=2023-06-01");

    expect(response.headers.get("Cache-Control")).toContain("max-age=3600");
    expect(response.headers.get("ETag")).not.toContain("fire-live-");
  });

  // F4: reads "today" exactly once per request and threads that same value into
  // getPublishedFireDetections, rather than reading it again after the DB round trip. A second,
  // independent read could straddle a day boundary and disagree with the first.
  it("reads serverCurrentDate exactly once per request, not once for the cache decision and again for the query", async () => {
    await firesRequest("?date=2023-06-01");

    expect(mocks.serverCurrentDate).toHaveBeenCalledTimes(1);
  });

  it("a 304 for an explicit today carries the LIVE cache-control, matching its 200", async () => {
    mocks.getPublishedFireDetections.mockResolvedValue(pointCollection(["a"]));
    const first = await firesRequest(`?date=${TODAY}`);
    const etag = first.headers.get("ETag")!;

    const second = await GET(
      new Request(`https://plantgeo.test/api/fires?date=${TODAY}`, {
        headers: { "if-none-match": etag },
      })
    );

    expect(second.status).toBe(304);
    expect(second.headers.get("Cache-Control")).toContain("max-age=30");
  });
});

// C5: the ETag/x-data-revision must be a content fingerprint, not the feature count, or a
// corrective re-ingest that preserves row count 304s forever.
describe("GET /api/fires ETag content sensitivity", () => {
  it("changes the ETag when content changes but the feature count does not", async () => {
    mocks.getPublishedFireDetections.mockResolvedValueOnce(pointCollection(["a"]));
    const first = await firesRequest("?date=2023-06-01");

    mocks.getPublishedFireDetections.mockResolvedValueOnce(pointCollection(["b"]));
    const second = await firesRequest("?date=2023-06-01");

    expect(first.headers.get("ETag")).not.toBe(second.headers.get("ETag"));
  });

  it("repeats the same ETag for byte-identical content", async () => {
    mocks.getPublishedFireDetections.mockResolvedValue(pointCollection(["a", "b"]));

    const first = await firesRequest("?date=2023-06-01");
    const second = await firesRequest("?date=2023-06-01");

    expect(first.headers.get("ETag")).toBe(second.headers.get("ETag"));
  });
});
