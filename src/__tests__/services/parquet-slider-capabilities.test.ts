import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ZoomTier } from "@/lib/map/zoom-tiers";

const mocks = vi.hoisted(() => ({
  getParquetWarehouseCoverage: vi.fn(),
  getSliderCapabilities: vi.fn(),
}));

vi.mock("@/lib/server/services/parquet-plane-client", () => ({
  getParquetWarehouseCoverage: mocks.getParquetWarehouseCoverage,
}));

vi.mock("@/lib/server/services/environmental-read-model", () => ({
  getSliderCapabilities: mocks.getSliderCapabilities,
  MAX_REPORTED_DAY_RANGES: 800,
}));

import {
  getParquetSliderCapabilities,
  PARQUET_CAPABILITY_CONTRACTS,
  PARQUET_CAPABILITY_LANES,
} from "@/lib/server/services/parquet-slider-capabilities";

const ZOOM_TIERS = [0, 5, 9, 13] as const satisfies readonly ZoomTier[];
const FIRST_DAY = "2022-08-01";
const LAST_DAY = "2026-08-20";

const baseCapability = (layerName: string) => ({
  layerName,
  temporalKind: "daily_series" as const,
  forecastHorizonDays: 0,
  forecastVariants: [],
  earliestObservedDate: "1999-01-01",
  latestObservedDate: "1999-01-02",
  coverageGaps: [],
  thinRanges: [],
  describedFromDay: null,
  coverageGapsTruncated: false,
  coverageGapsDescribedFromDay: null,
  thinRangesTruncated: false,
  thinRangesDescribedFromDay: null,
  earliestObservedDateRule: "full_history" as const,
  earliestRecordedObservationDate: "1999-01-01",
  earliestContinuousObservationDate: "1999-01-01",
  latestRecordedObservationDate: "1999-01-02",
  observedDayCount: 2,
  excludedObservedDayCount: 0,
  gapExcludedObservedDayCount: 0,
  densityExcludedObservedDayCount: 0,
  minimumDailyObservationCount: 1,
});

type CoverageRow = {
  layer: string;
  nature: "daily_series" | "release_series" | "static_lookup";
  kind: "observed";
  zoomTier: ZoomTier;
  earliestDay: string | null;
  latestDay: string | null;
  publishedRanges: Array<{ from: string; to: string }>;
  gapRanges: Array<{ from: string; to: string }>;
  governedAbsenceRanges: Array<{ from: string; to: string }>;
};

function natureFor(layer: string): CoverageRow["nature"] {
  if (layer === "drought" || layer === "burn-severity") return "release_series";
  if (["watersheds", "soil-survey", "evacuation-zones"].includes(layer)) {
    return "static_lookup";
  }
  return "daily_series";
}

function completeCoverage(): CoverageRow[] {
  return PARQUET_CAPABILITY_LANES.flatMap((layer) =>
    ZOOM_TIERS.map((zoomTier) => ({
      layer,
      nature: natureFor(layer),
      kind: "observed" as const,
      zoomTier,
      earliestDay: FIRST_DAY,
      latestDay: LAST_DAY,
      publishedRanges: [{ from: FIRST_DAY, to: LAST_DAY }],
      gapRanges: [],
      governedAbsenceRanges: [],
    }))
  );
}

