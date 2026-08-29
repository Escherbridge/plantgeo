import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ZoomTier } from "@/lib/map/zoom-tiers";
import {
  CLIMATE_FIELD_SIGNAL_IDS,
  climateFieldStreamName,
} from "@/lib/environmental/climate-field";

const mocks = vi.hoisted(() => ({
  getParquetWarehouseCoverage: vi.fn(),
  getGeoFeatureSliderCapabilities: vi.fn(),
}));

vi.mock("@/lib/server/services/parquet-plane-client", () => ({
  getParquetWarehouseCoverage: mocks.getParquetWarehouseCoverage,
}));

vi.mock("@/lib/server/services/environmental-read-model", () => ({
  getGeoFeatureSliderCapabilities: mocks.getGeoFeatureSliderCapabilities,
  MAX_REPORTED_DAY_RANGES: 800,
}));

import {
  getParquetSliderCapabilities,
  PARQUET_CAPABILITY_CONTRACTS,
  PARQUET_CAPABILITY_LANES,
} from "@/lib/server/services/parquet-slider-capabilities";

const ZOOM_TIERS = [0, 5, 9, 13] as const satisfies readonly ZoomTier[];
const FIRST_DAY = "2022-08-05";
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
  mocks.getGeoFeatureSliderCapabilities.mockResolvedValue({
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
    expect(
      CLIMATE_FIELD_SIGNAL_IDS.map((signal) => {
        const contract = PARQUET_CAPABILITY_CONTRACTS.find(
          (candidate) => candidate.layerName === climateFieldStreamName(signal)
        );
        return [signal, contract?.servingReader] as const;
      })
    ).toEqual(CLIMATE_FIELD_SIGNAL_IDS.map((signal) => [signal, "parquet"]));

    const result = await getParquetSliderCapabilities();

    const parquetReaders = PARQUET_CAPABILITY_CONTRACTS.filter(
      (contract) => contract.servingReader === "parquet"
    ).map((contract) => contract.layerName);
    const nonParquetReaders = PARQUET_CAPABILITY_CONTRACTS.filter(
      (contract) => contract.servingReader !== "parquet"
    ).map((contract) => contract.layerName);
    expect(result.layers.map((layer) => layer.layerName)).toEqual([
      "burn-severity",
      "interventions",
      ...parquetReaders,
    ]);
    expect(
      result.withheldParquetCapabilities
        .filter((entry) => entry.reason === "reader_not_parquet")
        .map((entry) => entry.layerName)
    ).toEqual(nonParquetReaders.filter((layerName) => layerName !== "burn-severity"));
    expect(result.withheldParquetCapabilities).toContainEqual({
      layerName: "fire-perimeters",
      parquetLanes: ["fire-perimeters"],
      reason: "reader_not_parquet",
      missingEvidence: [],
    });
    expect(result.parquetCoverageGeneratedAt).toBe("2026-08-28T12:00:00Z");
    expect(result.parquetCoverageEvaluatedThroughDay).toBe("2026-08-28");
    expect(result.layers.find((layer) => layer.layerName === "burn-severity")).toEqual(
      baseCapability("burn-severity")
    );
    for (const layerName of parquetReaders) {
      expect(result.layers.find((layer) => layer.layerName === layerName)).toMatchObject({
        earliestObservedDate: FIRST_DAY,
        latestObservedDate: LAST_DAY,
        earliestObservedDateRule: "warehouse_coverage",
        minimumDailyObservationCount: null,
      });
    }
  });

  it("preserves Burn History from its explicit PostgreSQL capability without Parquet synthesis", async () => {
    const burnCapability = {
      ...baseCapability("burn-severity"),
      earliestObservedDate: "1984-01-01",
      latestObservedDate: "2024-12-31",
    };
    mocks.getGeoFeatureSliderCapabilities.mockResolvedValue({
      serverCurrentDate: "2026-08-28",
      futureAxisDays: 30,
      streamsUnavailable: false,
      layers: [burnCapability],
    });
    setCoverage(completeCoverage().filter((entry) => entry.layer !== "burn-severity"));

    const result = await getParquetSliderCapabilities();

    expect(result.layers).toContainEqual(burnCapability);
    expect(
      result.withheldParquetCapabilities.some((entry) => entry.layerName === "burn-severity")
    ).toBe(false);
  });

  it("reports an exact missing moisture rung while complete soil temperature remains visible", async () => {
    setCoverage(
      completeCoverage().filter(
        (entry) => !(entry.layer === "soil-field-moisture-7-28cm" && entry.zoomTier === 5)
      )
    );

    const result = await getParquetSliderCapabilities();

    expect(result.layers.some((layer) => layer.layerName === "soil-field-moisture")).toBe(false);
    expect(result.layers.some((layer) => layer.layerName === "soil-field-temperature")).toBe(true);
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
    expect(
      result.withheldParquetCapabilities.some(
        (entry) => entry.layerName === "soil-field-temperature"
      )
    ).toBe(false);
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
    expect(result.layers.some((layer) => layer.layerName === "climate-field-precipitation")).toBe(true);
    expect(
      result.withheldParquetCapabilities.some(
        (entry) => entry.layerName === "climate-field-precipitation"
      )
    ).toBe(false);
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

  it("authors an ungoverned tail when a product ends before the server current day", async () => {
    const result = await getParquetSliderCapabilities();

    expect(result.layers.find((layer) => layer.layerName === "water-gauges")?.coverageGaps).toEqual(
      [{ from: "2026-08-21", to: "2026-08-28" }]
    );
  });

  it("leaves carried release-series days selectable after the latest publication", async () => {
    const result = await getParquetSliderCapabilities();
    const drought = result.layers.find((layer) => layer.layerName === "drought-areas");

    expect(drought).toMatchObject({
      latestObservedDate: LAST_DAY,
      coverageGaps: [],
    });
  });

  it("uses the earliest required lane or rung tail for a composite capability", async () => {
    setCoverage(
      completeCoverage().map((entry) => {
        if (
          entry.layer === "climate-field-air-temperature-mean" &&
          entry.zoomTier === 13
        ) {
          return {
            ...entry,
            latestDay: "2026-08-18",
            publishedRanges: [{ from: FIRST_DAY, to: "2026-08-18" }],
          };
        }
        if (
          entry.layer === "climate-field-air-temperature-max" &&
          entry.zoomTier === 9
        ) {
          return {
            ...entry,
            latestDay: "2026-08-22",
            publishedRanges: [{ from: FIRST_DAY, to: "2026-08-22" }],
          };
        }
        return entry;
      })
    );

    const result = await getParquetSliderCapabilities();
    const airTemperature = result.layers.find(
      (layer) => layer.layerName === "climate-field-air-temperature"
    );

    expect(airTemperature).toMatchObject({
      latestObservedDate: "2026-08-18",
      coverageGaps: [{ from: "2026-08-19", to: "2026-08-28" }],
    });
  });

  it("keeps legacy water facts auditable without stretching the selectable daily series", async () => {
    setCoverage(
      completeCoverage().map((entry) =>
        entry.layer === "water-gauges"
          ? {
              ...entry,
              earliestDay: "1990-09-30",
              publishedRanges: [
                { from: "1990-09-30", to: "1990-09-30" },
                { from: FIRST_DAY, to: LAST_DAY },
              ],
              gapRanges: [{ from: "1990-10-01", to: "2022-08-04" }],
            }
          : entry
      )
    );

    const result = await getParquetSliderCapabilities();
    const water = result.layers.find((layer) => layer.layerName === "water-gauges");

    expect(water).toMatchObject({
      earliestObservedDate: FIRST_DAY,
      earliestRecordedObservationDate: "1990-09-30",
      earliestContinuousObservationDate: FIRST_DAY,
      earliestObservedDateRule: "gap_clustered",
      observedDayCount: 1_477,
      excludedObservedDayCount: 1,
      gapExcludedObservedDayCount: 1,
      coverageGaps: [{ from: "2026-08-21", to: "2026-08-28" }],
    });
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

    expect(vegetation?.coverageGaps).toEqual([
      { from: "2026-08-21", to: "2026-08-28" },
    ]);
    expect(vegetation?.governedAbsenceRanges).toEqual([
      { from: "2026-08-10", to: "2026-08-10" },
    ]);
    expect(vegetation?.earliestContinuousObservationDate).toBe("2026-08-11");
  });

  it("lets an ungoverned composite gap override overlapping governed evidence", async () => {
    setCoverage(
      completeCoverage().map((entry) => {
        if (
          entry.layer === "climate-field-air-temperature-mean" &&
          entry.zoomTier === 13
        ) {
          return {
            ...entry,
            gapRanges: [{ from: "2026-08-10", to: "2026-08-10" }],
          };
        }
        if (
          entry.layer === "climate-field-air-temperature-max" &&
          entry.zoomTier === 9
        ) {
          return {
            ...entry,
            governedAbsenceRanges: [{ from: "2026-08-09", to: "2026-08-11" }],
          };
        }
        return entry;
      })
    );

    const result = await getParquetSliderCapabilities();
    const airTemperature = result.layers.find(
      (layer) => layer.layerName === "climate-field-air-temperature"
    );

    expect(airTemperature?.coverageGaps).toEqual([
      { from: "2026-08-10", to: "2026-08-10" },
      { from: "2026-08-21", to: "2026-08-28" },
    ]);
    expect(airTemperature?.governedAbsenceRanges).toEqual([
      { from: "2026-08-09", to: "2026-08-09" },
      { from: "2026-08-11", to: "2026-08-11" },
    ]);
  });

  it("propagates a Parquet census fault before reading PostgreSQL fallback rows", async () => {
    const fault = new Error("coverage unavailable");
    mocks.getParquetWarehouseCoverage.mockRejectedValue(fault);

    await expect(getParquetSliderCapabilities()).rejects.toBe(fault);
    expect(mocks.getGeoFeatureSliderCapabilities).not.toHaveBeenCalled();
  });

  it("withholds every Parquet-owned row when coverage predates the server current day", async () => {
    mocks.getParquetWarehouseCoverage.mockResolvedValue({
      generatedAt: "2026-08-27T23:59:59Z",
      evaluatedThroughDay: "2026-08-27",
      lanes: completeCoverage(),
    });

    const result = await getParquetSliderCapabilities();

    expect(result.layers.map((layer) => layer.layerName)).toEqual([
      "burn-severity",
      "interventions",
    ]);
    expect(result.withheldParquetCapabilities).toHaveLength(
      PARQUET_CAPABILITY_CONTRACTS.length - 1
    );
    expect(result.withheldParquetCapabilities.every((entry) => entry.reason === "coverage_not_current")).toBe(
      true
    );
  });

  it("does not let the retired PostgreSQL stream scan remount a withheld Parquet slider", async () => {
    mocks.getGeoFeatureSliderCapabilities.mockResolvedValue({
      serverCurrentDate: "2026-08-28",
      futureAxisDays: 30,
      streamsUnavailable: true,
      layers: [baseCapability("interventions")],
    });

    const result = await getParquetSliderCapabilities();

    expect(result.streamsUnavailable).toBe(false);
  });
});
