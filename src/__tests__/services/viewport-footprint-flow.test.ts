import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The HTTP edge is the only fake: `fetchBoundedJson` answers as the production Parquet plane does,
 * including its bbox rule (`_predicate` in agri-data-service `parquet_ops/warehouse_reader.py`:
 * `cell_longitude BETWEEN west AND east AND cell_latitude BETWEEN south AND north`). Router, reader,
 * decoder and collection adapter all run for real. See src/lib/map/AGENTS.md §viewport-footprint.
 */
vi.mock("@/lib/server/http/bounded-upstream", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/server/http/bounded-upstream")>();
  return { ...actual, providerUrl: vi.fn(), fetchBoundedJson: vi.fn() };
});

import { fetchBoundedJson, providerUrl } from "@/lib/server/http/bounded-upstream";
import type { Context } from "@/lib/server/trpc/init";
import { resetParquetCoverageCacheForTests } from "@/lib/server/services/parquet-plane-client";
import { environmentalRouter } from "@/lib/server/trpc/routers/environmental";
import { compiledRegionSlug, primeServedRegion } from "./served-region-fixture";
import {
  PRECIPITATION_Z0_ROWS,
  PRECIPITATION_Z13_ROWS,
  PRECIPITATION_Z9_ROWS,
  SOIL_MOISTURE_SURFACE_Z13_ROWS,
  SOIL_MOISTURE_SURFACE_Z5_ROWS,
} from "./viewport-footprint-plane-fixture";

const mockedProviderUrl = vi.mocked(providerUrl);
const mockedFetch = vi.mocked(fetchBoundedJson);

/** What the production warehouse holds, by layer and rung. */
const WAREHOUSE: Record<string, Partial<Record<string, readonly Record<string, unknown>[]>>> = {
  "climate-field-precipitation": {
    "13": PRECIPITATION_Z13_ROWS,
    "9": PRECIPITATION_Z9_ROWS,
    "0": PRECIPITATION_Z0_ROWS,
  },
  "soil-field-moisture-0-7cm": {
    "13": SOIL_MOISTURE_SURFACE_Z13_ROWS,
    "5": SOIL_MOISTURE_SURFACE_Z5_ROWS,
  },
};

/** Drawn cells in a stable order, so an extra (untrimmed) neighbour fails an exact match. */
function sortedCells(cells: readonly (readonly number[])[]): number[][] {
  return cells
    .map((cell) => [...cell])
    .sort((left, right) => left[0] - right[0] || left[1] - right[1]);
}

/** The plane's day route: rows whose STORED coordinate lies inside the requested bbox. */
function fakePlaneDay(url: URL) {
  const params = url.searchParams;
  const day = params.get("day");
  const [west, south, east, north] = (params.get("bbox") ?? "").split(",").map(Number);
  const stored = WAREHOUSE[params.get("layer") ?? ""]?.[params.get("zoom") ?? ""] ?? [];
  const rows = stored.filter((row) => {
    const longitude = row.cell_longitude as number;
    const latitude = row.cell_latitude as number;
    return longitude >= west && longitude <= east && latitude >= south && latitude <= north;
  });
  return { state: "published", requested_day: day, served_day: day, rows, truncated: false };
}

const caller = environmentalRouter.createCaller({ db: {}, session: null } as unknown as Context);

/** The day every captured row was observed on. */
const DAY = "2026-09-20";

/** The Boise viewport production answered `not_published` for on 2026-10-04 at every rung. */
const NARROW_BOISE_VIEWPORT = "-116.4,43.4,-116.0,43.8";

/** [west, south, east, north] of a feature's single ring. */
function ringBounds(feature: GeoJSON.Feature): [number, number, number, number] {
  if (feature.geometry.type !== "Polygon") throw new Error("expected a cell polygon");
  const ring = feature.geometry.coordinates[0];
  const longitudes = ring.map(([longitude]) => longitude);
  const latitudes = ring.map(([, latitude]) => latitude);
  return [
    Math.min(...longitudes),
    Math.min(...latitudes),
    Math.max(...longitudes),
    Math.max(...latitudes),
  ];
}

