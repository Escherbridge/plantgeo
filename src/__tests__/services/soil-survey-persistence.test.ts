import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * SSURGO map units are persisted, not proxied, so what is load-bearing here splits in two:
 * the acquisition contract (which SDA table, keyed on what, dated by what) and the honesty
 * contract (a row we could not store, a cell nobody has fetched, and an average are each
 * distinguishable from ground the survey found nothing on).
 *
 * Only the network and the database are stubbed. `db.execute` is dispatched on the SQL text
 * so one test can answer the coverage lookup and the feature read differently, which is what
 * makes the coverage-gap cases expressible at all.
 */
// Both stubs declare their parameters, so `mock.calls` stays an indexable tuple rather
// than the empty one a zero-arg `vi.fn()` infers.
const mocks = vi.hoisted(() => ({
  fetchBoundedJson: vi.fn(async (_url: unknown, _init?: unknown, _bounds?: unknown) =>
    ({}) as unknown
  ),
  dbExecute: vi.fn(async (_statement: unknown) => [] as unknown[]),
}));

vi.mock("@/lib/server/http/bounded-upstream", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/server/http/bounded-upstream")>();
  return { ...actual, fetchBoundedJson: mocks.fetchBoundedJson };
});

vi.mock("@/lib/server/db", () => ({
  db: {
    execute: mocks.dbExecute,
    transaction: async (run: (tx: { execute: typeof mocks.dbExecute }) => unknown) =>
      run({ execute: mocks.dbExecute }),
  },
}));

import {
  backfillSoilSurvey,
  getSoilSurvey,
  ingestSoilSurveyCell,
  MAX_SOIL_AGGREGATION_CELLS_PER_SIDE,
  MAX_SOIL_BBOX_SQUARE_DEGREES,
  MAX_SOIL_INGEST_POLYGONS_PER_CELL,
  MAX_SOIL_POLYGONS,
  parseSoilSurveyRows,
  resolveSoilSurveyGranularity,
  SOIL_SURVEY_CELL_DEGREES,
  SOIL_SURVEY_DETAIL_MIN_ZOOM,
  SOIL_SURVEY_LAYER_NAME,
  SOIL_SURVEY_PRODUCER,
  SOIL_SURVEY_REGIONAL_MIN_ZOOM,
  soilSurveyAreaCeiling,
  soilSurveyCellKey,
  soilSurveyCellsCovering,
  soilSurveyCellsForBbox,
  SoilSurveyResponseError,
} from "@/lib/server/services/usda-soil";

/** 0.01 square degrees over Boise — one grid cell, inside the detail-tier ceiling. */
const BBOX = "-116.35,43.5,-116.25,43.6";

/** A closed square ring, the smallest geometry the WKT reader will accept. */
const SQUARE_WKT =
  "POLYGON ((-116.35 43.5, -116.34 43.5, -116.34 43.51, -116.35 43.51, -116.35 43.5))";

/** `sacatalog.saverest` exactly as SDA serves it: US-locale text with no timezone. */
const SAVEREST = "8/27/2025 8:27:08 PM";

/** An SDA `JSON+COLUMNNAME` result table over the columns the query selects. */
function soilTable(rows: (string | null)[][]) {
  return {
    Table: [
      [
        "mupolygonkey",
        "mukey",
        "muname",
        "areasymbol",
        "saverest",
        "compname",
        "drainagecl",
        "hydricrating",
        "nirrcapcl",
        "geom",
      ],
      ...rows,
    ],
  };
}

/** One delineation, defaulting every field so a case can vary just the one it tests. */
function soilRow(overrides: Partial<Record<string, string | null>> = {}) {
  const row = {
    mupolygonkey: "397862825",
    mukey: "2519086",
    muname: "Power silt loam, 0 to 2 percent slopes",
    areasymbol: "ID001",
    saverest: SAVEREST,
    compname: "Power",
    drainagecl: "Well drained",
    hydricrating: "No",
    nirrcapcl: "6",
    geom: SQUARE_WKT,
    ...overrides,
  };
  return [
    row.mupolygonkey,
    row.mukey,
    row.muname,
    row.areasymbol,
    row.saverest,
    row.compname,
    row.drainagecl,
    row.hydricrating,
    row.nirrcapcl,
    row.geom,
  ];
}

/** The properties one served row normalizes into. */
function firstParsedProperties(
  overrides: Partial<Record<string, string | null>> = {}
): Record<string, unknown> {
  const parsed = parseSoilSurveyRows(soilTable([soilRow(overrides)]));
  return (parsed.units[0]?.properties ?? {}) as unknown as Record<string, unknown>;
}

