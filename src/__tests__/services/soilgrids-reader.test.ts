import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/services/parquet-plane-client", () => ({ getParquetLatestRelease: vi.fn() }));

import { UpstreamHttpError } from "@/lib/server/http/bounded-upstream";
import { getParquetLatestRelease } from "@/lib/server/services/parquet-plane-client";
import {
  getSoilProperties,
  resetSoilPropertiesCacheForTests,
  selectNearestCell,
  soilReadBbox,
} from "@/lib/server/services/soilgrids";
import type { SoilGridsMapped } from "@/lib/server/services/site-brief";

const MAPPED: SoilGridsMapped = {
  "0-5cm": { phh2o: 57, soc: 243, nitrogen: 190, bdod: 121, cec: 182, ocd: 380, clay: 189, sand: 371, silt: 440, cfvo: 98 },
  "5-15cm": { phh2o: 57, soc: 210, nitrogen: 160, bdod: 127, cec: 170, ocd: 340, clay: 195, sand: 380, silt: 425, cfvo: 102 },
  "15-30cm": { phh2o: 58, soc: 180, nitrogen: 130, bdod: 131, cec: 160, ocd: 300, clay: 201, sand: 390, silt: 409, cfvo: 106 },
};
const COLUMN_DEPTH = { "0-5cm": "0_5cm", "5-15cm": "5_15cm", "15-30cm": "15_30cm" } as const;

/** One lane row at a SW origin, carrying the mapped integers as float64 the way the lane stores them. */
function row(cellLongitude: number, cellLatitude: number, mapped: SoilGridsMapped = MAPPED, override: Record<string, number> = {}) {
  const values: Record<string, number> = {};
  for (const depth of Object.keys(COLUMN_DEPTH) as (keyof typeof COLUMN_DEPTH)[]) {
    for (const [property, value] of Object.entries(mapped[depth])) values[`${property}_${COLUMN_DEPTH[depth]}`] = value;
  }
  return {
    cell_longitude: cellLongitude, cell_latitude: cellLatitude, source_release: "soilgrids-v2.0",
    source_manifest_sha256: "a".repeat(64), release_day: "2020-06-02", ...values, ...override,
  };
}

function published(rows: unknown[]) {
  return { state: "published" as const, requestedDay: "2026-09-27", servedDay: "2020-06-02", rows: rows as Record<string, unknown>[], truncated: false };
}

// Boise: the cell with origin (-116.205, 43.600) has its centre at (-116.2025, 43.6025).
const BOISE = { lat: 43.6025, lon: -116.2025 };

beforeEach(() => {
  resetSoilPropertiesCacheForTests();
  vi.mocked(getParquetLatestRelease).mockReset();
  vi.stubEnv("SOIL_PROPERTIES_READS_ENABLED", "true");
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.useRealTimers();
});

describe("SOIL_PROPERTIES_READS_ENABLED kill switch", () => {
  it.each([undefined, "", "false", "TRUE", "1"])("answers reads_disabled without reading when the flag is %s", async (value) => {
    // `unstubAllEnvs` restores the original (unset) value after the delete as well.
    if (value === undefined) delete process.env.SOIL_PROPERTIES_READS_ENABLED;
    else vi.stubEnv("SOIL_PROPERTIES_READS_ENABLED", value);
    expect(await getSoilProperties(BOISE.lat, BOISE.lon)).toEqual({ state: "unavailable", reason: "reads_disabled" });
    expect(getParquetLatestRelease).not.toHaveBeenCalled();
  });

  it("is checked before the cache, so switching it off takes effect at once", async () => {
    vi.mocked(getParquetLatestRelease).mockResolvedValue(published([row(-116.205, 43.6)]));
    expect((await getSoilProperties(BOISE.lat, BOISE.lon)).state).toBe("available");
    vi.stubEnv("SOIL_PROPERTIES_READS_ENABLED", "false");
    expect(await getSoilProperties(BOISE.lat, BOISE.lon)).toEqual({ state: "unavailable", reason: "reads_disabled" });
  });
});

