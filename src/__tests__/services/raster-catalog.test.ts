import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ execute: vi.fn() }));

vi.mock("@/lib/server/db", () => ({
  db: { execute: mocks.execute },
}));

import {
  getPublishedSoilRasters,
  publicRasterArchiveUrl,
} from "@/lib/server/services/raster-catalog";

const previousBaseUrl = process.env.RASTER_TILES_BASE_URL;

beforeEach(() => {
  mocks.execute.mockReset();
  process.env.RASTER_TILES_BASE_URL = "https://tiles.example.test/base/";
});

afterEach(() => {
  if (previousBaseUrl === undefined) delete process.env.RASTER_TILES_BASE_URL;
  else process.env.RASTER_TILES_BASE_URL = previousBaseUrl;
});

describe("published SoilGrids raster catalogue", () => {
  it("maps a live PMTiles catalogue row to the client contract", async () => {
    mocks.execute.mockResolvedValue([
      {
        property: "soc",
        unit: "g/kg",
        scale_divisor: 10,
        value_min: 5.7,
        value_max: 462,
        color_ramp: [{ value: 5.7, color: "#fff7ec" }, { value: 60, color: "#7f0000" }],
        object_key: "raster/soil/soilgrids-v2.0/tiles/soc_0-5cm_mean.pmtiles",
        min_zoom: 0,
        max_zoom: 10,
        attribution: "ISRIC SoilGrids",
        source_name: "SoilGrids",
        source_release: "2.0",
        license_name: "CC-BY 4.0",
        bbox_west: -125,
        bbox_south: 42,
        bbox_east: -111,
        bbox_north: 49,
      },
    ]);

    await expect(getPublishedSoilRasters()).resolves.toEqual([
      expect.objectContaining({
        property: "soc",
        archiveUrl:
          "https://tiles.example.test/base/raster/soil/soilgrids-v2.0/tiles/soc_0-5cm_mean.pmtiles",
        colorRamp: [{ value: 5.7, color: "#fff7ec" }, { value: 60, color: "#7f0000" }],
        bounds: [-125, 42, -111, 49],
      }),
    ]);
  });

  it("rejects catalogue keys that can escape the reviewed public origin", () => {
    expect(() => publicRasterArchiveUrl("../private.pmtiles")).toThrow(/Unsafe raster object key/);
    expect(() => publicRasterArchiveUrl("https://evil.test/archive.pmtiles")).toThrow();
  });

  it("rejects an unsafe configured public origin", () => {
    process.env.RASTER_TILES_BASE_URL = "file:///private/tiles/";
    expect(() => publicRasterArchiveUrl("raster/soil/soc.pmtiles")).toThrow(/HTTP or HTTPS/);

    process.env.RASTER_TILES_BASE_URL = "https://user:secret@tiles.example.test/";
    expect(() => publicRasterArchiveUrl("raster/soil/soc.pmtiles")).toThrow(/credential-free/);
  });

  it("rejects unknown soil properties and malformed colour ramps", async () => {
    const validRow = {
      property: "soc",
      unit: "g/kg",
      scale_divisor: 10,
      value_min: 5,
      value_max: 460,
      color_ramp: [{ value: 5, color: "#fff7ec" }],
      object_key: "raster/soil/soc.pmtiles",
      min_zoom: 0,
      max_zoom: 10,
      attribution: "ISRIC SoilGrids",
      source_name: "SoilGrids",
      source_release: "2.0",
      license_name: "CC-BY 4.0",
      bbox_west: -125,
      bbox_south: 42,
      bbox_east: -111,
      bbox_north: 49,
    };

    mocks.execute.mockResolvedValueOnce([{ ...validRow, property: "clay" }]);
    await expect(getPublishedSoilRasters()).rejects.toThrow(/Unknown published SoilGrids property/);

    mocks.execute.mockResolvedValueOnce([{ ...validRow, color_ramp: [{ value: "5", color: "red" }] }]);
    await expect(getPublishedSoilRasters()).rejects.toThrow(/Invalid color ramp stop/);
  });
});