/** The SQL text of every `db.execute` call, in order. */
function executedSql(): string[] {
  return mocks.dbExecute.mock.calls.map((call) =>
    // drizzle builds an SQL object; its chunks carry the literal text.
    JSON.stringify(call[0])
  );
}

/** True when some statement's text contains every fragment. */
function someStatementContains(...fragments: string[]): boolean {
  return executedSql().some((statement) =>
    fragments.every((fragment) => statement.includes(fragment))
  );
}

/**
 * Routes `db.execute` by SQL text so one test can answer the coverage lookup, the feature
 * read and the stored-count probe differently. Anything unmatched answers with no rows.
 */
function stubDatabase(handlers: {
  coverage?: unknown[];
  detail?: unknown[];
  aggregate?: unknown[];
  storedCount?: string;
}) {
  mocks.dbExecute.mockImplementation(async (statement: unknown) => {
    const text = JSON.stringify(statement);
    if (text.includes("geo.soil_survey_coverage") && text.includes("SELECT cell_key")) {
      return handlers.coverage ?? [];
    }
    if (text.includes("AS stored")) {
      return handlers.storedCount === undefined
        ? []
        : [{ stored: handlers.storedCount }];
    }
    if (text.includes("WITH candidate AS")) return handlers.aggregate ?? [];
    if (text.includes("ST_AsGeoJSON(f.geom)")) return handlers.detail ?? [];
    return [];
  });
}

/** A `geo.soil_survey_coverage` row as the ledger lookup returns it. */
function coverageRow(
  overrides: Partial<{
    cell_key: string;
    polygon_count: string;
    unreadable_count: string;
    truncated: boolean;
  }> = {}
) {
  return {
    cell_key: soilSurveyCellKey(-116.3, 43.55),
    polygon_count: "412",
    unreadable_count: "0",
    truncated: false,
    ...overrides,
  };
}

/** A persisted feature row as the detail read returns it. */
function detailRow(
  overrides: Partial<{ polygon_key: string; properties: Record<string, unknown> }> = {}
) {
  return {
    polygon_key: "397862825",
    geometry: JSON.stringify({
      type: "Polygon",
      coordinates: [
        [
          [0, 0],
          [1, 0],
          [1, 1],
          [0, 1],
          [0, 0],
        ],
      ],
    }),
    properties: {
      id: "397862825",
      mukey: "2519086",
      muname: "Power silt loam, 0 to 2 percent slopes",
      soilSeries: "Power",
      drainageClass: "well-drained",
      hydric: false,
      landCapabilityClass: "6",
      areaSymbol: "ID001",
      surveyAreaVintage: "2025-08-27",
    },
    ...overrides,
  };
}

beforeEach(() => {
  mocks.fetchBoundedJson.mockReset();
  mocks.dbExecute.mockReset();
  mocks.dbExecute.mockResolvedValue([]);
});