beforeEach(async () => {
  resetParquetCoverageCacheForTests();
  mockedProviderUrl.mockReset();
  mockedFetch.mockReset();
  mockedProviderUrl.mockImplementation(() => new URL("http://agri.internal:8000"));
  await primeServedRegion(mockedFetch, compiledRegionSlug());
  mockedFetch.mockImplementation(async (url) => fakePlaneDay(url as URL));
});

describe("a climate viewport narrower than one lattice cell", () => {
  it.each([
    // [map zoom, rung, cells drawn as [west, south, east, north]]
    [14, 13, [[-116.5, 42.5, -115.5, 43.5], [-116.5, 43.5, -115.5, 44.5]]],
    [10, 9, [[-116.5, 42.5, -115.5, 43.5], [-116.5, 43.5, -115.5, 44.5]]],
    [2, 0, [[-120, 40, -115, 45]]],
  ] as const)(
    "draws the cells under it at map zoom %s (rung z%s)",
    async (zoom, zoomTier, expectedCells) => {
      const collection = await caller.getClimateField({
        bbox: NARROW_BOISE_VIEWPORT,
        date: DAY,
        zoom,
        signal: "precipitation",
        renderForm: "field",
      });

      expect(collection).toMatchObject({
        availability: "published",
        reason: null,
        zoomTier,
        observedDay: DAY,
        cellCount: expectedCells.length,
        // Numerator and denominator on the same footprint rule.
        latticeCellCount: expectedCells.length,
      });
      // EXACT set: the padded request also returns the -117 and -115 columns (and, at z0, a ring
      // of five-degree cells), none of whose squares meet this viewport, so all are trimmed.
      expect(sortedCells(collection.features.map(ringBounds))).toEqual(sortedCells(expectedCells));
      expect(collection.features.map((feature) => feature.properties?.value)).toEqual(
        expectedCells.map(() => 0)
      );
    }
  );

  it("trims a cell the padded request returned but whose square misses the viewport", async () => {
    // Inside the -116/44 cell only. The padded request also returns -116/43, whose square
    // [42.5, 43.5] ends below this viewport's south edge, so it must not be drawn or counted.
    const collection = await caller.getClimateField({
      bbox: "-116.4,43.6,-116.0,43.8",
      date: DAY,
      zoom: 10,
      signal: "precipitation",
      renderForm: "field",
    });

    expect(collection).toMatchObject({ availability: "published", cellCount: 1, latticeCellCount: 1 });
    expect(collection.features.map(ringBounds)).toEqual([[-116.5, 43.5, -115.5, 44.5]]);
  });
});

describe("a soil viewport narrower than one quarter-degree cell", () => {
  it("draws the ERA5-Land cell under a street-zoom viewport", async () => {
    // The exact street-zoom probe production answered `not_published` for on 2026-10-04.
    const collection = await caller.getSoilField({
      bbox: "-116.24,43.6,-116.18,43.64",
      date: DAY,
      zoom: 14,
    });

    expect(collection).toMatchObject({ availability: "published", observedDay: DAY, cellCount: 1 });
    expect(collection.features.map(ringBounds)).toEqual([[-116.25, 43.5, -116, 43.75]]);
    expect(collection.features[0].properties?.value).toBe(0.114);
  });

  it("fetches the z5 cell whose floored coordinate sits west of its own square", async () => {
    // At z5 the writer floors each quarter-degree CENTRE onto the 0.2 grid, so the cell
    // [-116.75, -116.5] (centre -116.625) is stored at -116.8 -- outside the cell. A one-cell pad
    // from this west edge stops at -116.77 and never fetches it.
    const collection = await caller.getSoilField({
      bbox: "-116.52,43.55,-116.40,43.70",
      date: DAY,
      zoom: 6,
    });

    expect(collection).toMatchObject({ availability: "published", zoomTier: 5, cellCount: 2 });
    const valuesByCell = collection.features
      .map((feature) => [...ringBounds(feature), feature.properties?.value])
      .sort((left, right) => (left[0] as number) - (right[0] as number));
    expect(valuesByCell).toEqual([
      [-116.75, 43.5, -116.5, 43.75, 0.133],
      [-116.5, 43.5, -116.25, 43.75, 0.128],
    ]);
  });
});
