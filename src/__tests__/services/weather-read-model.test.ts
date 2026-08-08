import { beforeEach, describe, expect, it, vi } from "vitest";

// Chainable drizzle query-builder stub: every intermediate call returns the
// same object, `.limit()` resolves to the rows the test configures.
// vi.hoisted keeps this ahead of the vi.mock factory's hoist point.
const queryChain = vi.hoisted(() => ({
  select: vi.fn((..._args: unknown[]) => queryChain),
  from: vi.fn((..._args: unknown[]) => queryChain),
  innerJoin: vi.fn((..._args: unknown[]) => queryChain),
  where: vi.fn((..._args: unknown[]) => queryChain),
  orderBy: vi.fn((..._args: unknown[]) => queryChain),
  limit: vi.fn(() => Promise.resolve([] as Array<{ properties: unknown }>)),
}));

vi.mock("@/lib/server/db", () => ({
  db: { select: (...args: unknown[]) => queryChain.select(...args) },
}));

import {
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
    queryChain.select.mockReturnValue(queryChain);
    queryChain.from.mockReturnValue(queryChain);
    queryChain.innerJoin.mockReturnValue(queryChain);
    queryChain.where.mockReturnValue(queryChain);
    queryChain.orderBy.mockReturnValue(queryChain);
  });

  it("rejects a malformed bbox before querying the warehouse", async () => {
    await expect(getPublishedWeatherForBbox("garbage")).rejects.toThrow(
      RangeError
    );
    expect(queryChain.select).not.toHaveBeenCalled();
  });

  it("returns fresh, complete observations and excludes incomplete ones", async () => {
    const observedAt = freshObservedAt();
    queryChain.limit.mockResolvedValue([
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
    ]);

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
    queryChain.limit.mockResolvedValue([
      row({
        geometry: { type: "Point", coordinates: [-122, 45] },
        observedAt: staleObservedAt,
        windSpeed: 4,
        windDirection: 270,
        temperature: 18,
        humidity: 60,
      }),
    ]);

    const result = await getPublishedWeatherForBbox(PNW_BBOX);
    expect(result).toHaveLength(0);
  });

  it("caps the warehouse read at the bbox row limit", async () => {
    queryChain.limit.mockResolvedValue([]);
    await getPublishedWeatherForBbox(PNW_BBOX);
    expect(queryChain.limit).toHaveBeenCalledWith(500);
  });
});
