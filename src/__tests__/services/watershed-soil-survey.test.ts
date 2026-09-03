import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * HUC12 watersheds are an upstream proxy, so the load-bearing behaviour is which endpoint
 * the service asks for, how it normalizes what comes back, and what it refuses to cache or
 * claim. Only the network and the shared Redis helpers are stubbed; the tRPC procedures run
 * the real services underneath.
 *
 * The SSURGO service is no longer a proxy — it reads the warehouse and warms uncovered
 * grid cells — so its own tests live in `soil-survey-persistence.test.ts`. What stays here
 * is the router-level fault mapping and viewport bounds it shares with watersheds.
 */
const mocks = vi.hoisted(() => ({
  fetchBoundedJson: vi.fn(),
  getCachedGeoJSON: vi.fn(async () => null as unknown),
  cacheGeoJSON: vi.fn(async () => undefined),
  dbExecute: vi.fn(async () => [] as unknown[]),
  /**
   * A plausible `geo.layers.id` for `resolveCachedLayerId`'s own `db.select` call to resolve
   * to. The soil procedure's detail/aggregate/summary tier readers all resolve
   * `SOIL_SURVEY_LAYER_NAME` through it before their own `db.execute` read, so `db.select`
   * must exist on the mocked `db` below -- without it `caller.getSoilSurvey` throws
   * `db.select is not a function` before ever reaching `mocks.dbExecute`.
   */
  fakeLayerId: "11111111-1111-4111-8111-111111111111",
}));

vi.mock("@/lib/server/http/bounded-upstream", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/server/http/bounded-upstream")>();
  return { ...actual, fetchBoundedJson: mocks.fetchBoundedJson };
});

vi.mock("@/lib/server/redis", () => ({
  getCachedGeoJSON: mocks.getCachedGeoJSON,
  cacheGeoJSON: mocks.cacheGeoJSON,
}));

// The SSURGO reader is warehouse-backed, so the router's soil procedure touches the DB
// even here. `transaction` is present because a read-through write path runs inside one;
// the callback gets the same execute stub, which is enough for the router-level cases.
// `select` is shape-agnostic: resolveCachedLayerId's `.select({id}).from(layers).where(...)
// .limit(1)` is the only `db.select` call the soil procedure's readers issue.
vi.mock("@/lib/server/db", () => ({
  db: {
    select: () => ({
      from: () => ({
        where: () => ({
          limit: async () => [{ id: mocks.fakeLayerId }],
        }),
      }),
    }),
    execute: mocks.dbExecute,
    transaction: async (run: (tx: { execute: typeof mocks.dbExecute }) => unknown) =>
      run({ execute: mocks.dbExecute }),
  },
}));
vi.mock("@/lib/server/auth", () => ({ getServerSession: vi.fn() }));

import {
  UpstreamHttpError,
  UpstreamPayloadError,
  UpstreamTimeoutError,
} from "@/lib/server/http/bounded-upstream";
import {
  getWatersheds,
  MAX_WATERSHED_BBOX_SQUARE_DEGREES,
  WatershedResponseError,
} from "@/lib/server/services/hydrosheds";
import { MAX_SOIL_BBOX_SQUARE_DEGREES } from "@/lib/server/services/usda-soil";
import { clearLayerIdCache } from "@/lib/server/services/environmental-read-model";
import type { Context } from "@/lib/server/trpc/init";
import { environmentalRouter } from "@/lib/server/trpc/routers/environmental";

// 0.01 square degrees over Boise -- inside both procedures' viewport ceilings, so the same
// bbox exercises the services and the router.
const BBOX = "-116.35,43.5,-116.25,43.6";

const caller = environmentalRouter.createCaller({
  db: {},
  session: null,
} as unknown as Context);

beforeEach(() => {
  mocks.fetchBoundedJson.mockReset();
  mocks.getCachedGeoJSON.mockReset();
  mocks.getCachedGeoJSON.mockResolvedValue(null);
  mocks.cacheGeoJSON.mockReset();
  mocks.cacheGeoJSON.mockResolvedValue(undefined);
  mocks.dbExecute.mockReset();
  mocks.dbExecute.mockResolvedValue([]);
  // resolveCachedLayerId memoizes a hit in module scope; clearing it keeps every test's
  // resolver call going through the same fixed mock rather than a stale cross-test id.
  clearLayerIdCache();
});

/** The URL the service asked for, as a parsed URL. */
function requestedUrl(): URL {
  return new URL(String(mocks.fetchBoundedJson.mock.calls.at(-1)?.[0]));
}

