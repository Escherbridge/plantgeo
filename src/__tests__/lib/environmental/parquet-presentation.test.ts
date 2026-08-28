import { describe, expect, it } from "vitest";
import {
  presentParquetDrought,
  presentParquetVegetation,
  presentParquetWater,
} from "@/lib/environmental/parquet-presentation";
import type {
  ParquetDroughtArea,
  ParquetReaderResult,
  ParquetVegetationWindow,
  ParquetWaterGauge,
} from "@/lib/server/services/parquet-trpc-readers";

function waterRow(overrides: Partial<ParquetWaterGauge>): ParquetWaterGauge {
  return {
    siteNumber: "13172500",
    observedAt: "2026-08-28T12:00:00Z",
    observedDay: "2026-08-28",
    siteName: "Boise River",
    latitude: 43.6,
    longitude: -116.2,
    flowCfs: 500,
    percentile: null,
    condition: "unknown",
    trend: null,
    source: "USGS NWIS",
    geometryLinked: true,
    dataAvailableAt: null,
    ingestedAt: "2026-08-28T12:05:00Z",
    ...overrides,
  };
}

describe("Parquet browser presentation", () => {
  it("keeps named gauges, anonymous coarse cells, and unlocated rows distinct", () => {
    const result: ParquetReaderResult<readonly ParquetWaterGauge[]> = {
      state: "ready",
      requestedDay: "2026-08-28",
      servedDay: "2026-08-28",
      truncated: false,
      data: [
        waterRow({}),
        waterRow({ siteNumber: null, siteName: null, latitude: 43.125, longitude: -116.125 }),
        waterRow({ latitude: null, longitude: null, geometryLinked: false }),
      ],
    };

    const presented = presentParquetWater(result);
    expect(presented.gauges.map((gauge) => gauge.siteNo)).toEqual(["13172500"]);
    expect(presented.cells).toEqual([
      expect.objectContaining({ latitude: 43.125, longitude: -116.125, flowCfs: 500 }),
    ]);
    expect(presented.unlocatedRows).toBe(1);
  });

  it("renders vegetation support as a point rather than inventing a cell polygon", () => {
    const result: ParquetReaderResult<ParquetVegetationWindow> = {
      state: "ready",
      requestedDay: "2026-08-28",
      servedDay: "2026-08-27",
      truncated: false,
      data: {
        firstDay: "2026-07-30",
        lastDay: "2026-08-28",
        days: [],
        observations: [
          {
            cellId: "cell-1",
            gridName: "sentinel-2",
            metricName: "ndvi",
            metricUnit: "index",
            observedDay: "2026-08-27",
            metricValue: 0.61,
            observationChecksum: null,
            dataAvailableAt: "2026-08-28T00:00:00Z",
            releaseCount: 1,
            allowedClientExposure: true,
            longitude: -116.2,
            latitude: 43.6,
          },
        ],
      },
    };

    const feature = presentParquetVegetation(result).features[0];
    expect(feature.geometry.type).toBe("Point");
    expect(feature.properties).toEqual(expect.objectContaining({ ndvi: 0.61 }));
  });

  it("preserves the drought release geometry and category in GeoJSON", () => {
    const area: ParquetDroughtArea = {
      areaId: "d2",
      validDate: "2026-08-25",
      droughtCategory: 2,
      sourceUrl: "https://example.test/usdm",
      ingestedAt: "2026-08-26T00:00:00Z",
      geometry: {
        type: "Polygon",
        coordinates: [
          [
            [-117, 43],
            [-116, 43],
            [-116, 44],
            [-117, 43],
          ],
        ],
      },
    };
    const result: ParquetReaderResult<readonly ParquetDroughtArea[]> = {
      state: "ready",
      requestedDay: "2026-08-28",
      servedDay: "2026-08-25",
      truncated: false,
      data: [area],
    };

    const feature = presentParquetDrought(result).features[0];
    expect(feature.geometry).toEqual(area.geometry);
    expect(feature.properties).toEqual(expect.objectContaining({ DM: 2, validDate: "2026-08-25" }));
  });
});