describe("SSURGO acquisition targets the delineation, not the clipped map unit", () => {
  it("posts to Soil Data Access tabular, not the SSURGO spatial WFS", async () => {
    mocks.fetchBoundedJson.mockResolvedValue({});

    await getSoilSurvey(BBOX);

    // Probed live 2026-08-04: `Spatial/SDM.wfs` does not exist (400, "Requested WFS
    // Service does not exist"), and the service that does -- SDMWGS84Geographic.wfs --
    // accepts only GML2/GML3/XMLMukeyList, never JSON. Its MapunitPoly type also carries
    // no muname/compname/drainagecl/hydricrating at all.
    const url = new URL(String(mocks.fetchBoundedJson.mock.calls.at(-1)?.[0]));
    expect(url.host).toBe("sdmdataaccess.nrcs.usda.gov");
    expect(url.pathname).toBe("/Tabular/post.rest");
    expect(url.href.toLowerCase()).not.toContain(".wfs");
  });

  it("reads mupolygon by intersection rather than the clipping macro, and never keys on mukey", async () => {
    mocks.fetchBoundedJson.mockResolvedValue({});

    await getSoilSurvey(BBOX);

    const query = JSON.parse(
      String((mocks.fetchBoundedJson.mock.calls.at(-1)?.[1] as RequestInit).body)
    ).query as string;
    // `~GetClippedMapunits~` returns geometry clipped to whichever viewport asked, keyed on
    // mukey -- measured 683 rows over only 98 distinct mukeys for one cell. Persisting
    // either property is wrong: a stored clip depends on the request that fetched it, and a
    // mukey key would collapse 683 delineations into 98.
    expect(query).not.toContain("GetClippedMapunits");
    expect(query).toContain("from mupolygon p");
    expect(query).toContain("where p.mupolygongeo.STIntersects(@aoi) = 1");
    expect(query).toContain("p.mupolygonkey");
    // The vintage join: mapunit -> legend -> sacatalog, which is the only publication time
    // SSURGO issues for a map unit.
    expect(query).toContain("inner join legend lg on lg.lkey = mu.lkey");
    expect(query).toContain("left join sacatalog sac on sac.areasymbol = lg.areasymbol");
    // The ratings still come from the dominant component.
    expect(query).toContain("left join component c on c.mukey = mu.mukey");
    expect(query).toContain("c.drainagecl");
    expect(query).toContain("c.hydricrating");
    // Bounded and ordered: one row over the ceiling makes truncation detectable, and a
    // stable order makes the dropped row the same on every call.
    expect(query).toContain(`select top ${MAX_SOIL_INGEST_POLYGONS_PER_CELL + 1}`);
    expect(query).toContain("order by p.mupolygonkey");
  });

  it("asks for the whole grid cell, never the caller's exact bbox", async () => {
    mocks.fetchBoundedJson.mockResolvedValue({});

    await getSoilSurvey(BBOX);

    const query = JSON.parse(
      String((mocks.fetchBoundedJson.mock.calls.at(-1)?.[1] as RequestInit).body)
    ).query as string;
    // Cell-aligned, so two viewports over the same ground reuse one stored fetch instead of
    // each minting its own near-duplicate coverage.
    const col = Math.floor(-116.35 / SOIL_SURVEY_CELL_DEGREES);
    const row = Math.floor(43.5 / SOIL_SURVEY_CELL_DEGREES);
    expect(query).toContain(
      `POLYGON((${col * SOIL_SURVEY_CELL_DEGREES} ${row * SOIL_SURVEY_CELL_DEGREES}`
    );
  });

  it("refuses a bbox it would otherwise interpolate into SQL", async () => {
    // A RangeError, not a SoilSurveyResponseError: the request was never sent, so blaming
    // SDA for it would be a fault the provider never reported.
    await expect(getSoilSurvey("0,0,0'; drop table mapunit --,1")).rejects.toBeInstanceOf(
      RangeError
    );
    expect(mocks.fetchBoundedJson).not.toHaveBeenCalled();
  });
});

