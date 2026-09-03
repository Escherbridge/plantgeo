import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Every `placesRouter` procedure is `publicProcedure`, so its zod schema is the only thing
 * standing between an anonymous caller and a PostGIS scan. Until 2026-09-03 the schemas bounded
 * nothing spatial -- `lat`/`lon` were bare `z.number()`, `radius` a bare `z.number()` with a
 * default and no ceiling, and a bbox could be handed to `ST_MakeEnvelope` inverted or infinite.
 *
 * The service is stubbed: what is under test is which inputs reach it at all, and a real reader
 * would put a database in the way of that question. See `services/places.test.ts` for the
 * spatial predicates themselves.
 */

const places = vi.hoisted(() => ({
  searchByCategory: vi.fn(async () => ({ places: [], truncated: false })),
  searchByText: vi.fn(async () => ({ places: [], truncated: false })),
  searchNearby: vi.fn(async () => ({ places: [], truncated: false })),
  getById: vi.fn(async () => null),
}));

vi.mock("@/lib/server/db", () => ({ db: {} }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: vi.fn() }));
vi.mock("@/lib/server/services/places", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/server/services/places")>();
  return { ...actual, ...places };
});

import type { Context } from "@/lib/server/trpc/init";
import { placesRouter } from "@/lib/server/trpc/routers/places";

const caller = placesRouter.createCaller({ db: {}, session: null } as unknown as Context);

const PORTLAND_BBOX = { west: -122.8, south: 45.4, east: -122.5, north: 45.6 };

