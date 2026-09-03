import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The POI readers behind `placesRouter`, whose spatial predicates did not exist until
 * 2026-09-03: `searchNearby(_lat, _lon, _radius, limit)` was `select().from(poi).limit(limit)`,
 * so an unauthenticated "what is near me" answered with arbitrary rows from anywhere on earth,
 * and both bbox readers discarded the viewport the same way. The `_`-prefixed parameters that
 * satisfied `noUnusedParameters` are what made a wrong answer look like a deliberate one.
 *
 * These cases therefore assert the SQL rather than the rows: what was missing was a predicate,
 * and a fixture-shaped row count cannot tell a filtered read from an unfiltered one. Drizzle is
 * real here -- only the driver underneath it is a stand-in -- so the statements inspected are
 * the ones postgres would receive.
 */

type Recorded = {
  select: unknown[];
  where: unknown[];
  orderBy: unknown[];
  limit: number[];
  rows: unknown[];
};

const recorded = vi.hoisted<Recorded>(() => ({
  select: [],
  where: [],
  orderBy: [],
  limit: [],
  rows: [],
}));

/**
 * Chainable drizzle stand-in. Every builder method records its argument and returns itself;
 * `.limit()` is the terminal and resolves whatever the case queued in `recorded.rows`.
 */
vi.mock("@/lib/server/db", () => ({
  db: {
    select: (columns?: unknown) => {
      recorded.select.push(columns);
      const chain = {
        from: () => chain,
        where: (condition: unknown) => {
          recorded.where.push(condition);
          return chain;
        },
        orderBy: (order: unknown) => {
          recorded.orderBy.push(order);
          return chain;
        },
        limit: (count: number) => {
          recorded.limit.push(count);
          return Promise.resolve(recorded.rows);
        },
      };
      return chain;
    },
  },
}));

import {
  MAX_PLACE_RESULTS,
  escapeLikeWildcards,
  getById,
  searchByCategory,
  searchByText,
  searchNearby,
} from "@/lib/server/services/places";

const PORTLAND_BBOX = { west: -122.8, south: 45.4, east: -122.5, north: 45.6 };

/**
 * Flattens a drizzle `sql` template back to its literal text. Columns and bound parameters
 * contribute no text of their own, which is all these assertions need: what is under test is
 * which PostGIS functions the statement calls, not what was bound into them. Mirrors the helper
 * of the same name in `environmental-read-model.test.ts`.
 */
function renderSqlText(value: unknown): string {
  if (value === null || typeof value !== "object") return "";
  const node = value as { queryChunks?: unknown[]; value?: unknown };
  if (Array.isArray(node.queryChunks)) {
    return node.queryChunks.map(renderSqlText).join("");
  }
  if (Array.isArray(node.value)) return node.value.join("");
  return "";
}

/** The text of the only `where` clause the case under test issued. */
function onlyWhereClause(): string {
  expect(recorded.where).toHaveLength(1);
  return renderSqlText(recorded.where[0]);
}

/**
 * Every raw value bound into a `sql` tree. The `sql` template pushes interpolated primitives into
 * `queryChunks` as-is (drizzle wraps them in `Param` only when the query is built), so a primitive
 * chunk IS a bound value; an explicit `Param` carries it under `.value`, which is never an array --
 * that is what distinguishes it from a `StringChunk` (whose `.value` always is one) and from a
 * `Column` (which carries no `.value` at all) during the walk.
 */
function collectBoundParams(value: unknown): unknown[] {
  if (value === null || value === undefined) return [];
  if (typeof value !== "object") return [value];
  const node = value as { queryChunks?: unknown[]; value?: unknown };
  if (Array.isArray(node.queryChunks)) {
    return node.queryChunks.flatMap(collectBoundParams);
  }
  if ("value" in node && !Array.isArray(node.value)) {
    return [node.value];
  }
  return [];
}