describe("SSURGO row normalization", () => {
  it("reads WKT into GeoJSON in longitude/latitude order", () => {
    expect(parseSoilSurveyRows(soilTable([soilRow()])).units[0].geometry).toEqual({
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

  it("reads a MULTIPOLYGON with an interior ring", () => {
    const wkt =
      "MULTIPOLYGON (((0 0, 2 0, 2 2, 0 2, 0 0), (0.5 0.5, 1 0.5, 1 1, 0.5 1, 0.5 0.5)), " +
      "((5 5, 6 5, 6 6, 5 6, 5 5)))";

    const geometry = parseSoilSurveyRows(soilTable([soilRow({ geom: wkt })])).units[0]
      .geometry as GeoJSON.MultiPolygon;

    expect(geometry.type).toBe("MultiPolygon");
    expect(geometry.coordinates).toHaveLength(2);
    expect(geometry.coordinates[0]).toHaveLength(2);
    expect(geometry.coordinates[1][0][0]).toEqual([5, 5]);
  });

  it("carries the tabular ratings the spatial WFS cannot supply, plus the survey vintage", () => {
    expect(firstParsedProperties()).toEqual({
      mukey: "2519086",
      muname: "Power silt loam, 0 to 2 percent slopes",
      soilSeries: "Power",
      drainageClass: "well-drained",
      hydric: false,
      landCapabilityClass: "6",
      areaSymbol: "ID001",
      surveyAreaVintage: "2025-08-27",
    });
  });

  it("reports a missing rating as absent, never as a value SSURGO did not record", () => {
    expect(
      firstParsedProperties({
        muname: null,
        compname: null,
        drainagecl: null,
        hydricrating: null,
        nirrcapcl: null,
        areasymbol: null,
      })
    ).toMatchObject({
      muname: null,
      soilSeries: null,
      drainageClass: null,
      hydric: null,
      landCapabilityClass: null,
      areaSymbol: null,
    });
  });

  it.each([
    ["Yes", true],
    ["yes", true],
    ["No", false],
    ["Unranked", null],
    ["", null],
  ])("rates hydric %s as %s", (rating, expected) => {
    expect(firstParsedProperties({ hydricrating: rating }).hydric).toBe(expected);
  });

  it.each([
    ["Very poorly drained", "very-poorly-drained"],
    ["Somewhat poorly drained", "somewhat-poorly-drained"],
    ["Poorly drained", "poorly-drained"],
    ["Moderately well drained", "moderately-well-drained"],
    ["Somewhat excessively drained", "somewhat-excessively-drained"],
    ["Excessively drained", "excessively-drained"],
    ["Well drained", "well-drained"],
  ])("normalizes %s to %s", (raw, expected) => {
    expect(firstParsedProperties({ drainagecl: raw }).drainageClass).toBe(expected);
  });

  it("passes an unrecognized drainage label through rather than guessing a class", () => {
    expect(firstParsedProperties({ drainagecl: "Subaqueous" }).drainageClass).toBe(
      "Subaqueous"
    );
  });
});

describe("SSURGO rows that cannot be stored are counted, never dropped silently", () => {
  it("drops a delineation whose geometry will not parse rather than placing it", () => {
    const parsed = parseSoilSurveyRows(
      soilTable([
        soilRow({ geom: "POLYGON EMPTY" }),
        soilRow({ mupolygonkey: "397862846" }),
      ])
    );

    expect(parsed.units).toHaveLength(1);
    expect(parsed.units[0].polygonKey).toBe("397862846");
    expect(parsed.unreadable).toBe(1);
  });

  it("refuses an unclosed ring rather than emitting one for a renderer to repair", () => {
    // RFC 7946 3.1.6: an unclosed ring handed to a renderer is auto-closed into a polygon
    // the survey never recorded.
    const parsed = parseSoilSurveyRows(
      soilTable([soilRow({ geom: "POLYGON ((0 0, 10 0, 10 10, 5 5))" })])
    );

    expect(parsed.units).toEqual([]);
    expect(parsed.unreadable).toBe(1);
  });

  it("drops a row with no survey-area vintage, because there is nothing honest to date it by", () => {
    // `geo.geometry.version_valid_from` is NOT NULL, and SSURGO publishes no other
    // release time for a map unit. Storing it under `now()` would fabricate a vintage.
    const parsed = parseSoilSurveyRows(soilTable([soilRow({ saverest: null })]));

    expect(parsed.units).toEqual([]);
    expect(parsed.unreadable).toBe(1);
  });

  it("rejects a vintage that is not a real calendar date", () => {
    expect(parseSoilSurveyRows(soilTable([soilRow({ saverest: "2/30/2025" })])).unreadable).toBe(
      1
    );
    expect(parseSoilSurveyRows(soilTable([soilRow({ saverest: "13/1/2025" })])).unreadable).toBe(
      1
    );
  });

  it("keeps the vintage date and discards the clock SDA states no timezone for", () => {
    const [unit] = parseSoilSurveyRows(
      soilTable([soilRow({ saverest: "3/19/2026 7:13:03 PM" })])
    ).units;

    expect(unit.properties.surveyAreaVintage).toBe("2026-03-19");
    expect(unit.vintageInstant).toBe("2026-03-19T00:00:00.000Z");
  });

  it("reports no dropped rows for a cell it could read in full", () => {
    expect(parseSoilSurveyRows(soilTable([soilRow()])).unreadable).toBe(0);
  });

  it("marks the cell truncated once the ingest ceiling is exceeded", () => {
    const rows = Array.from(
      { length: MAX_SOIL_INGEST_POLYGONS_PER_CELL + 1 },
      (_unused, index) => soilRow({ mupolygonkey: String(index) })
    );

    const parsed = parseSoilSurveyRows(soilTable(rows));

    expect(parsed.units).toHaveLength(MAX_SOIL_INGEST_POLYGONS_PER_CELL);
    expect(parsed.truncated).toBe(true);
  });

  it("keeps one copy of a delineation SDA repeated", () => {
    // A repeated natural key would make the geometry upsert fail with "cannot affect row a
    // second time"; the component join is already narrowed to one row, so a repeat is an
    // upstream anomaly rather than something to merge.
    const parsed = parseSoilSurveyRows(soilTable([soilRow(), soilRow()]));

    expect(parsed.units).toHaveLength(1);
  });

  it("treats SDA's empty body as no coverage here, not a fault", () => {
    // Probed live over open ocean: HTTP 200 with a bare `{}` and no Table key.
    expect(parseSoilSurveyRows({})).toEqual({ units: [], truncated: false, unreadable: 0 });
  });

  it("refuses a body carrying no readable result table", () => {
    expect(() => parseSoilSurveyRows({ Table: "not a table" })).toThrow(
      SoilSurveyResponseError
    );
    expect(() => parseSoilSurveyRows(null)).toThrow(SoilSurveyResponseError);
  });
});

describe("the persistence grid", () => {
  it("resolves a coordinate to one cell with no floating-point drift", () => {
    // 1/8 is exactly representable, so the division is exact and the same ground always
    // lands in the same cell -- what stops the ledger developing near-duplicate rows.
    expect(soilSurveyCellKey(-116.35, 43.5)).toBe("-931:348");
    expect(soilSurveyCellKey(-116.125, 43.625)).toBe("-929:349");
    // A coordinate exactly on a boundary belongs to the cell it starts.
    expect(soilSurveyCellKey(-116.0, 43.0)).toBe("-928:344");
  });

  it("does not claim the zero-width cell beyond an edge-aligned bbox", () => {
    const { cells } = soilSurveyCellsForBbox([-116.25, 43.5, -116.125, 43.625]);

    expect(cells).toHaveLength(1);
    expect(cells[0].key).toBe("-930:348");
  });

  it("caps the cells one request may warm, and centres what it samples", () => {
    const span = SOIL_SURVEY_CELL_DEGREES * (MAX_SOIL_AGGREGATION_CELLS_PER_SIDE + 2);
    const { cells, budgetExceeded } = soilSurveyCellsForBbox([
      -116.5,
      43.5,
      -116.5 + span,
      43.5 + span,
    ]);

    expect(budgetExceeded).toBe(true);
    expect(cells).toHaveLength(
      MAX_SOIL_AGGREGATION_CELLS_PER_SIDE * MAX_SOIL_AGGREGATION_CELLS_PER_SIDE
    );
    // Centred rather than taken from a corner: the sampled ground is the part most likely
    // under the pointer.
    expect(cells[0].key).toBe("-931:349");
  });

  it("enumerates the whole envelope for the backfill, uncapped", () => {
    // The number the backfill projection is built on: 112 x 56 cells over the PNW.
    expect(soilSurveyCellsCovering([-125, 42, -111, 49])).toHaveLength(112 * 56);
  });
});

describe("SSURGO render granularity", () => {
  it("resolves detail with no zoom, and at or above the detail threshold", () => {
    expect(resolveSoilSurveyGranularity(undefined)).toBe("detail");
    expect(resolveSoilSurveyGranularity(SOIL_SURVEY_DETAIL_MIN_ZOOM)).toBe("detail");
    expect(resolveSoilSurveyGranularity(SOIL_SURVEY_DETAIL_MIN_ZOOM + 2)).toBe("detail");
  });

  it("resolves the finer averaged band between the two thresholds", () => {
    expect(resolveSoilSurveyGranularity(SOIL_SURVEY_REGIONAL_MIN_ZOOM)).toBe(
      "regional-average"
    );
    expect(resolveSoilSurveyGranularity(SOIL_SURVEY_DETAIL_MIN_ZOOM - 1)).toBe(
      "regional-average"
    );
  });

  it("resolves the coarser averaged band below the regional threshold", () => {
    expect(resolveSoilSurveyGranularity(SOIL_SURVEY_REGIONAL_MIN_ZOOM - 1)).toBe(
      "coarse-average"
    );
    expect(resolveSoilSurveyGranularity(0)).toBe("coarse-average");
  });

  it("caps the schema-level viewport area only for the detail tier", () => {
    expect(soilSurveyAreaCeiling(undefined)).toBe(MAX_SOIL_BBOX_SQUARE_DEGREES);
    expect(soilSurveyAreaCeiling(SOIL_SURVEY_DETAIL_MIN_ZOOM)).toBe(
      MAX_SOIL_BBOX_SQUARE_DEGREES
    );
    expect(soilSurveyAreaCeiling(SOIL_SURVEY_REGIONAL_MIN_ZOOM)).toBeNull();
    expect(soilSurveyAreaCeiling(0)).toBeNull();
  });
});

describe("the warehouse write", () => {
  it("keys identity on the delineation, namespaced by producer", async () => {
    mocks.fetchBoundedJson.mockResolvedValue(soilTable([soilRow()]));
    stubDatabase({ storedCount: "1" });

    await ingestSoilSurveyCell({
      key: "-931:348",
      west: -116.375,
      south: 43.5,
      east: -116.25,
      north: 43.625,
    });

    // `ck_geometry_natural_key_namespaced` requires natural_key LIKE producer || ':%'.
    const payloads = mocks.dbExecute.mock.calls.map((call) => JSON.stringify(call[0]));
    expect(
      payloads.some((statement) => statement.includes(`${SOIL_SURVEY_PRODUCER}:397862825`))
    ).toBe(true);
    expect(someStatementContains("INSERT INTO geo.geometry", "'polygon'")).toBe(true);
  });

  it("dates the stored version by the survey area's vintage, never by write time", async () => {
    mocks.fetchBoundedJson.mockResolvedValue(soilTable([soilRow()]));
    stubDatabase({ storedCount: "1" });

    await ingestSoilSurveyCell({
      key: "-931:348",
      west: -116.375,
      south: 43.5,
      east: -116.25,
      north: 43.625,
    });

    expect(
      someStatementContains("version_valid_from", "elem->>'vintage'")
    ).toBe(true);
    // now() may only ever be a confirmation stamp, never a version's start.
    expect(someStatementContains("version_valid_from = now()")).toBe(false);
    expect(someStatementContains("last_confirmed_at = now()")).toBe(true);
  });

  it("only ever closes a version for a strictly newer vintage", async () => {
    mocks.fetchBoundedJson.mockResolvedValue(soilTable([soilRow()]));
    stubDatabase({ storedCount: "1" });

    await ingestSoilSurveyCell({
      key: "-931:348",
      west: -116.375,
      south: 43.5,
      east: -116.25,
      north: 43.625,
    });

    // ck_geometry_version_order requires version_valid_to > version_valid_from, so a
    // regressed upstream vintage must fail loudly rather than be absorbed.
    expect(
      someStatementContains(
        "UPDATE geo.geometry",
        "g.version_valid_from < incoming.vintage"
      )
    ).toBe(true);
  });

  it("writes features published, under the persisted soil layer", async () => {
    mocks.fetchBoundedJson.mockResolvedValue(soilTable([soilRow()]));
    stubDatabase({ storedCount: "1" });

    await ingestSoilSurveyCell({
      key: "-931:348",
      west: -116.375,
      south: 43.5,
      east: -116.25,
      north: 43.625,
    });

    expect(
      someStatementContains("INSERT INTO geo.features", "'published'")
    ).toBe(true);
    expect(
      mocks.dbExecute.mock.calls.some((call) =>
        JSON.stringify(call[0]).includes(SOIL_SURVEY_LAYER_NAME)
      )
    ).toBe(true);
  });

  it("records the ledger count from the database, not from the parse", async () => {
    // Counted back out of geo.features after the writes, so a row lost inside PostGIS --
    // where TypeScript cannot see it -- still surfaces as unreadable rather than quietly
    // shrinking the map.
    mocks.fetchBoundedJson.mockResolvedValue(
      soilTable([soilRow(), soilRow({ mupolygonkey: "2" }), soilRow({ mupolygonkey: "3" })])
    );
    stubDatabase({ storedCount: "2" });

    const row = await ingestSoilSurveyCell({
      key: "-931:348",
      west: -116.375,
      south: 43.5,
      east: -116.25,
      north: 43.625,
    });

    expect(row?.polygonCount).toBe(2);
    expect(row?.unreadableCount).toBe(1);
  });

  it("takes the advisory lock before writing, so two warmers cannot double-insert", async () => {
    mocks.fetchBoundedJson.mockResolvedValue(soilTable([soilRow()]));
    stubDatabase({ storedCount: "1" });

    await ingestSoilSurveyCell({
      key: "-931:348",
      west: -116.375,
      south: 43.5,
      east: -116.25,
      north: 43.625,
    });

    expect(executedSql()[0]).toContain("pg_advisory_xact_lock");
  });

  it("leaves a cell unrecorded when SDA fails, so a fault never becomes a coverage claim", async () => {
    mocks.fetchBoundedJson.mockRejectedValue(new Error("connection reset"));

    await expect(
      ingestSoilSurveyCell({
        key: "-931:348",
        west: -116.375,
        south: 43.5,
        east: -116.25,
        north: 43.625,
      })
    ).rejects.toThrow("connection reset");
    expect(someStatementContains("INSERT INTO geo.soil_survey_coverage")).toBe(false);
  });
});

describe("the coverage gap persistence created", () => {
  it("reads locally and asks SDA nothing when the cell is already covered", async () => {
    stubDatabase({ coverage: [coverageRow()], detail: [detailRow()] });

    const collection = await getSoilSurvey(BBOX, SOIL_SURVEY_DETAIL_MIN_ZOOM);

    expect(mocks.fetchBoundedJson).not.toHaveBeenCalled();
    expect(collection.features).toHaveLength(1);
    expect(collection.coverage).toEqual({ cells: 1, covered: 1, ingested: 0 });
    expect(collection.truncated).toBe(false);
  });

  it("reports a viewport nobody has fetched as uncovered rather than as empty ground", async () => {
    // The whole reason the ledger exists: without `coverage`, this response is
    // byte-identical to "USDA surveyed nothing here" — empty features, nothing dropped.
    stubDatabase({ coverage: [], detail: [], storedCount: "0" });
    mocks.fetchBoundedJson.mockResolvedValue({});

    const collection = await getSoilSurvey(BBOX, SOIL_SURVEY_DETAIL_MIN_ZOOM);

    expect(collection.features).toEqual([]);
    expect(collection.unreadableGeometries).toBe(0);
    expect(collection.coverage).toEqual({ cells: 1, covered: 0, ingested: 1 });
    expect(collection.truncated).toBe(true);
  });

  it("marks the view truncated while any cell in it is uncovered", async () => {
    const span = SOIL_SURVEY_CELL_DEGREES * 2;
    mocks.fetchBoundedJson.mockResolvedValue({});
    stubDatabase({ coverage: [coverageRow()], detail: [detailRow()], storedCount: "0" });

    const collection = await getSoilSurvey(
      `-116.375,43.5,${-116.375 + span},${43.5 + span}`,
      SOIL_SURVEY_DETAIL_MIN_ZOOM
    );

    expect(collection.coverage.cells).toBe(4);
    expect(collection.coverage.covered).toBe(1);
    expect(collection.truncated).toBe(true);
  });

  it("sums the ledger's unreadable counts rather than recomputing them", async () => {
    stubDatabase({
      coverage: [coverageRow({ unreadable_count: "3" })],
      detail: [detailRow()],
    });

    const collection = await getSoilSurvey(BBOX, SOIL_SURVEY_DETAIL_MIN_ZOOM);

    expect(collection.unreadableGeometries).toBe(3);
  });

  it("carries a cell truncated at ingest into the view's own truncation", async () => {
    stubDatabase({ coverage: [coverageRow({ truncated: true })], detail: [detailRow()] });

    expect((await getSoilSurvey(BBOX, SOIL_SURVEY_DETAIL_MIN_ZOOM)).truncated).toBe(true);
  });

  it("reports a covered cell with nothing in it as genuinely unsurveyed ground", async () => {
    // The one honest empty: the ledger says we asked, and USDA had nothing.
    stubDatabase({ coverage: [coverageRow({ polygon_count: "0" })], detail: [] });

    const collection = await getSoilSurvey(BBOX, SOIL_SURVEY_DETAIL_MIN_ZOOM);

    expect(collection.features).toEqual([]);
    expect(collection.truncated).toBe(false);
    expect(collection.unreadableGeometries).toBe(0);
    expect(collection.coverage).toEqual({ cells: 1, covered: 1, ingested: 0 });
  });
});

describe("the warehouse read", () => {
  it("serves real per-map-unit delineations at the detail tier", async () => {
    stubDatabase({ coverage: [coverageRow()], detail: [detailRow()] });

    const collection = await getSoilSurvey(BBOX, SOIL_SURVEY_DETAIL_MIN_ZOOM);

    expect(collection.granularity).toBe("detail");
    const properties = collection.features[0].properties as Record<string, unknown>;
    expect(properties.mukey).toBe("2519086");
    expect(properties.surveyAreaVintage).toBe("2025-08-27");
    expect(properties.aggregated).toBeUndefined();
  });

  it("bounds the detail read and reports the ceiling as truncation", async () => {
    stubDatabase({
      coverage: [coverageRow()],
      detail: Array.from({ length: MAX_SOIL_POLYGONS + 1 }, (_unused, index) =>
        detailRow({ polygon_key: String(index) })
      ),
    });

    const collection = await getSoilSurvey(BBOX, SOIL_SURVEY_DETAIL_MIN_ZOOM);

    expect(collection.features).toHaveLength(MAX_SOIL_POLYGONS);
    expect(collection.truncated).toBe(true);
  });

  it("averages by drainage class in one local query, never by re-asking SDA", async () => {
    stubDatabase({
      coverage: [coverageRow()],
      aggregate: [
        {
          drainage_class: "well-drained",
          geometry: JSON.stringify({
            type: "MultiPolygon",
            coordinates: [
              [
                [
                  [0, 0],
                  [1, 0],
                  [1, 1],
                  [0, 1],
                  [0, 0],
                ],
              ],
            ],
          }),
          map_unit_count: "6",
          hydric_count: "2",
          rated_count: "4",
          input_rows: "6",
        },
      ],
    });

    const collection = await getSoilSurvey(BBOX, SOIL_SURVEY_REGIONAL_MIN_ZOOM);

    // The 30-40 s that used to be nine SDA round trips is now one PostGIS query.
    expect(mocks.fetchBoundedJson).not.toHaveBeenCalled();
    expect(collection.granularity).toBe("regional-average");
    const properties = collection.features[0].properties as Record<string, unknown>;
    expect(properties).toMatchObject({
      aggregated: true,
      drainageClass: "well-drained",
      mapUnitCount: 6,
      hydricFraction: 0.5,
    });
    // Structurally unable to pass as a surveyed unit.
    expect(properties.mukey).toBeUndefined();
    expect(properties.muname).toBeUndefined();
  });

  it("reports hydricFraction null rather than 0 when no merged unit carried a rating", async () => {
    stubDatabase({
      coverage: [coverageRow()],
      aggregate: [
        {
          drainage_class: "well-drained",
          geometry: JSON.stringify({
            type: "MultiPolygon",
            coordinates: [
              [
                [
                  [0, 0],
                  [1, 0],
                  [1, 1],
                  [0, 1],
                  [0, 0],
                ],
              ],
            ],
          }),
          map_unit_count: "6",
          hydric_count: "0",
          rated_count: "0",
          input_rows: "6",
        },
      ],
    });

    const collection = await getSoilSurvey(BBOX, SOIL_SURVEY_REGIONAL_MIN_ZOOM);

    expect(
      (collection.features[0].properties as Record<string, unknown>).hydricFraction
    ).toBeNull();
  });

  it("skips an averaged class whose union collapsed to no polygon", async () => {
    stubDatabase({
      coverage: [coverageRow()],
      aggregate: [
        {
          drainage_class: "well-drained",
          geometry: null,
          map_unit_count: "2",
          hydric_count: "0",
          rated_count: "0",
          input_rows: "2",
        },
      ],
    });

    expect((await getSoilSurvey(BBOX, SOIL_SURVEY_REGIONAL_MIN_ZOOM)).features).toEqual([]);
  });

  it("never rejects an oversized viewport once zoom selects an averaged tier", async () => {
    mocks.fetchBoundedJson.mockResolvedValue({});
    stubDatabase({ coverage: [], aggregate: [], storedCount: "0" });

    await expect(getSoilSurvey("-120,42,-112,48", 4)).resolves.toMatchObject({
      granularity: "coarse-average",
      truncated: true,
    });
  });
});

describe("the backfill driver", () => {
  const PNW = "-125,42,-111,49";

  it("is bounded by maxCells and reports what remains", async () => {
    mocks.fetchBoundedJson.mockResolvedValue(soilTable([soilRow()]));
    stubDatabase({ coverage: [], storedCount: "1" });

    const report = await backfillSoilSurvey({ bbox: PNW, maxCells: 2, concurrency: 1 });

    expect(report.cellsInEnvelope).toBe(112 * 56);
    expect(report.attempted).toBe(2);
    expect(mocks.fetchBoundedJson).toHaveBeenCalledTimes(2);
    expect(report.remaining).toBe(112 * 56 - 2);
    expect(report.polygonsStored).toBe(2);
  });

  it("uses the ledger as its cursor, so a rerun skips what is already covered", async () => {
    // No cursor of its own: coverage IS the resume point, which is what makes an
    // interrupted multi-hour run safe to restart.
    const covered = soilSurveyCellsCovering([-125, 42, -111, 49])
      .slice(0, 3)
      .map((cell) => coverageRow({ cell_key: cell.key }));
    mocks.fetchBoundedJson.mockResolvedValue(soilTable([soilRow()]));
    stubDatabase({ coverage: covered, storedCount: "1" });

    const report = await backfillSoilSurvey({ bbox: PNW, maxCells: 1, concurrency: 1 });

    expect(report.alreadyCovered).toBe(3);
    // The fourth cell, not the first.
    expect(report.cells[0].cellKey).not.toBe(covered[0].cell_key);
  });

  it("records a failed cell without covering it, so a retry picks it up", async () => {
    mocks.fetchBoundedJson.mockRejectedValue(new Error("SDA timed out"));
    stubDatabase({ coverage: [] });

    const report = await backfillSoilSurvey({ bbox: PNW, maxCells: 1, concurrency: 1 });

    expect(report.failed).toBe(1);
    expect(report.cells[0].error).toBe("SDA timed out");
    expect(report.remaining).toBe(112 * 56);
    expect(someStatementContains("INSERT INTO geo.soil_survey_coverage")).toBe(false);
  });
});
