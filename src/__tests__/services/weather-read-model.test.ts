import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Chainable drizzle query-builder stub: every intermediate call returns the same object.
 * vi.hoisted keeps this ahead of the vi.mock factory's hoist point.
 *
 * `getPublishedWeatherForBbox` now resolves `WEATHER_LAYER_ID` through the shared
 * `resolveCachedLayerId` (`db.select(...).from(layers)...limit(1)`) BEFORE its own
 * `db.select(...).from(features)...limit(500)` -- both share this one mocked chain.
 * `.limit()` tells them apart by the table `.from()` was last called with, not by call
 * order: keyed on `lastFromTable === layers` -> a fake layer id, else -> `featureRows` (set
 * directly by each test rather than via `.mockResolvedValue`, which would also feed the
 * fixture to the resolver's own call).
 */
const queryChain = vi.hoisted(() => {
  const chain: {
    lastFromTable: unknown;
    featureRows: unknown[];
    select: ReturnType<typeof vi.fn>;
    from: ReturnType<typeof vi.fn>;
    innerJoin: ReturnType<typeof vi.fn>;
    where: ReturnType<typeof vi.fn>;
    orderBy: ReturnType<typeof vi.fn>;
    limit: ReturnType<typeof vi.fn>;
  } = {
    lastFromTable: undefined,
    featureRows: [],
    select: vi.fn((..._args: unknown[]) => chain),
    from: vi.fn((table: unknown) => {
      chain.lastFromTable = table;
      return chain;
    }),
    innerJoin: vi.fn((..._args: unknown[]) => chain),
    where: vi.fn((..._args: unknown[]) => chain),
    orderBy: vi.fn((..._args: unknown[]) => chain),
    limit: vi.fn(() => Promise.resolve([] as Array<{ properties: unknown }>)),
  };
  return chain;
});

const FAKE_LAYER_ID = "11111111-1111-4111-8111-111111111111";

vi.mock("@/lib/server/db", () => ({
  db: { select: (...args: unknown[]) => queryChain.select(...args) },
}));

import { layers } from "@/lib/server/db/schema";
import {
  clearLayerIdCache,
  getPublishedWeatherForBbox,
  isRenderableWeatherObservation,
  parseBbox,
} from "@/lib/server/services/environmental-read-model";

const PNW_BBOX = "-124,42,-117,49";

function row(properties: Record<string, unknown>) {
  return { properties };
}

function freshObservedAt(): string {
  return new Date(Date.now() - 30 * 60 * 1000).toISOString();
}

describe("parseBbox", () => {
  it("parses a well-formed west,south,east,north string", () => {
    expect(parseBbox(PNW_BBOX)).toEqual([-124, 42, -117, 49]);
  });

  it("rejects malformed input", () => {
    expect(() => parseBbox("not,a,bbox")).toThrow(RangeError);
    expect(() => parseBbox("-124,42,-117")).toThrow(RangeError);
    expect(() => parseBbox("")).toThrow(RangeError);
  });

  it("rejects out-of-range or inverted bounds", () => {
    expect(() => parseBbox("-200,42,-117,49")).toThrow(RangeError);
    expect(() => parseBbox("-117,42,-124,49")).toThrow(RangeError); // west >= east
    expect(() => parseBbox("-124,49,-117,42")).toThrow(RangeError); // south >= north
  });
});

describe("isRenderableWeatherObservation", () => {
  it("accepts a complete wind pair or a temperature, and nothing less", () => {
    expect(
      isRenderableWeatherObservation({
        windSpeed: 3,
        windDirection: 180,
        temperature: 20,
      })
    ).toBe(true);
    // Temperature alone draws the temperature dots.
    expect(
      isRenderableWeatherObservation({
        windSpeed: null,
        windDirection: 180,
        temperature: 20,
      })
    ).toBe(true);
    // A wind pair alone draws the arrows.
    expect(
      isRenderableWeatherObservation({
        windSpeed: 3,
        windDirection: 180,
        temperature: null,
      })
    ).toBe(true);
    // Half a wind pair and no temperature draws nothing.
    expect(
      isRenderableWeatherObservation({
        windSpeed: 3,
        windDirection: null,
        temperature: null,
      })
    ).toBe(false);
  });
});

describe("getPublishedWeatherForBbox", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryChain.lastFromTable = undefined;
    queryChain.featureRows = [];
    queryChain.select.mockReturnValue(queryChain);
    queryChain.from.mockImplementation((table: unknown) => {
      queryChain.lastFromTable = table;
      return queryChain;
    });
    queryChain.innerJoin.mockReturnValue(queryChain);
    queryChain.where.mockReturnValue(queryChain);
    queryChain.orderBy.mockReturnValue(queryChain);
    // A `.from(layers)` call is always resolveCachedLayerId's own lookup; anything else is
    // the reader's real feature select, answered from `queryChain.featureRows`.
    queryChain.limit.mockImplementation(() =>
      Promise.resolve(
        queryChain.lastFromTable === layers ? [{ id: FAKE_LAYER_ID }] : queryChain.featureRows
      )
    );
    // resolveCachedLayerId memoizes a hit in module scope; clear it so every test's resolver
    // call goes through this mock rather than a cached id from a previous test.
    clearLayerIdCache();
  });

  it("rejects a malformed bbox before querying the warehouse", async () => {
    await expect(getPublishedWeatherForBbox("garbage")).rejects.toThrow(
      RangeError
    );
    expect(queryChain.select).not.toHaveBeenCalled();
  });

  it("returns fresh, complete observations and excludes incomplete ones", async () => {
    const observedAt = freshObservedAt();
    queryChain.featureRows = [
      row({
        geometry: { type: "Point", coordinates: [-122, 45] },
        observedAt,
        windSpeed: 4,
        windDirection: 270,
        temperature: 18,
        humidity: 60,
      }),
      row({
        // missing humidity -- tooltip-only, so it renders with humidity null
        geometry: { type: "Point", coordinates: [-121, 46] },
        observedAt,
        windSpeed: 5,
        windDirection: 90,
        temperature: 17,
        humidity: null,
      }),
      row({
        // no drawable signal at all -- dropped, never zero-filled
        geometry: { type: "Point", coordinates: [-120, 47] },
        observedAt,
        windSpeed: 6,
        windDirection: null,
        temperature: null,
        humidity: 40,
      }),
    ];

    const result = await getPublishedWeatherForBbox(PNW_BBOX);

    expect(result).toHaveLength(2);
    expect(result[0]).toMatchObject({
      lat: 45,
      lon: -122,
      windSpeed: 4,
      windDirection: 270,
      temperature: 18,
      humidity: 60,
    });
    expect(result[1]).toMatchObject({
      lat: 46,
      lon: -121,
      humidity: null,
    });
  });

  it("excludes stale observations", async () => {
    const staleObservedAt = new Date(Date.now() - 6 * 60 * 60 * 1000).toISOString();
    queryChain.featureRows = [
      row({
        geometry: { type: "Point", coordinates: [-122, 45] },
        observedAt: staleObservedAt,
        windSpeed: 4,
        windDirection: 270,
        temperature: 18,
        humidity: 60,
      }),
    ];

    const result = await getPublishedWeatherForBbox(PNW_BBOX);
    expect(result).toHaveLength(0);
  });

  it("caps the warehouse read at the bbox row limit", async () => {
    queryChain.featureRows = [];
    await getPublishedWeatherForBbox(PNW_BBOX);
    expect(queryChain.limit).toHaveBeenCalledWith(500);
  });
});
