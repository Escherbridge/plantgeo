import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Both services under test are upstream proxies, so the load-bearing behaviour is
 * which endpoint they ask for, how they normalize what comes back, and what they
 * refuse to cache or claim. Only the network and the shared Redis helpers are
 * stubbed; the tRPC procedures run the real services underneath.
 */
const mocks = vi.hoisted(() => ({
  fetchBoundedJson: vi.fn(),
  getCachedGeoJSON: vi.fn(async () => null as unknown),
  cacheGeoJSON: vi.fn(async () => undefined),
}));

vi.mock("@/lib/server/http/bounded-upstream", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/server/http/bounded-upstream")>();
  return { ...actual, fetchBoundedJson: mocks.fetchBoundedJson };
});

vi.mock("@/lib/server/redis", () => ({
  getCachedGeoJSON: mocks.getCachedGeoJSON,
  cacheGeoJSON: mocks.cacheGeoJSON,
}));

vi.mock("@/lib/server/db", () => ({ db: {} }));
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
import {
  getSoilSurvey,
  MAX_SOIL_BBOX_SQUARE_DEGREES,
  MAX_SOIL_POLYGONS,
  SoilSurveyResponseError,
} from "@/lib/server/services/usda-soil";
import type { Context } from "@/lib/server/trpc/init";
import { environmentalRouter } from "@/lib/server/trpc/routers/environmental";

// 0.01 square degrees over Boise -- inside both proxied procedures' viewport ceilings,
// so the same bbox exercises the services and the router.
const BBOX = "-116.35,43.5,-116.25,43.6";

/** A closed square ring, the smallest geometry the WKT reader will accept. */
const SQUARE_WKT =
  "POLYGON ((-116.35 43.5, -116.34 43.5, -116.34 43.51, -116.35 43.51, -116.35 43.5))";

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
});

/** The URL the service asked for, as a parsed URL. */
function requestedUrl(): URL {
  return new URL(String(mocks.fetchBoundedJson.mock.calls.at(-1)?.[0]));
}