/** `count` distinct rows, enough to push a reader past whatever cap it applies. */
function fakeRows(count: number) {
  return [...Array(count).keys()].map((index) => ({
    id: `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
    name: `Place ${index}`,
    longitude: -122.6 + index * 1e-4,
    latitude: 45.5,
    category: "parks",
    subcategory: null,
    address: null,
    phone: null,
    website: null,
    hours: {},
    tags: {},
    distanceMeters: index * 10,
  }));
}

beforeEach(() => {
  recorded.select = [];
  recorded.where = [];
  recorded.orderBy = [];
  recorded.limit = [];
  recorded.rows = [];
});

describe("searchNearby", () => {
  it("filters on a metric radius around the point and orders by distance", async () => {
    recorded.rows = fakeRows(3);

    await searchNearby(45.52, -122.68, 2_000, 20);

    const where = onlyWhereClause();
    expect(where).toContain("ST_DWithin");
    expect(where).toContain("ST_MakePoint");
    // Metres, not degrees: without the cast the third argument would be an SRID-4326 degree
    // distance, which is a different radius at every latitude.
    expect(where).toContain("::geography");
    // Index pre-filter ahead of the exact geodesic test: idx_poi_geom (GiST on the geometry
    // column) answers `&&` against a buffered envelope, not ST_DWithin on a geography cast.
    expect(where).toContain("&&");
    expect(where).toContain("ST_Envelope");
    expect(where).toContain("ST_Buffer");

    expect(renderSqlText(recorded.orderBy[0])).toContain("ST_Distance");
  });

  it("over-fetches by one so a full page can report itself truncated", async () => {
    recorded.rows = fakeRows(21);

    const result = await searchNearby(45.52, -122.68, 2_000, 20);

    expect(recorded.limit).toEqual([21]);
    expect(result.places).toHaveLength(20);
    expect(result.truncated).toBe(true);
  });

  it("reports a short page as complete", async () => {
    recorded.rows = fakeRows(3);

    const result = await searchNearby(45.52, -122.68, 2_000, 20);

    expect(result.places).toHaveLength(3);
    expect(result.truncated).toBe(false);
    expect(result.places[0].distanceMeters).toBe(0);
  });
});

describe("searchByCategory", () => {
  it("restricts the read to the viewport envelope", async () => {
    recorded.rows = fakeRows(2);

    await searchByCategory("parks", PORTLAND_BBOX);

    const where = onlyWhereClause();
    expect(where).toContain("ST_MakeEnvelope");
    // `&&` rather than ST_Intersects: it is the predicate idx_poi_geom can answer, and the
    // envelope is already the question being asked.
    expect(where).toContain("&&");
    expect(recorded.limit).toEqual([MAX_PLACE_RESULTS + 1]);
  });

  it("projects columns rather than selecting the raw WKB geom", async () => {
    recorded.rows = fakeRows(1);

    await searchByCategory("parks", PORTLAND_BBOX);

    const projected = recorded.select.at(-1) as Record<string, unknown>;
    expect(Object.keys(projected)).not.toContain("geom");
    expect(Object.keys(projected)).toEqual(
      expect.arrayContaining(["id", "name", "longitude", "latitude"])
    );
  });
});

describe("searchByText", () => {
  it("restricts the read to the viewport envelope", async () => {
    recorded.rows = fakeRows(2);

    await searchByText("fire station", PORTLAND_BBOX);

    const where = onlyWhereClause();
    expect(where).toContain("ILIKE");
    expect(where).toContain("ST_MakeEnvelope");
  });

  it("caps a full page of a viewport-bounded text search and says so", async () => {
    recorded.rows = fakeRows(MAX_PLACE_RESULTS + 1);

    const result = await searchByText("a", PORTLAND_BBOX);

    expect(result.places).toHaveLength(MAX_PLACE_RESULTS);
    expect(result.truncated).toBe(true);
  });

  it("binds the escaped pattern, not the raw query text, into the ILIKE parameter", async () => {
    recorded.rows = fakeRows(1);
    const rawQuery = "100% off_beaten\\path";

    await searchByText(rawQuery, PORTLAND_BBOX);

    const bound = collectBoundParams(recorded.where[0]);
    expect(bound).toContain(`%${escapeLikeWildcards(rawQuery)}%`);
    expect(bound).not.toContain(`%${rawQuery}%`);
  });
});

describe("escapeLikeWildcards", () => {
  it("escapes %, _ and \\ and leaves plain text untouched", () => {
    expect(escapeLikeWildcards("100% off")).toBe("100\\% off");
    expect(escapeLikeWildcards("a_b")).toBe("a\\_b");
    expect(escapeLikeWildcards("back\\slash")).toBe("back\\\\slash");
    expect(escapeLikeWildcards("plain text")).toBe("plain text");
  });
});

describe("getById", () => {
  it("answers null rather than undefined for an id nothing carries", async () => {
    recorded.rows = [];

    expect(await getById("00000000-0000-4000-8000-000000000000")).toBeNull();
  });
});