describe("HUC12 watershed acquisition", () => {
  it("queries WBDHU12 (layer 12), not the NHDPoint layer", async () => {
    mocks.fetchBoundedJson.mockResolvedValue({ type: "FeatureCollection", features: [] });

    await getWatersheds(BBOX);

    const url = requestedUrl();
    // Verified against the live service catalog (`?f=json`): layer 2 is NHDPoint
    // (esriGeometryPoint, fields permanent_identifier/gnis_name/reachcode -- no HUC12 at
    // all) and layer 12 is WBDHU12 (esriGeometryPolygon). Querying 2 returned points that
    // no watershed consumer could label or fill.
    expect(url.host).toBe("hydro.nationalmap.gov");
    expect(url.pathname).toBe(
      "/arcgis/rest/services/NHDPlus_HR/MapServer/12/query"
    );
    expect(url.pathname).not.toContain("MapServer/2/");
  });

  it("asks for bounded geojson geometry inside the requested envelope", async () => {
    mocks.fetchBoundedJson.mockResolvedValue({ type: "FeatureCollection", features: [] });

    await getWatersheds(BBOX);

    const url = requestedUrl();
    expect(url.searchParams.get("f")).toBe("geojson");
    expect(url.searchParams.get("returnGeometry")).toBe("true");
    expect(url.searchParams.get("geometryType")).toBe("esriGeometryEnvelope");
    expect(url.searchParams.get("resultRecordCount")).toBe("500");
    // ~0.1 m rounding; measured to cut a 1 sq deg response from 8.63 MB to 5.07 MB,
    // which is what keeps it inside the service's own byte cap.
    expect(url.searchParams.get("geometryPrecision")).toBe("6");
    expect(JSON.parse(url.searchParams.get("geometry")!)).toEqual({
      xmin: -116.35,
      ymin: 43.5,
      xmax: -116.25,
      ymax: 43.6,
      spatialReference: { wkid: 4326 },
    });
  });

  it("passes the provider's lowercase WBDHU12 attributes through untouched", async () => {
    // The exact property spelling the live layer emits under f=geojson -- the title-case
    // aliases the catalog displays never reach the client, which is what hover-fields.ts
    // and WaterPanel both read.
    mocks.fetchBoundedJson.mockResolvedValue({
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: { type: "Polygon", coordinates: [] },
          properties: {
            name: "Cottonwood Creek-Shafer Creek",
            huc12: "170501220201",
            areasqkm: 112.23,
            tohuc: "170501220203",
          },
        },
      ],
    });

    const collection = await getWatersheds(BBOX);

    expect(collection.features[0].properties).toMatchObject({
      name: "Cottonwood Creek-Shafer Creek",
      huc12: "170501220201",
    });
    expect(collection.truncated).toBe(false);
  });

  it("reports the provider's transfer limit rather than silently serving a subset", async () => {
    // Live at basin scale the layer answers with a top-level exceededTransferLimit
    // alongside exactly resultRecordCount features. Dropping the flag would let the
    // API assert whole-viewport coverage over an arbitrary 500-polygon slice.
    mocks.fetchBoundedJson.mockResolvedValue({
      type: "FeatureCollection",
      exceededTransferLimit: true,
      features: [
        { type: "Feature", geometry: null, properties: { huc12: "170501220201" } },
      ],
    });

    expect((await getWatersheds(BBOX)).truncated).toBe(true);
  });

  it("refuses an ArcGIS 200-with-error body and never caches it", async () => {
    // ArcGIS reports some faults as HTTP 200 plus an `error` object. Caching that for
    // the full TTL would serve the fault back for an hour to the same viewport.
    mocks.fetchBoundedJson.mockResolvedValue({
      error: { code: 400, message: "Unable to complete operation." },
    });

    await expect(getWatersheds(BBOX)).rejects.toBeInstanceOf(WatershedResponseError);
    expect(mocks.cacheGeoJSON).not.toHaveBeenCalled();
  });

  it("serves a validated cache hit without touching the provider", async () => {
    mocks.getCachedGeoJSON.mockResolvedValue({
      type: "FeatureCollection",
      features: [],
      truncated: true,
    });

    expect((await getWatersheds(BBOX)).truncated).toBe(true);
    expect(mocks.fetchBoundedJson).not.toHaveBeenCalled();
  });

  it("re-fetches when the cached value is not a watershed collection", async () => {
    mocks.getCachedGeoJSON.mockResolvedValue({ error: "stale poison" });
    mocks.fetchBoundedJson.mockResolvedValue({ type: "FeatureCollection", features: [] });

    await getWatersheds(BBOX);

    expect(mocks.fetchBoundedJson).toHaveBeenCalledTimes(1);
  });
});

