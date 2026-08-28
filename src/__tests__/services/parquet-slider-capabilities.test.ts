import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getParquetWarehouseCoverage: vi.fn(),
  getSliderCapabilities: vi.fn(),
}));

vi.mock("@/lib/server/services/parquet-plane-client", () => ({
  getParquetWarehouseCoverage: mocks.getParquetWarehouseCoverage,
}));

vi.mock("@/lib/server/services/environmental-read-model", () => ({
  getSliderCapabilities: mocks.getSliderCapabilities,
}));

import { getParquetSliderCapabilities } from "@/lib/server/services/parquet-slider-capabilities";

const baseCapability = (layerName: string) => ({
  layerName,
  temporalKind: "daily_series" as const,
  forecastHorizonDays: 0,
  forecastVariants: [],
  earliestObservedDate: "2026-08-01",
  latestObservedDate: "2026-08-20",
  coverageGaps: [],
  thinRanges: [],
  describedFromDay: null,
  coverageGapsTruncated: false,
  coverageGapsDescribedFromDay: null,
  thinRangesTruncated: false,
  thinRangesDescribedFromDay: null,
  earliestObservedDateRule: "full_history" as const,
  earliestRecordedObservationDate: "2026-08-01",
  earliestContinuousObservationDate: "2026-08-01",
  latestRecordedObservationDate: "2026-08-20",
  observedDayCount: 20,
  excludedObservedDayCount: 0,
  gapExcludedObservedDayCount: 0,
  densityExcludedObservedDayCount: 0,
  minimumDailyObservationCount: 1,
});

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getParquetWarehouseCoverage.mockResolvedValue({
    generatedAt: "2026-08-28T12:00:00Z",
    lanes: [
      "drought",
      "fire-detections",
      "signal",
      "vegetation",
      "water-gauges",
      "weather-observations",
    ].map((layer) => ({
      layer,
      nature: layer === "drought" ? "release_series" : "daily_series",
      kind: "observed",
      earliestDay: "2026-08-01",
      latestDay: "2026-08-20",
      gapRanges: [],
      governedAbsenceRanges: [],
    })),
  });
  mocks.getSliderCapabilities.mockResolvedValue({
    serverCurrentDate: "2026-08-28",
    futureAxisDays: 30,
    streamsUnavailable: false,
    layers: [
      baseCapability("water-gauges"),
      baseCapability("weather-observations"),
      baseCapability("drought-areas"),
      baseCapability("soil-field-moisture"),
      baseCapability("fire-perimeters"),
      baseCapability("interventions"),
    ],
  });
});

describe("getParquetSliderCapabilities", () => {
  it("keeps unrelated PostgreSQL rows but never advertises unproved Parquet rungs or products", async () => {
    const result = await getParquetSliderCapabilities();

    expect(result.layers.map((layer) => layer.layerName)).toEqual([
      "fire-perimeters",
      "interventions",
    ]);
    expect(result.parquetCoverageGeneratedAt).toBe("2026-08-28T12:00:00Z");
    expect(result.withheldParquetCapabilities).toEqual(
      expect.arrayContaining([
        {
          layerName: "water-gauges",
          parquetLane: "water-gauges",
          reason: "per_rung_coverage_not_reported",
        },
        {
          layerName: "soil-field-moisture",
          parquetLane: "signal",
          reason: "signal_product_coverage_not_reported",
        },
      ])
    );
  });

  it("propagates a Parquet census fault instead of serving PostgreSQL fallback rows", async () => {
    const fault = new Error("coverage unavailable");
    mocks.getParquetWarehouseCoverage.mockRejectedValue(fault);

    await expect(getParquetSliderCapabilities()).rejects.toBe(fault);
    expect(mocks.getSliderCapabilities).not.toHaveBeenCalled();
  });

  it("marks a missing Parquet lane as unregistered rather than borrowing its PostgreSQL row", async () => {
    mocks.getParquetWarehouseCoverage.mockResolvedValue({
      generatedAt: "2026-08-28T12:00:00Z",
      lanes: [],
    });

    const result = await getParquetSliderCapabilities();

    expect(result.layers.some((layer) => layer.layerName === "water-gauges")).toBe(false);
    expect(result.withheldParquetCapabilities).toContainEqual({
      layerName: "water-gauges",
      parquetLane: "water-gauges",
      reason: "lane_not_registered",
    });
  });

  it("does not let the retired PostgreSQL stream scan remount withheld Parquet sliders", async () => {
    mocks.getSliderCapabilities.mockResolvedValue({
      serverCurrentDate: "2026-08-28",
      futureAxisDays: 30,
      streamsUnavailable: true,
      layers: [baseCapability("fire-perimeters")],
    });

    const result = await getParquetSliderCapabilities();

    expect(result.streamsUnavailable).toBe(false);
    expect(result.layers.map((layer) => layer.layerName)).toEqual(["fire-perimeters"]);
  });

  it("distinguishes a registered lane that has never written a day", async () => {
    mocks.getParquetWarehouseCoverage.mockResolvedValue({
      generatedAt: "2026-08-28T12:00:00Z",
      lanes: [
        {
          layer: "water-gauges",
          nature: "daily_series",
          kind: "observed",
          earliestDay: null,
          latestDay: null,
          gapRanges: [],
          governedAbsenceRanges: [],
        },
      ],
    });

    const result = await getParquetSliderCapabilities();

    expect(result.withheldParquetCapabilities).toContainEqual({
      layerName: "water-gauges",
      parquetLane: "water-gauges",
      reason: "lane_never_written",
    });
  });
});