/** The RequestInit the service passed alongside that URL. */
function requestInit(): RequestInit {
  return (mocks.fetchBoundedJson.mock.calls.at(-1)?.[1] ?? {}) as RequestInit;
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

/** An SDA `JSON+COLUMNNAME` result table over the columns the query selects. */
function soilTable(rows: (string | null)[][]) {
  return {
    Table: [
      ["mukey", "muname", "compname", "drainagecl", "hydricrating", "nirrcapcl", "geom"],
      ...rows,
    ],
  };
}

/** One map unit, defaulting every rating so a case can vary just the one it tests. */
function soilRow(overrides: Partial<Record<string, string | null>> = {}) {
  const row = {
    mukey: "2519086",
    muname: "Power silt loam, 0 to 2 percent slopes",
    compname: "Power",
    drainagecl: "Well drained",
    hydricrating: "No",
    nirrcapcl: "6",
    geom: SQUARE_WKT,
    ...overrides,
  };
  return [
    row.mukey,
    row.muname,
    row.compname,
    row.drainagecl,
    row.hydricrating,
    row.nirrcapcl,
    row.geom,
  ];
}

async function firstSoilProperties(
  overrides: Partial<Record<string, string | null>> = {}
): Promise<Record<string, unknown>> {
  mocks.fetchBoundedJson.mockResolvedValue(soilTable([soilRow(overrides)]));
  const collection = await getSoilSurvey(BBOX);
  return (collection.features[0]?.properties ?? {}) as Record<string, unknown>;
}

describe("SSURGO map unit acquisition", () => {
  it("posts to Soil Data Access tabular, not the SSURGO spatial WFS", async () => {
    mocks.fetchBoundedJson.mockResolvedValue({});

    await getSoilSurvey(BBOX);

    const url = requestedUrl();
    // Probed live 2026-08-04: `Spatial/SDM.wfs` does not exist (400, "Requested WFS
    // Service does not exist"), and the service that does -- SDMWGS84Geographic.wfs --
    // accepts only GML2/GML3/XMLMukeyList, never JSON. Its MapunitPoly type also carries
    // no muname/compname/drainagecl/hydricrating at all, so a URL fix alone would have
    // reported "Unknown"/"unknown"/hydric-No for every polygon in the country.
    expect(url.host).toBe("sdmdataaccess.nrcs.usda.gov");
    expect(url.pathname).toBe("/Tabular/post.rest");
    expect(url.href.toLowerCase()).not.toContain(".wfs");
    expect(requestInit().method).toBe("POST");
  });

  it("asks the clipped-mapunit macro for the viewport and joins the ratings tables", async () => {
    mocks.fetchBoundedJson.mockResolvedValue({});

    await getSoilSurvey(BBOX);

    const query = JSON.parse(String(requestInit().body)).query as string;
    // The spatial half: SDA's own preprocessor macro, which clips map-unit polygons to
    // the area of interest and is the only route to SSURGO geometry as JSON.
    expect(query).toContain("~GetClippedMapunits(@aoi,polygon,geo,@clipped)~");
    expect(query).toContain(
      "POLYGON((-116.35 43.5, -116.25 43.5, -116.25 43.6, -116.35 43.6, -116.35 43.5))"
    );
    // The tabular half: muname lives on `mapunit`, and the drainage/hydric ratings on
    // the dominant `component`. Neither is reachable from the spatial WFS.
    expect(query).toContain("inner join mapunit mu on mu.mukey = g.id");
    expect(query).toContain("left join component c on c.mukey = mu.mukey");
    expect(query).toContain("c.drainagecl");
    expect(query).toContain("c.hydricrating");
    // Bounded and ordered: one row over the ceiling makes truncation detectable, and a
    // stable order makes the dropped row the same on every call.
    expect(query).toContain(`select top ${MAX_SOIL_POLYGONS + 1}`);
    expect(query).toContain("order by g.id");
  });

  it("reads WKT into GeoJSON in longitude/latitude order", async () => {
    mocks.fetchBoundedJson.mockResolvedValue(soilTable([soilRow()]));

    const geometry = (await getSoilSurvey(BBOX)).features[0].geometry;

    expect(geometry).toEqual({
      type: "Polygon",
      coordinates: [
        [
          [-116.35, 43.5],
          [-116.34, 43.5],
          [-116.34, 43.51],
          [-116.35, 43.51],
          [-116.35, 43.5],
        ],
      ],
    });
  });

  it("reads a MULTIPOLYGON with an interior ring", async () => {
    const wkt =
      "MULTIPOLYGON (((0 0, 2 0, 2 2, 0 2, 0 0), (0.5 0.5, 1 0.5, 1 1, 0.5 1, 0.5 0.5)), " +
      "((5 5, 6 5, 6 6, 5 6, 5 5)))";
    mocks.fetchBoundedJson.mockResolvedValue(soilTable([soilRow({ geom: wkt })]));

    const geometry = (await getSoilSurvey(BBOX)).features[0].geometry as GeoJSON.MultiPolygon;

    expect(geometry.type).toBe("MultiPolygon");
    expect(geometry.coordinates).toHaveLength(2);
    expect(geometry.coordinates[0]).toHaveLength(2);
    expect(geometry.coordinates[1][0][0]).toEqual([5, 5]);
  });

  it("drops a map unit whose geometry will not parse rather than placing it", async () => {
    mocks.fetchBoundedJson.mockResolvedValue(
      soilTable([soilRow({ geom: "POLYGON EMPTY" }), soilRow({ mukey: "2496658" })])
    );

    const collection = await getSoilSurvey(BBOX);

    expect(collection.features).toHaveLength(1);
    expect((collection.features[0].properties as { mukey: string }).mukey).toBe("2496658");
    // Counted, not absorbed: a silent drop is indistinguishable downstream from ground
    // the survey never covered, and the panel captions the two differently.
    expect(collection.unreadableGeometries).toBe(1);
  });

  it("reports no dropped rows for a view it could read in full", async () => {
    mocks.fetchBoundedJson.mockResolvedValue(soilTable([soilRow()]));

    expect((await getSoilSurvey(BBOX)).unreadableGeometries).toBe(0);
  });

  it("reports a view whose every map unit was unreadable as empty AND dropped", async () => {
    // The dishonest case the count exists for: SDA served rows, none survived the
    // parser, and without the count this is byte-identical to open ocean.
    mocks.fetchBoundedJson.mockResolvedValue(
      soilTable([soilRow({ geom: "POLYGON EMPTY" }), soilRow({ geom: "not wkt" })])
    );

    const collection = await getSoilSurvey(BBOX);

    expect(collection.features).toEqual([]);
    expect(collection.unreadableGeometries).toBe(2);
  });

  it("refuses an unclosed exterior ring rather than emitting one for a renderer to repair", async () => {
    // RFC 7946 3.1.6 requires the last position to repeat the first. SDA's STAsText
    // always closes its rings, so this is latent -- but an unclosed ring that reached a
    // renderer would be auto-closed into a different polygon than the survey recorded,
    // which is exactly the "partial or repaired geometry" this parser refuses to emit.
    mocks.fetchBoundedJson.mockResolvedValue(
      soilTable([
        soilRow({ geom: "POLYGON ((0 0, 10 0, 10 10, 5 5))" }),
        soilRow({ mukey: "2496658" }),
      ])
    );

    const collection = await getSoilSurvey(BBOX);

    expect(collection.features).toHaveLength(1);
    expect((collection.features[0].properties as { mukey: string }).mukey).toBe("2496658");
  });

  it("refuses an unclosed interior ring inside an otherwise closed polygon", async () => {
    // The hole is what would be repaired here; dropping the map unit is honest, drawing
    // a hole the survey never cut is not.
    mocks.fetchBoundedJson.mockResolvedValue(
      soilTable([
        soilRow({
          geom: "POLYGON ((0 0, 2 0, 2 2, 0 2, 0 0), (0.5 0.5, 1 0.5, 1 1, 0.5 1))",
        }),
      ])
    );

    expect((await getSoilSurvey(BBOX)).features).toEqual([]);
  });

  it("carries the tabular ratings the spatial WFS cannot supply", async () => {
    expect(await firstSoilProperties()).toEqual({
      mukey: "2519086",
      muname: "Power silt loam, 0 to 2 percent slopes",
      soilSeries: "Power",
      drainageClass: "well-drained",
      hydric: false,
      landCapabilityClass: "6",
    });
  });

  it("reports a missing rating as absent, never as a value SSURGO did not record", async () => {
    expect(
      await firstSoilProperties({
        muname: null,
        compname: null,
        drainagecl: null,
        nirrcapcl: null,
      })
    ).toMatchObject({
      muname: null,
      soilSeries: null,
      drainageClass: null,
      landCapabilityClass: null,
    });
  });
});

describe("SSURGO hydric rating", () => {
  // `Boolean(rating)` is true for the literal string "No" and for "Unranked", and
  // false for an absent rating -- so it both inverts real ratings and manufactures a
  // "Hydric: No" verdict for units SSURGO never rated. Only "Yes"/"No" is a rating.
  it.each([
    ["Yes", true],
    ["No", false],
    ["Unranked", null],
    ["", null],
    [null, null],
  ])("maps %s to %s", async (rating, expected) => {
    expect((await firstSoilProperties({ hydricrating: rating })).hydric).toBe(expected);
  });
});

describe("SSURGO drainage class normalization", () => {
  // Every branch is a substring test, so a general phrase placed first swallows the
  // specific one: "well" used to match "moderately well drained" and "poorly" used to
  // match "very poorly drained", making both classes unreachable and reporting better
  // drainage than the survey recorded.
  it.each([
    ["Moderately well drained", "moderately-well-drained"],
    ["Very poorly drained", "very-poorly-drained"],
    ["Somewhat excessively drained", "somewhat-excessively-drained"],
    ["Somewhat poorly drained", "somewhat-poorly-drained"],
    ["Well drained", "well-drained"],
    ["Poorly drained", "poorly-drained"],
    ["Excessively drained", "excessively-drained"],
  ])("maps %s to %s", async (raw, expected) => {
    expect((await firstSoilProperties({ drainagecl: raw })).drainageClass).toBe(expected);
  });

  it("passes an unrecognized label through rather than guessing a class", async () => {
    expect((await firstSoilProperties({ drainagecl: "Subaqueous" })).drainageClass).toBe(
      "Subaqueous"
    );
  });

  it("reports no class when the survey left the field empty", async () => {
    expect((await firstSoilProperties({ drainagecl: "" })).drainageClass).toBeNull();
  });
});

describe("SSURGO response handling", () => {
  it("treats SDA's empty body as no coverage here, not a fault", async () => {
    // Probed live over open ocean: HTTP 200 with a bare `{}` and no Table key.
    mocks.fetchBoundedJson.mockResolvedValue({});

    const collection = await getSoilSurvey(BBOX);

    expect(collection.features).toEqual([]);
    expect(collection.truncated).toBe(false);
  });

  it("refuses a body carrying no readable result table and never caches it", async () => {
    mocks.fetchBoundedJson.mockResolvedValue({ Table: "not a table" });

    await expect(getSoilSurvey(BBOX)).rejects.toBeInstanceOf(SoilSurveyResponseError);
    expect(mocks.cacheGeoJSON).not.toHaveBeenCalled();
  });

  it("marks the view truncated once the row ceiling is exceeded", async () => {
    const rows = Array.from({ length: MAX_SOIL_POLYGONS + 1 }, (_unused, index) =>
      soilRow({ mukey: String(index) })
    );
    mocks.fetchBoundedJson.mockResolvedValue(soilTable(rows));

    const collection = await getSoilSurvey(BBOX);

    expect(collection.features).toHaveLength(MAX_SOIL_POLYGONS);
    expect(collection.truncated).toBe(true);
  });

  it("re-fetches a cached collection that cannot state its dropped-row count", async () => {
    // What a deploy before the count existed left in Redis. Serving it would publish
    // "nothing was dropped" over an entry that never measured it.
    mocks.getCachedGeoJSON.mockResolvedValue({
      type: "FeatureCollection",
      features: [],
      truncated: false,
    });
    mocks.fetchBoundedJson.mockResolvedValue(soilTable([soilRow()]));

    const collection = await getSoilSurvey(BBOX);

    expect(mocks.fetchBoundedJson).toHaveBeenCalledTimes(1);
    expect(collection.unreadableGeometries).toBe(0);
  });

  it("refuses a bbox it would otherwise interpolate into SQL", async () => {
    // A RangeError, not a SoilSurveyResponseError: the request was never sent, so
    // blaming SDA for it would be a fault the provider never reported.
    await expect(getSoilSurvey("0,0,0'; drop table mapunit --,1")).rejects.toBeInstanceOf(
      RangeError
    );
    expect(mocks.fetchBoundedJson).not.toHaveBeenCalled();
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

  it("carries the unreadable-geometry count to the client, not just the features", async () => {
    // Otherwise the client sees `features: []`, `truncated: false`,
    // `availability: "published"` and captions a parser gap as USDA finding no soil.
    mocks.fetchBoundedJson.mockResolvedValue(
      soilTable([soilRow({ geom: "POLYGON EMPTY" })])
    );

    expect(await caller.getSoilSurvey({ bbox: BBOX })).toMatchObject({
      availability: "published",
      reason: null,
      truncated: false,
      unreadableGeometries: 1,
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