describe("environmental router upstream fault mapping", () => {
  it.each([
    ["a 500", new UpstreamHttpError(500)],
    ["a 429", new UpstreamHttpError(429)],
    ["a timeout", new UpstreamTimeoutError("timed out")],
    ["an oversized body", new UpstreamPayloadError("too big")],
  ])("reports %s as SERVICE_UNAVAILABLE", async (_label, error) => {
    mocks.fetchBoundedJson.mockRejectedValue(error);

    await expect(caller.getWatersheds({ bbox: BBOX })).rejects.toMatchObject({
      code: "SERVICE_UNAVAILABLE",
    });
    await expect(caller.getSoilSurvey({ bbox: BBOX })).rejects.toMatchObject({
      code: "SERVICE_UNAVAILABLE",
    });
  });

  it("does not relabel a permanent 400 as a temporary outage", async () => {
    // A malformed request is not something the client should retry, and SDA answers a
    // bad query with 400 -- so it must propagate as our own fault (tRPC wraps it as
    // INTERNAL_SERVER_ERROR) rather than be dressed up as "the provider is down".
    mocks.fetchBoundedJson.mockRejectedValue(new UpstreamHttpError(400));

    for (const call of [
      () => caller.getWatersheds({ bbox: BBOX }),
      () => caller.getSoilSurvey({ bbox: BBOX }),
    ]) {
      await expect(call()).rejects.toMatchObject({
        code: "INTERNAL_SERVER_ERROR",
        cause: expect.any(UpstreamHttpError),
      });
    }
  });
});

describe("environmental router proxied collections", () => {
  it("declares a watershed provider fault unavailable instead of empty", async () => {
    mocks.fetchBoundedJson.mockResolvedValue({ error: { code: 400 } });

    expect(await caller.getWatersheds({ bbox: BBOX })).toMatchObject({
      availability: "unavailable",
      reason: "watershed_upstream_returned_no_features",
      features: [],
      truncated: false,
    });
  });

  it("declares an unreadable SDA table unavailable instead of empty", async () => {
    mocks.fetchBoundedJson.mockResolvedValue({ Table: 42 });

    expect(await caller.getSoilSurvey({ bbox: BBOX })).toMatchObject({
      availability: "unavailable",
      reason: "soil_survey_upstream_returned_no_table",
      features: [],
    });
  });

  it("publishes a truncated watershed view as partial rather than complete", async () => {
    mocks.fetchBoundedJson.mockResolvedValue({
      type: "FeatureCollection",
      exceededTransferLimit: true,
      features: [{ type: "Feature", geometry: null, properties: {} }],
    });

    expect(await caller.getWatersheds({ bbox: BBOX })).toMatchObject({
      availability: "published",
      truncated: true,
    });
  });

  it("reports an empty viewport as published with nothing in it", async () => {
    mocks.fetchBoundedJson.mockResolvedValue({ type: "FeatureCollection", features: [] });

    expect(await caller.getWatersheds({ bbox: BBOX })).toMatchObject({
      availability: "published",
      reason: null,
      truncated: false,
      unreadableGeometries: 0,
      features: [],
    });
  });

  it("reports zero unreadable geometries for a watershed viewport", async () => {
    // hydrosheds rejects a whole payload rather than dropping features, so a non-zero
    // count here would be a claim nothing measured.
    mocks.fetchBoundedJson.mockResolvedValue({
      type: "FeatureCollection",
      features: [{ type: "Feature", geometry: null, properties: {} }],
    });

    expect(await caller.getWatersheds({ bbox: BBOX })).toMatchObject({
      unreadableGeometries: 0,
    });
  });
});

describe("environmental router viewport bounds", () => {
  /** A bbox anchored at Boise covering exactly `squareDegrees`. */
  function bboxOfArea(squareDegrees: number): string {
    const side = Math.sqrt(squareDegrees);
    return `-116.35,43.5,${-116.35 + side},${43.5 + side}`;
  }

  it("refuses a watershed viewport past the measured ceiling", async () => {
    mocks.fetchBoundedJson.mockResolvedValue({ type: "FeatureCollection", features: [] });

    await expect(
      caller.getWatersheds({ bbox: bboxOfArea(MAX_WATERSHED_BBOX_SQUARE_DEGREES * 4) })
    ).rejects.toMatchObject({ code: "BAD_REQUEST" });
    expect(mocks.fetchBoundedJson).not.toHaveBeenCalled();

    await expect(
      caller.getWatersheds({ bbox: bboxOfArea(MAX_WATERSHED_BBOX_SQUARE_DEGREES / 2) })
    ).resolves.toMatchObject({ availability: "published" });
  });

  it("refuses a soil viewport past the measured ceiling", async () => {
    mocks.fetchBoundedJson.mockResolvedValue({});

    await expect(
      caller.getSoilSurvey({ bbox: bboxOfArea(MAX_SOIL_BBOX_SQUARE_DEGREES * 4) })
    ).rejects.toMatchObject({ code: "BAD_REQUEST" });
    expect(mocks.fetchBoundedJson).not.toHaveBeenCalled();

    await expect(
      caller.getSoilSurvey({ bbox: bboxOfArea(MAX_SOIL_BBOX_SQUARE_DEGREES / 2) })
    ).resolves.toMatchObject({ availability: "published" });
  });

  it("still refuses a bbox outside WGS84 regardless of its area", async () => {
    await expect(caller.getWatersheds({ bbox: "-181,43.5,-180.9,43.6" })).rejects.toMatchObject(
      { code: "BAD_REQUEST" }
    );
  });
});