describe("lane read and nearest-centre selection (CONTRACT C2)", () => {
  it("reads z13 at the server date inside the radius bbox and returns the labelled estimate", async () => {
    vi.mocked(getParquetLatestRelease).mockResolvedValue(published([row(-116.205, 43.6), row(-116.2, 43.6)]));
    const read = await getSoilProperties(BOISE.lat, BOISE.lon);
    const [request] = vi.mocked(getParquetLatestRelease).mock.calls[0];
    expect(request).toMatchObject({ layer: "soil-properties", kind: "observed", zoomTier: 13 });
    expect(request.asOfDay).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    const expected = soilReadBbox(BOISE.lat, BOISE.lon, 1000);
    expect(request.bbox).toBe(`${expected.west},${expected.south},${expected.east},${expected.north}`);
    expect(expected.north - BOISE.lat).toBeCloseTo(1000 / 110574 + 0.005, 12);
    expect(read).toMatchObject({
      state: "available",
      properties: {
        ph: 5.7, organicCarbon: 24.3, nitrogen: 1.9, bulkDensity: 1.21, cec: 18.2, ocd: 38,
        basis: "model_estimate", releaseId: "soilgrids-v2.0/2020-06-02", distanceM: 0,
        label: "SoilGrids v2.0 250 m model estimate, 0-5 cm, cell centre 0 m away (release soilgrids-v2.0/2020-06-02)",
        mapped: MAPPED,
        topsoil: { ph: 5.8, texture_class: "loam", label: "SoilGrids v2.0 250 m model estimate, 0-30 cm (thickness-weighted)" },
      },
    });
  });

  it("breaks an exact distance tie by the lower latitude, then the lower longitude", () => {
    const lower = { cell_longitude: 0, cell_latitude: -0.005 };
    const upper = { cell_longitude: 0, cell_latitude: 0 };
    expect(selectNearestCell([upper, lower], 0, 0.0025, 1000)?.row).toBe(lower);
    const west = { cell_longitude: -0.005, cell_latitude: 0 };
    const east = { cell_longitude: 0, cell_latitude: 0 };
    expect(selectNearestCell([east, west], 0.0025, 0, 1000)?.row).toBe(west);
  });

  it("refuses beyond the radius with no_cell_within_radius and never widens it", async () => {
    // One cell whose centre is ~1.6 km north of the point.
    vi.mocked(getParquetLatestRelease).mockResolvedValue(published([row(-116.205, 43.615)]));
    expect(await getSoilProperties(BOISE.lat, BOISE.lon)).toEqual({ state: "unavailable", reason: "no_cell_within_radius", radiusM: 1000 });
    expect(await getSoilProperties(BOISE.lat, BOISE.lon, { radiusMeters: 2000 })).toMatchObject({ state: "available", properties: { distanceM: 1668 } });
  });

  it("treats a non-integral base value as a corrupt lane, never rounding it", async () => {
    vi.mocked(getParquetLatestRelease).mockResolvedValue(published([row(-116.205, 43.6, MAPPED, { phh2o_0_5cm: 57.3 })]));
    expect(await getSoilProperties(BOISE.lat, BOISE.lon)).toEqual({ state: "unavailable", reason: "read_failed" });
  });

  it("answers outside the pinned lattice envelope without reading", async () => {
    expect(await getSoilProperties(40.7, -74)).toEqual({ state: "unavailable", reason: "outside_release_coverage" });
    expect(getParquetLatestRelease).not.toHaveBeenCalled();
  });
});

describe("refusal reasons (CONTRACT C5.6)", () => {
  it("passes lane_never_written through and maps an unwritten day to not_published", async () => {
    vi.mocked(getParquetLatestRelease).mockResolvedValueOnce({ state: "lane_never_written", requestedDay: "2026-09-27" });
    expect(await getSoilProperties(BOISE.lat, BOISE.lon)).toEqual({ state: "unavailable", reason: "lane_never_written" });
    vi.mocked(getParquetLatestRelease).mockResolvedValueOnce({ state: "day_not_written", requestedDay: "2026-09-27" });
    expect(await getSoilProperties(BOISE.lat, BOISE.lon)).toEqual({ state: "unavailable", reason: "not_published" });
  });

  it("names agri's serving_at_capacity refusal rather than folding it into read_failed", async () => {
    vi.mocked(getParquetLatestRelease).mockRejectedValue(new UpstreamHttpError(409,
      JSON.stringify({ error: { code: "serving_at_capacity", message: "all 3 serving slots busy" } })));
    expect(await getSoilProperties(BOISE.lat, BOISE.lon)).toEqual({ state: "unavailable", reason: "serving_at_capacity" });
  });

  it("gives up at its deadline with timeout", async () => {
    vi.mocked(getParquetLatestRelease).mockImplementation(() => new Promise(() => {}));
    expect(await getSoilProperties(BOISE.lat, BOISE.lon, { timeoutMs: 5 })).toEqual({ state: "unavailable", reason: "timeout" });
  });

  it("does not cache a refusal: the next request reads again", async () => {
    vi.mocked(getParquetLatestRelease).mockResolvedValueOnce({ state: "lane_never_written", requestedDay: "2026-09-27" });
    await getSoilProperties(BOISE.lat, BOISE.lon);
    vi.mocked(getParquetLatestRelease).mockResolvedValueOnce(published([row(-116.205, 43.6)]));
    expect((await getSoilProperties(BOISE.lat, BOISE.lon)).state).toBe("available");
    expect(getParquetLatestRelease).toHaveBeenCalledTimes(2);
  });
});

