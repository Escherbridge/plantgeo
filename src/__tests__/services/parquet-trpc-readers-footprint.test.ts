import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The HTTP edge is the only fake: the Parquet plane client is mocked at the `getParquetLayerDayWindow`
 * boundary, and the real reader (footprint padding, decode, `meetsViewport` trim) runs for every row.
 * See src/lib/map/AGENTS.md §viewport-footprint and services/AGENTS.md's note on vegetation/fire-detections
 * moving onto `cellFootprintViewport`.
 */
vi.mock("@/lib/server/services/parquet-plane-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/server/services/parquet-plane-client")>();
  return { ...actual, getParquetLayerDayWindow: vi.fn() };
});

import { getParquetLayerDayWindow } from "@/lib/server/services/parquet-plane-client";
import { getParquetFireDetections } from "@/lib/server/services/parquet-trpc-readers/fire-detections";
import { getParquetVegetation } from "@/lib/server/services/parquet-trpc-readers/vegetation";

const mockedWindow = vi.mocked(getParquetLayerDayWindow);

function published(day: string, rows: readonly Record<string, unknown>[]) {
  return { state: "published" as const, requestedDay: day, servedDay: day, rows, truncated: false };
}

beforeEach(() => {
  mockedWindow.mockReset();
});

describe("vegetation viewport footprint", () => {
  const DAY = "2026-09-20";
  // Quarter-degree cells, centroid a half step off the 0.25 grid (zoom-tiers.ts LANE_BASE_LATTICES
  // "vegetation"): centre -116.125 -> cell square [-116.25, -116.0] x [42.75, 43.0].
  const CELL_OUTSIDE_POINT_INSIDE_SQUARE = { cell_longitude: -116.125, cell_latitude: 42.875 };
  // centre -117.125 -> cell square [-117.25, -117.0], which never touches the viewport below.
  const CELL_WHOLLY_OUTSIDE = { cell_longitude: -117.125, cell_latitude: 42.875 };
  // West of both cells' centroids, so each stored point falls outside it while the first cell's
  // square still overlaps it.
  const VIEWPORT = "-116.05,42.95,-115.95,43.05";

  function vegetationRow(overrides: Record<string, unknown>) {
    return {
      cell_id: null,
      grid_name: "sentinel-2-quarter-degree",
      metric_name: "ndvi",
      metric_unit: "1",
      observed_day: DAY,
      metric_value: 0.5,
      observation_checksum: null,
      data_available_at: `${DAY}T18:30:00Z`,
      release_count: 1,
      allowed_client_exposure: true,
      ...overrides,
    };
  }

  it("returns a cell whose stored centroid sits outside the viewport but whose square overlaps it, and drops one wholly outside", async () => {
    mockedWindow.mockResolvedValue([
      published(DAY, [
        vegetationRow(CELL_OUTSIDE_POINT_INSIDE_SQUARE),
        vegetationRow(CELL_WHOLLY_OUTSIDE),
      ]),
    ]);

    const result = await getParquetVegetation({ bbox: VIEWPORT, date: DAY, mapZoom: 14 });

    if (result.state !== "ready") throw new Error(`expected ready, got ${result.state}`);
    expect(result.data.observations).toHaveLength(1);
    expect(result.data.observations[0]).toMatchObject({ longitude: -116.125, latitude: 42.875 });
  });
});

describe("fire-detections viewport footprint", () => {
  const DAY = "2026-09-20";

  function fireRow(overrides: Record<string, unknown>) {
    return {
      cell_longitude: -116.1,
      cell_latitude: 42.9,
      observed_day: DAY,
      detection_count: 4,
      frp_sum: 18.5,
      frp_observation_count: 3,
      high_confidence_detection_count: 2,
      newest_observed_at: `${DAY}T22:15:00Z`,
      ...overrides,
    };
  }

  // FIRMS stores the cell ORIGIN (south-west corner), floor-snapped to a 0.005-degree grid
  // (zoom-tiers.ts LANE_BASE_LATTICES "fire-detections"): origin (-116.1, 42.9) is a real cell
  // whose square is [-116.1, -116.095] x [42.9, 42.905] -- it has a base lattice, so it is a real
  // cell lane, not raw points, and gets the same footprint treatment as vegetation/soil-field.
  const CELL_OUTSIDE_POINT_INSIDE_SQUARE = { cell_longitude: -116.1, cell_latitude: 42.9 };
  // Origin (-117.1, 42.9) -> square [-117.1, -117.095], which never reaches the viewport below.
  const CELL_WHOLLY_OUTSIDE = { cell_longitude: -117.1, cell_latitude: 42.9 };
  // Both axes exclude each stored point (west of the first cell's longitude, south of its
  // latitude) while its square still overlaps -- the worst case the pad margin must cover.
  const VIEWPORT = "-116.098,42.903,-115.9,43.0";

  it("returns a cell whose stored origin sits outside the viewport but whose square overlaps it, and drops one wholly outside", async () => {
    mockedWindow.mockResolvedValue([
      published(DAY, [fireRow(CELL_OUTSIDE_POINT_INSIDE_SQUARE), fireRow(CELL_WHOLLY_OUTSIDE)]),
    ]);

    const result = await getParquetFireDetections({ bbox: VIEWPORT, date: DAY, mapZoom: 14 });

    if (result.state !== "ready") throw new Error(`expected ready, got ${result.state}`);
    expect(result.data.cells).toHaveLength(1);
    expect(result.data.cells[0]).toMatchObject({ longitude: -116.1, latitude: 42.9 });
  });

  it("skips footprint trimming entirely when no viewport was requested", async () => {
    mockedWindow.mockResolvedValue([
      published(DAY, [fireRow(CELL_OUTSIDE_POINT_INSIDE_SQUARE), fireRow(CELL_WHOLLY_OUTSIDE)]),
    ]);

    const result = await getParquetFireDetections({ date: DAY, mapZoom: 14 });

    expect(mockedWindow).toHaveBeenCalledWith(
      expect.not.objectContaining({ bbox: expect.anything() })
    );
    if (result.state !== "ready") throw new Error(`expected ready, got ${result.state}`);
    expect(result.data.cells).toHaveLength(2);
  });
});