interface BoundingBox {
  west: number;
  south: number;
  east: number;
  north: number;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("nearby bounds", () => {
  it("applies the documented radius and page defaults", async () => {
    await caller.nearby({ lat: 45.52, lon: -122.68 });

    expect(places.searchNearby).toHaveBeenCalledWith(45.52, -122.68, 1_000, 20);
  });

  it("admits the widest radius and page it documents", async () => {
    await caller.nearby({ lat: 45.52, lon: -122.68, radius: 50_000, limit: 100 });

    expect(places.searchNearby).toHaveBeenCalledWith(45.52, -122.68, 50_000, 100);
  });

  // ST_DWithin's third argument is a typed `double precision` PostGIS parameter, so a
  // fractional value bound into it needs no `::numeric` cast and no `.int()` schema guard.
  it("accepts a fractional radius and passes it through unrounded", async () => {
    await caller.nearby({ lat: 45.52, lon: -122.68, radius: 1_000.5 });

    expect(places.searchNearby).toHaveBeenCalledWith(45.52, -122.68, 1_000.5, 20);
  });

  const rejectedPoints: { label: string; lat: number; lon: number }[] = [
    { label: "a latitude above the pole", lat: 90.1, lon: -122.68 },
    { label: "a latitude below the pole", lat: -90.1, lon: -122.68 },
    { label: "a longitude past the antimeridian", lat: 45.52, lon: 180.1 },
    { label: "an infinite coordinate", lat: Number.POSITIVE_INFINITY, lon: -122.68 },
    { label: "a NaN coordinate", lat: Number.NaN, lon: -122.68 },
  ];
  for (const { label, lat, lon } of rejectedPoints) {
    it(`rejects ${label}`, async () => {
      await expect(caller.nearby({ lat, lon })).rejects.toThrow();
      expect(places.searchNearby).not.toHaveBeenCalled();
    });
  }

  const rejectedRadii: { label: string; radius: number }[] = [
    { label: "a radius wider than the browsing ceiling", radius: 50_001 },
    { label: "a zero radius", radius: 0 },
    { label: "a negative radius", radius: -1 },
  ];
  for (const { label, radius } of rejectedRadii) {
    it(`rejects ${label}`, async () => {
      await expect(caller.nearby({ lat: 45.52, lon: -122.68, radius })).rejects.toThrow();
      expect(places.searchNearby).not.toHaveBeenCalled();
    });
  }

  const rejectedPages: { label: string; limit: number }[] = [
    { label: "a page larger than the cap", limit: 101 },
    { label: "an empty page", limit: 0 },
  ];
  for (const { label, limit } of rejectedPages) {
    it(`rejects ${label}`, async () => {
      await expect(caller.nearby({ lat: 45.52, lon: -122.68, limit })).rejects.toThrow();
      expect(places.searchNearby).not.toHaveBeenCalled();
    });
  }
});

describe("bounding-box bounds", () => {
  it("passes an ordered, in-range viewport through", async () => {
    await caller.byCategory({ category: "parks", bbox: PORTLAND_BBOX });

    expect(places.searchByCategory).toHaveBeenCalledWith("parks", PORTLAND_BBOX);
  });

  const rejectedBoxes: { label: string; bbox: BoundingBox }[] = [
    {
      label: "an east edge west of the west edge",
      bbox: { ...PORTLAND_BBOX, west: -122.5, east: -122.8 },
    },
    {
      label: "a north edge south of the south edge",
      bbox: { ...PORTLAND_BBOX, south: 45.6, north: 45.4 },
    },
    {
      label: "a degenerate envelope",
      bbox: { west: -122.6, south: 45.5, east: -122.6, north: 45.5 },
    },
    {
      label: "a longitude past the antimeridian",
      bbox: { ...PORTLAND_BBOX, east: 180.5 },
    },
    { label: "a latitude past the pole", bbox: { ...PORTLAND_BBOX, north: 90.5 } },
    {
      label: "an infinite edge",
      bbox: { ...PORTLAND_BBOX, east: Number.POSITIVE_INFINITY },
    },
    {
      label: "a viewport wider than the browsing ceiling",
      bbox: { west: -130, south: 30, east: -120, north: 40 }, // 10deg x 10deg = 100 sq deg
    },
  ];
  for (const { label, bbox } of rejectedBoxes) {
    it(`rejects ${label}`, async () => {
      await expect(caller.byCategory({ category: "parks", bbox })).rejects.toThrow();
      expect(places.searchByCategory).not.toHaveBeenCalled();
    });
  }

  it("requires a viewport on a text search", async () => {
    // @ts-expect-error -- bbox is required by the input type too; this pins the runtime refusal.
    await expect(caller.search({ query: "fire station" })).rejects.toThrow();
    expect(places.searchByText).not.toHaveBeenCalled();
  });

  it("passes an ordered, in-range viewport through on a text search", async () => {
    await caller.search({ query: "fire station", bbox: PORTLAND_BBOX });

    expect(places.searchByText).toHaveBeenCalledWith("fire station", PORTLAND_BBOX);
  });

  it("rejects an oversized viewport on a text search the same way byCategory does", async () => {
    await expect(
      caller.search({
        query: "fire station",
        bbox: { west: -130, south: 30, east: -120, north: 40 },
      })
    ).rejects.toThrow();
    expect(places.searchByText).not.toHaveBeenCalled();
  });
});

describe("text bounds", () => {
  // Both cases below supply a valid bbox so the rejection can only be the query check under
  // test -- bbox is required on `search` too (see "bounding-box bounds"), and a missing one
  // would throw for an unrelated reason and pass this test regardless of the query.

  it("trims before measuring, so whitespace is not a query", async () => {
    await expect(caller.search({ query: "   ", bbox: PORTLAND_BBOX })).rejects.toThrow();
    expect(places.searchByText).not.toHaveBeenCalled();
  });

  it("rejects a query longer than the bound", async () => {
    await expect(
      caller.search({ query: "x".repeat(201), bbox: PORTLAND_BBOX })
    ).rejects.toThrow();
    expect(places.searchByText).not.toHaveBeenCalled();
  });

  it("rejects a category wider than the column", async () => {
    await expect(
      caller.byCategory({ category: "c".repeat(51), bbox: PORTLAND_BBOX })
    ).rejects.toThrow();
    expect(places.searchByCategory).not.toHaveBeenCalled();
  });

  it("rejects an id that is not a uuid", async () => {
    await expect(caller.getById({ id: "not-a-uuid" })).rejects.toThrow();
    expect(places.getById).not.toHaveBeenCalled();
  });
});