function setCoverage(lanes: CoverageRow[]): void {
  mocks.getParquetWarehouseCoverage.mockResolvedValue({
    generatedAt: "2026-08-28T12:00:00Z",
    evaluatedThroughDay: "2026-08-28",
    lanes,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  setCoverage(completeCoverage());
  mocks.getSliderCapabilities.mockResolvedValue({
    serverCurrentDate: "2026-08-28",
    futureAxisDays: 30,
    streamsUnavailable: false,
    layers: [
      ...PARQUET_CAPABILITY_CONTRACTS.map((contract) => baseCapability(contract.layerName)),
      baseCapability("interventions"),
    ],
  });
});

describe("getParquetSliderCapabilities", () => {
  it("owns every catalogue row but publishes only end-to-end Parquet readers with exact evidence", async () => {
    const expectedCatalogue = [
      "drought-areas",
      "fire-detections",
      "fire-perimeters",
      "water-gauges",
      "weather-observations",
      "sensors",
      "watersheds",
      "vegetation",
      "soil-survey",
      "evacuation-zones",
      "burn-severity",
      "soil-field-moisture",
      "soil-field-temperature",
      "soil-field-vpd",
      "climate-field-air-temperature",
      "climate-field-dew-point",
      "climate-field-precipitation",
      "climate-field-relative-humidity",
      "climate-field-shortwave-radiation",
      "climate-field-wind-speed",
      "climate-field-soil-wetness-surface",
      "climate-field-soil-wetness-root-zone",
      "climate-field-soil-wetness-profile",
    ];
    expect(PARQUET_CAPABILITY_CONTRACTS.map((contract) => contract.layerName)).toEqual(
      expectedCatalogue
    );
    expect(PARQUET_CAPABILITY_LANES).toEqual([
      "drought",
      "fire-detections",
      "fire-perimeters",
      "water-gauges",
      "weather-observations",
      "sensors",
      "watersheds",
      "vegetation",
      "soil-survey",
      "evacuation-zones",
      "burn-severity",
      "soil-field-moisture-0-7cm",
      "soil-field-moisture-7-28cm",
      "soil-field-moisture-28-100cm",
      "soil-temperature-0-to-7cm",
      "soil-temperature-7-to-28cm",
      "soil-temperature-28-to-100cm",
      "soil-temperature-100-to-255cm",
      "soil-field-vpd",
      "climate-field-air-temperature-mean",
      "climate-field-air-temperature-max",
      "climate-field-air-temperature-min",
      "climate-field-dew-point",
      "climate-field-precipitation",
      "climate-field-relative-humidity",
      "climate-field-shortwave-radiation",
      "climate-field-wind-speed",
      "soil-wetness-surface",
      "soil-wetness-root-zone",
      "soil-wetness-profile",
    ]);
    expect(
      PARQUET_CAPABILITY_CONTRACTS.find(
        (contract) => contract.layerName === "climate-field-air-temperature"
      )?.parquetLanes
    ).toEqual([
      "climate-field-air-temperature-mean",
      "climate-field-air-temperature-max",
      "climate-field-air-temperature-min",
    ]);
    expect(
      PARQUET_CAPABILITY_CONTRACTS.find(
        (contract) => contract.layerName === "soil-field-temperature"
      )?.parquetLanes
    ).toHaveLength(4);

    const result = await getParquetSliderCapabilities();

    const parquetReaders = PARQUET_CAPABILITY_CONTRACTS.filter(
      (contract) => contract.servingReader === "parquet"
    ).map((contract) => contract.layerName);
    const nonParquetReaders = PARQUET_CAPABILITY_CONTRACTS.filter(
      (contract) => contract.servingReader !== "parquet"
    ).map((contract) => contract.layerName);
    expect(result.layers.map((layer) => layer.layerName)).toEqual([
      "interventions",
      ...parquetReaders,
    ]);
    expect(
      result.withheldParquetCapabilities
        .filter((entry) => entry.reason === "reader_not_parquet")
        .map((entry) => entry.layerName)
    ).toEqual(nonParquetReaders);
    expect(result.withheldParquetCapabilities).toContainEqual({
      layerName: "fire-perimeters",
      parquetLanes: ["fire-perimeters"],
      reason: "reader_not_parquet",
      missingEvidence: [],
    });
    expect(result.parquetCoverageGeneratedAt).toBe("2026-08-28T12:00:00Z");
    expect(result.parquetCoverageEvaluatedThroughDay).toBe("2026-08-28");
    for (const layerName of parquetReaders) {
      expect(result.layers.find((layer) => layer.layerName === layerName)).toMatchObject({
        earliestObservedDate: FIRST_DAY,
        latestObservedDate: LAST_DAY,
        earliestObservedDateRule: "warehouse_coverage",
        minimumDailyObservationCount: null,
      });
    }
  });

  it("reports an exact missing depth/rung before the separate reader-ownership gate", async () => {
    setCoverage(
      completeCoverage().filter(
        (entry) => !(entry.layer === "soil-field-moisture-7-28cm" && entry.zoomTier === 5)
      )
    );

    const result = await getParquetSliderCapabilities();

    expect(result.layers.some((layer) => layer.layerName === "soil-field-moisture")).toBe(false);
    expect(result.layers.some((layer) => layer.layerName === "soil-field-temperature")).toBe(false);
    expect(result.withheldParquetCapabilities).toContainEqual({
      layerName: "soil-field-moisture",
      parquetLanes: [
        "soil-field-moisture-0-7cm",
        "soil-field-moisture-7-28cm",
        "soil-field-moisture-28-100cm",
      ],
      reason: "rung_not_reported",
      missingEvidence: [{ parquetLane: "soil-field-moisture-7-28cm", zoomTier: 5 }],
    });
    expect(result.withheldParquetCapabilities).toContainEqual({
      layerName: "soil-field-temperature",
      parquetLanes: [
        "soil-temperature-0-to-7cm",
        "soil-temperature-7-to-28cm",
        "soil-temperature-28-to-100cm",
        "soil-temperature-100-to-255cm",
      ],
      reason: "reader_not_parquet",
      missingEvidence: [],
    });
  });

  it("distinguishes a schema-registered product lane that has never written any rung", async () => {
    setCoverage(
      completeCoverage().map((entry) =>
        entry.layer === "climate-field-precipitation"
          ? { ...entry, earliestDay: null, latestDay: null, publishedRanges: [] }
          : entry
      )
    );

    const result = await getParquetSliderCapabilities();

    expect(result.withheldParquetCapabilities).toContainEqual({
      layerName: "climate-field-precipitation",
      parquetLanes: ["climate-field-precipitation"],
      reason: "lane_never_written",
      missingEvidence: [{ parquetLane: "climate-field-precipitation", zoomTier: null }],
    });
  });

  it.each([
    {
      name: "empty",
      mutate: (entry: CoverageRow): CoverageRow => ({
        ...entry,
        earliestDay: null,
        latestDay: null,
        publishedRanges: [],
      }),
      reason: "rung_never_written",
    },
    {
      name: "half-bounded",
      mutate: (entry: CoverageRow): CoverageRow => ({ ...entry, latestDay: null }),
      reason: "invalid_rung_bounds",
    },
  ] as const)("withholds a direct lane whose z0 evidence is $name", async ({ mutate, reason }) => {
    setCoverage(
      completeCoverage().map((entry) =>
        entry.layer === "water-gauges" && entry.zoomTier === 0 ? mutate(entry) : entry
      )
    );

    const result = await getParquetSliderCapabilities();

    expect(result.withheldParquetCapabilities).toContainEqual({
      layerName: "water-gauges",
      parquetLanes: ["water-gauges"],
      reason,
      missingEvidence: [{ parquetLane: "water-gauges", zoomTier: 0 }],
    });
  });

  it("rejects duplicate rung evidence instead of choosing one by array order", async () => {
    const lanes = completeCoverage();
    const duplicate = lanes.find(
      (entry) => entry.layer === "fire-perimeters" && entry.zoomTier === 9
    )!;
    setCoverage([...lanes, { ...duplicate }]);

    const result = await getParquetSliderCapabilities();

    expect(result.withheldParquetCapabilities).toContainEqual({
      layerName: "fire-perimeters",
      parquetLanes: ["fire-perimeters"],
      reason: "rung_not_reported",
      missingEvidence: [{ parquetLane: "fire-perimeters", zoomTier: 9 }],
    });
  });

  it("withholds a physical lane whose nature differs from its reader contract", async () => {
    setCoverage(
      completeCoverage().map((entry) =>
        entry.layer === "water-gauges" && entry.zoomTier === 9
          ? { ...entry, nature: "release_series" }
          : entry
      )
    );

    const result = await getParquetSliderCapabilities();

    expect(result.withheldParquetCapabilities).toContainEqual({
      layerName: "water-gauges",
      parquetLanes: ["water-gauges"],
      reason: "lane_nature_mismatch",
      missingEvidence: [{ parquetLane: "water-gauges", zoomTier: 9 }],
    });
  });

  it("withholds unregistered dew and NASA products before the reader-ownership gate", async () => {
    const unregistered = new Set([
      "climate-field-dew-point",
      "soil-wetness-surface",
      "soil-wetness-root-zone",
      "soil-wetness-profile",
    ]);
    setCoverage(completeCoverage().filter((entry) => !unregistered.has(entry.layer)));

    const result = await getParquetSliderCapabilities();

    expect(
      result.withheldParquetCapabilities
        .filter((entry) => entry.reason === "lane_not_registered")
        .map((entry) => entry.layerName)
    ).toEqual([
      "climate-field-dew-point",
      "climate-field-soil-wetness-surface",
      "climate-field-soil-wetness-root-zone",
      "climate-field-soil-wetness-profile",
    ]);
    expect(result.layers.some((layer) => layer.layerName === "climate-field-precipitation")).toBe(false);
    expect(result.withheldParquetCapabilities).toContainEqual({
      layerName: "climate-field-precipitation",
      parquetLanes: ["climate-field-precipitation"],
      reason: "reader_not_parquet",
      missingEvidence: [],
    });
  });

  it("withholds a capability when product/rung bounds overlap but no published day does", async () => {
    setCoverage(
      completeCoverage().map((entry) => {
        if (!entry.layer.startsWith("climate-field-air-temperature-")) return entry;
        return {
          ...entry,
          publishedRanges: [
            entry.layer.endsWith("-mean")
              ? { from: FIRST_DAY, to: "2022-08-05" }
              : { from: "2022-08-10", to: LAST_DAY },
          ],
        };
      })
    );

    const result = await getParquetSliderCapabilities();

    expect(result.withheldParquetCapabilities).toContainEqual({
      layerName: "climate-field-air-temperature",
      parquetLanes: [
        "climate-field-air-temperature-mean",
        "climate-field-air-temperature-max",
        "climate-field-air-temperature-min",
      ],
      reason: "no_common_readable_history",
      missingEvidence: [],
    });
  });

  it("keeps a proved trailing Parquet gap visible through the server current day", async () => {
    setCoverage(
      completeCoverage().map((entry) =>
        entry.layer === "water-gauges" && entry.zoomTier === 5
          ? {
              ...entry,
              gapRanges: [{ from: "2026-08-21", to: "2026-08-28" }],
            }
          : entry
      )
    );

    const result = await getParquetSliderCapabilities();

    expect(result.layers.find((layer) => layer.layerName === "water-gauges")?.coverageGaps).toEqual(
      [{ from: "2026-08-21", to: "2026-08-28" }]
    );
  });

  it("keeps a governed absence out of warehouse gaps while breaking published continuity", async () => {
    setCoverage(
      completeCoverage().map((entry) =>
        entry.layer === "vegetation" && entry.zoomTier === 13
          ? {
              ...entry,
              publishedRanges: [
                { from: FIRST_DAY, to: "2026-08-09" },
                { from: "2026-08-11", to: LAST_DAY },
              ],
              governedAbsenceRanges: [{ from: "2026-08-10", to: "2026-08-10" }],
            }
          : entry
      )
    );

    const result = await getParquetSliderCapabilities();
    const vegetation = result.layers.find((layer) => layer.layerName === "vegetation");

    expect(vegetation?.coverageGaps).toEqual([]);
    expect(vegetation?.earliestContinuousObservationDate).toBe("2026-08-11");
  });

  it("propagates a Parquet census fault before reading PostgreSQL fallback rows", async () => {
    const fault = new Error("coverage unavailable");
    mocks.getParquetWarehouseCoverage.mockRejectedValue(fault);

    await expect(getParquetSliderCapabilities()).rejects.toBe(fault);
    expect(mocks.getSliderCapabilities).not.toHaveBeenCalled();
  });

  it("withholds every owned row when coverage was evaluated before the server current day", async () => {
    mocks.getParquetWarehouseCoverage.mockResolvedValue({
      generatedAt: "2026-08-27T23:59:59Z",
      evaluatedThroughDay: "2026-08-27",
      lanes: completeCoverage(),
    });

    const result = await getParquetSliderCapabilities();

    expect(result.layers.map((layer) => layer.layerName)).toEqual(["interventions"]);
    expect(result.withheldParquetCapabilities).toHaveLength(PARQUET_CAPABILITY_CONTRACTS.length);
    expect(result.withheldParquetCapabilities.every((entry) => entry.reason === "coverage_not_current")).toBe(
      true
    );
  });

  it("does not let the retired PostgreSQL stream scan remount a withheld Parquet slider", async () => {
    mocks.getSliderCapabilities.mockResolvedValue({
      serverCurrentDate: "2026-08-28",
      futureAxisDays: 30,
      streamsUnavailable: true,
      layers: [baseCapability("interventions")],
    });

    const result = await getParquetSliderCapabilities();

    expect(result.streamsUnavailable).toBe(false);
  });
});