describe("caller cancellation (review M8)", () => {
  it("reads an aborted brief deadline as timeout and lets go of the transport", async () => {
    let transportSignal: AbortSignal | undefined;
    vi.mocked(getParquetLatestRelease).mockImplementation((request) => {
      transportSignal = request.signal;
      return new Promise(() => {});
    });
    const controller = new AbortController();
    const read = getSoilProperties(BOISE.lat, BOISE.lon, { signal: controller.signal });
    controller.abort();
    expect(await read).toEqual({ state: "unavailable", reason: "timeout" });
    expect(transportSignal?.aborted).toBe(true);
  });

  it("never reads when the deadline has already passed", async () => {
    vi.mocked(getParquetLatestRelease).mockImplementation(() => new Promise(() => {}));
    expect(await getSoilProperties(BOISE.lat, BOISE.lon, { signal: AbortSignal.abort() })).toEqual({ state: "unavailable", reason: "timeout" });
  });
});

describe("LRU cache", () => {
  it("serves a second point in the same cell from cache and re-measures its own distance", async () => {
    vi.mocked(getParquetLatestRelease).mockResolvedValue(published([row(-116.205, 43.6)]));
    await getSoilProperties(BOISE.lat, BOISE.lon);
    const second = await getSoilProperties(BOISE.lat + 0.002, BOISE.lon);
    expect(getParquetLatestRelease).toHaveBeenCalledTimes(1);
    expect(second).toMatchObject({ state: "available", properties: { distanceM: 222 } });
  });
});

/**
 * Review M4: the cache key is the QUERY point's cell, so a neighbour chosen for one point must never
 * be served to another point of the same masked cell. The masked cell (origin -116.205, 43.600) has
 * neighbours west (centre -116.2075) and east (centre -116.1975), told apart by their pH.
 */
describe("LRU cache over a masked query cell (review M4)", () => {
  const west = row(-116.21, 43.6, MAPPED, { phh2o_0_5cm: 50 });
  const east = row(-116.2, 43.6, MAPPED, { phh2o_0_5cm: 70 });
  const nearWestEdge = { lat: 43.6025, lon: -116.2048 };
  const nearEastEdge = { lat: 43.6025, lon: -116.2002 };

  it("re-ranks cached candidates per point, so each point gets its own nearest centre", async () => {
    vi.mocked(getParquetLatestRelease).mockResolvedValue(published([west, east]));
    const first = await getSoilProperties(nearWestEdge.lat, nearWestEdge.lon);
    expect(first).toMatchObject({ state: "available", properties: { ph: 5 } });
    // A point beside the first, inside the cached bbox: served from the candidates, re-ranked, no read.
    const beside = await getSoilProperties(nearWestEdge.lat, nearWestEdge.lon + 0.0001);
    expect(beside).toMatchObject({ state: "available", properties: { ph: 5 } });
    expect(getParquetLatestRelease).toHaveBeenCalledTimes(1);
    // The same masked cell's east edge: the east neighbour (~220 m), never the cached west one (~590 m).
    const second = await getSoilProperties(nearEastEdge.lat, nearEastEdge.lon);
    expect(second).toMatchObject({ state: "available", properties: { ph: 7 } });
    expect((second as { properties: { distanceM: number } }).properties.distanceM).toBeLessThan(300);
    // Both edges of the cell lie inside the first read's bbox, so this was a re-rank, not a re-read.
    expect(getParquetLatestRelease).toHaveBeenCalledTimes(1);
  });

  it("re-reads when the cached bbox cannot hold every candidate of the new point", async () => {
    vi.mocked(getParquetLatestRelease).mockResolvedValue(published([west, east]));
    expect(await getSoilProperties(nearEastEdge.lat, nearEastEdge.lon)).toMatchObject({ properties: { ph: 7 } });
    // The C2 bbox leaves 0.0075 deg of slack east and north of the origins a point needs, but only 0.0025
    // west and south: a point 0.0046 deg west could need origins the first read never asked for.
    expect(await getSoilProperties(nearWestEdge.lat, nearWestEdge.lon)).toMatchObject({ properties: { ph: 5 } });
    expect(getParquetLatestRelease).toHaveBeenCalledTimes(2);
  });

  it("answers from cached candidates exactly as a fresh read would", async () => {
    vi.mocked(getParquetLatestRelease).mockResolvedValue(published([west, east]));
    await getSoilProperties(nearWestEdge.lat, nearWestEdge.lon);
    const cached = await getSoilProperties(nearWestEdge.lat, nearWestEdge.lon + 0.0001);
    resetSoilPropertiesCacheForTests();
    const fresh = await getSoilProperties(nearWestEdge.lat, nearWestEdge.lon + 0.0001);
    expect(cached).toEqual(fresh);
  });
});
