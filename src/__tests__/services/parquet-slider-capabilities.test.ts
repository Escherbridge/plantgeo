import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ZoomTier } from "@/lib/map/zoom-tiers";
import {
  CLIMATE_FIELD_SIGNAL_IDS,
  climateFieldStreamName,
} from "@/lib/environmental/climate-field";
import { UpstreamHttpError } from "@/lib/server/http/bounded-upstream";
import type { ParquetLaneCoverage } from "@/lib/server/services/parquet-plane-client";

const mocks = vi.hoisted(() => ({
  getParquetWarehouseCoverage: vi.fn(),
  getGeoFeatureSliderCapabilities: vi.fn(),
}));

vi.mock("@/lib/server/services/parquet-plane-client", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("@/lib/server/services/parquet-plane-client")
  >();
  return {
    ...actual,
    getParquetWarehouseCoverage: mocks.getParquetWarehouseCoverage,
  };
});

vi.mock("@/lib/server/services/environmental-read-model", () => ({
  getGeoFeatureSliderCapabilities: mocks.getGeoFeatureSliderCapabilities,
  MAX_REPORTED_DAY_RANGES: 800,
}));

import {
  getParquetSliderCapabilities,
  MAX_REPORTED_GOVERNED_ABSENCE_RANGES,
  PARQUET_CAPABILITY_CONTRACTS,
  PARQUET_CAPABILITY_LANES,
} from "@/lib/server/services/parquet-slider-capabilities";
// The REAL client readers, imported rather than restated: what this payload has to keep
// derivable is whatever these functions can still answer, so a second copy of their rules here
// would pin the copy instead of the contract.
import {
  dayCoverageState,
  describeDayCoverage,
  MINIMUM_DRAWN_BAND_PERCENT,
} from "@/components/map/layer-panel/layer-coverage-track";
import {
  isDayDescribed,
  isWithinGovernedAbsence,
  type SliderDomain,
} from "@/stores/time-slider-store";

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

/**
 * A row the decoder could really have produced, narrowed to the `observed` kind these tests use.
 *
 * Intersected with the exported `ParquetLaneCoverage` rather than hand-copied from it, which is
 * what a hand copy cost once already: `latestRecordedDay` reached the gate while a restated shape
 * here would have kept every fixture silently one field short of the wire, and the suite would
 * have passed on evidence no client ever receives.
 */
type CoverageRow = ParquetLaneCoverage & { kind: "observed" };

/** A healthy, index-backed lane: the state every test starts from and then breaks one field of. */
const AVAILABILITY_EVIDENCE = {
  coverageAuthority: "availability",
  availabilityGenerationSha256: "b".repeat(64),
  availabilityPointerKey: "availability/_LATEST.json",
  sourceCeilingDay: null,
  requiredRungs: ZOOM_TIERS,
  withheldReason: null,
} as const satisfies Omit<
  CoverageRow,
  | "layer"
  | "nature"
  | "kind"
  | "zoomTier"
  | "earliestDay"
  | "latestDay"
  | "latestRecordedDay"
  | "publishedRanges"
  | "gapRanges"
  | "governedAbsenceRanges"
>;

/** Rewrite one lane's rows across every rung; a lane's index is withheld for all four or none. */
function withLane(
  lanes: CoverageRow[],
  layer: string,
  patch: Partial<CoverageRow>
): CoverageRow[] {
  return lanes.map((lane) => (lane.layer === layer ? { ...lane, ...patch } : lane));
}

function natureFor(layer: string): CoverageRow["nature"] {
  if (layer === "drought" || layer === "burn-severity") return "release_series";
  if (["watersheds", "soil-survey", "evacuation-zones", "fire-perimeters"].includes(layer)) {
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
      // Nothing carries in the baseline, so the day it answers and the day it wrote are one day.
      // A carry lane is built by moving `latestDay` forward and leaving this one where it is.
      latestRecordedDay: LAST_DAY,
      publishedRanges: [{ from: FIRST_DAY, to: LAST_DAY }],
      gapRanges: [],
      governedAbsenceRanges: [],
      ...AVAILABILITY_EVIDENCE,
    }))
  );
}

function setCoverage(lanes: CoverageRow[]): void {
  mocks.getParquetWarehouseCoverage.mockResolvedValue({
    coverageSchemaVersion: 3,
    generatedAt: "2026-08-28T12:00:00Z",
    evaluatedThroughDay: "2026-08-28",
    lanes,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-08-28T12:00:00Z"));
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

afterEach(() => {
  vi.useRealTimers();
});

describe("getParquetSliderCapabilities", () => {
  it("serves Parquet dates when the retired PostgreSQL census would fail", async () => {
    mocks.getGeoFeatureSliderCapabilities.mockRejectedValue(
      new Error('materialized view "mv_signal_observation_day" has not been populated')
    );
    const result = await getParquetSliderCapabilities();
    expect(result.layers.find((layer) => layer.layerName === "fire-detections")).toMatchObject({
      earliestObservedDate: FIRST_DAY,
      latestObservedDate: LAST_DAY,
    });
    expect(result.serverCurrentDate).toBe("2026-08-28");
    expect(result.futureAxisDays).toBe(30);
    expect(mocks.getGeoFeatureSliderCapabilities).not.toHaveBeenCalled();
  });

  it("restamps UTC today after a cold read crosses midnight", async () => {
    mocks.getParquetWarehouseCoverage.mockImplementation(async () => {
      vi.setSystemTime(new Date("2026-08-29T00:00:00Z"));
      return { generatedAt: "2026-08-28T12:00:00Z", evaluatedThroughDay: "2026-08-28", lanes: completeCoverage() };
    });
    const result = await getParquetSliderCapabilities();
    expect(result.serverCurrentDate).toBe("2026-08-29");
    expect(result.layers).toEqual([]);
    expect(result.withheldParquetCapabilities.every((entry) => entry.reason === "coverage_not_current")).toBe(true);
  });
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
    // The public census publishes only its explicitly owned Parquet contracts.
    expect(result.layers.map((layer) => layer.layerName)).toEqual([
      ...parquetReaders,
    ]);
    expect(parquetReaders).toContain("burn-severity");
    expect(nonParquetReaders).toEqual([]);
    expect(result.parquetCoverageGeneratedAt).toBe("2026-08-28T12:00:00Z");
    expect(result.parquetCoverageEvaluatedThroughDay).toBe("2026-08-28");
    expect(result.parquetCoverageUnavailable).toBe(false);
    // Synthesized from the census, NOT the `baseCapability("burn-severity")` row the PostgreSQL
    // mock still publishes: its 1999 days would show here if any fallback survived.
    expect(result.layers.find((layer) => layer.layerName === "burn-severity")).toMatchObject({
      earliestObservedDate: FIRST_DAY,
      latestObservedDate: LAST_DAY,
      coverageGaps: [],
      // The cumulative reader restates the whole axis as described, so BOTH boundaries clear.
      describedFromDay: null,
      describedThroughDay: null,
      earliestObservedDateRule: "full_history",
      minimumDailyObservationCount: null,
    });
    for (const layerName of parquetReaders.filter((name) => name !== "burn-severity")) {
      expect(result.layers.find((layer) => layer.layerName === layerName)).toMatchObject({
        earliestObservedDate: FIRST_DAY,
        latestObservedDate: LAST_DAY,
        earliestObservedDateRule: "warehouse_coverage",
        minimumDailyObservationCount: null,
      });
    }
  });

  /**
   * The PostgreSQL row this asserts AGAINST is the one burn-severity was served from until
   * 2026-09-07, while its pixels already came from `getParquetBurnSeverity`. Its distinctive 2015
   * -2024 days are the tell: if any passthrough survived, they would appear here instead of the
   * census's own bounds.
   */
  it("synthesizes Burn History from Parquet evidence instead of its PostgreSQL capability", async () => {
    const postgresBurnRow = {
      ...baseCapability("burn-severity"),
      earliestObservedDate: "2024-08-22",
      latestObservedDate: "2024-08-22",
      coverageGaps: [{ from: "2015-04-02", to: "2024-08-21" }],
      thinRanges: [{ from: "2015-04-01", to: "2024-08-22" }],
      describedFromDay: "2015-04-01",
      earliestObservedDateRule: "gap_clustered" as const,
      earliestRecordedObservationDate: "2015-04-01",
      earliestContinuousObservationDate: "2024-08-22",
      latestRecordedObservationDate: "2024-08-22",
      observedDayCount: 1,
      excludedObservedDayCount: 9,
      gapExcludedObservedDayCount: 9,
      minimumDailyObservationCount: 1,
    };
    mocks.getGeoFeatureSliderCapabilities.mockResolvedValue({
      serverCurrentDate: "2026-08-28",
      futureAxisDays: 30,
      streamsUnavailable: false,
      layers: [postgresBurnRow, baseCapability("interventions")],
    });

    const result = await getParquetSliderCapabilities();
    const burn = result.layers.find((layer) => layer.layerName === "burn-severity");

    expect(burn).toMatchObject({
      temporalKind: "event",
      earliestObservedDate: FIRST_DAY,
      latestObservedDate: LAST_DAY,
      earliestRecordedObservationDate: FIRST_DAY,
      latestRecordedObservationDate: LAST_DAY,
      // The cumulative rewrite reached the Parquet path: no PostgreSQL row states these.
      coverageGaps: [],
      earliestObservedDateRule: "full_history",
      coverageAuthority: "availability",
      requiredRungs: [0, 5, 9, 13],
    });
    expect(burn).not.toMatchObject({ earliestObservedDate: "2024-08-22" });
    expect(
      result.withheldParquetCapabilities.some((entry) => entry.layerName === "burn-severity")
    ).toBe(false);
  });

  it("withholds Burn History rather than falling back when its lane is absent from the census", async () => {
    setCoverage(completeCoverage().filter((entry) => entry.layer !== "burn-severity"));

    const result = await getParquetSliderCapabilities();

    expect(result.layers.some((layer) => layer.layerName === "burn-severity")).toBe(false);
    expect(result.withheldParquetCapabilities).toContainEqual({
      layerName: "burn-severity",
      parquetLanes: ["burn-severity"],
      reason: "lane_not_registered",
      missingEvidence: [{ parquetLane: "burn-severity", zoomTier: null }],
    });
  });

  /**
   * THE regression this cutover exists to avoid.
   *
   * MTBS publishes roughly five release days across 2015-2026, so the census's own evidence for the
   * lane is a handful of single days separated by multi-year `gapRanges`. Synthesized literally,
   * that axis marks nearly every day between releases as uncovered -- while
   * `getParquetBurnSeverity` (`parquet-trpc-readers.ts:2122`) walks back and unions every release at
   * or before the viewed day, so those days draw perfectly. The scrubber would forbid days the
   * renderer handles, which is the one direction of error the slider contract must never take.
   */
  it("keeps every day after the first release selectable across sparse MTBS release days", async () => {
    setCoverage(
      withLane(completeCoverage(), "burn-severity", {
        earliestDay: "2015-04-01",
        latestDay: "2024-08-22",
        latestRecordedDay: "2024-08-22",
        publishedRanges: [
          { from: "2015-04-01", to: "2015-04-01" },
          { from: "2020-11-24", to: "2020-11-24" },
          { from: "2024-08-22", to: "2024-08-22" },
        ],
        gapRanges: [
          { from: "2015-04-02", to: "2020-11-23" },
          { from: "2020-11-25", to: "2024-08-21" },
        ],
      })
    );

    const result = await getParquetSliderCapabilities();
    const burn = result.layers.find((layer) => layer.layerName === "burn-severity");

    expect(burn).toMatchObject({
      earliestObservedDate: "2015-04-01",
      latestObservedDate: "2024-08-22",
      // Not the two multi-year holes the census reported: nine years of them would be forbidden days.
      coverageGaps: [],
      governedAbsenceRanges: [],
      thinRanges: [],
      describedFromDay: null,
      describedThroughDay: null,
      earliestObservedDateRule: "full_history",
      earliestRecordedObservationDate: "2015-04-01",
      earliestContinuousObservationDate: "2015-04-01",
      // Three released days folded once, never twice: the rewrite adds `excludedObservedDayCount`
      // into this total and is applied at a single site.
      observedDayCount: 3,
      excludedObservedDayCount: 0,
      gapExcludedObservedDayCount: 0,
      densityExcludedObservedDayCount: 0,
      minimumDailyObservationCount: null,
    });
  });

  /**
   * The negative control for the rewrite above: it is keyed on the LAYER NAME, not on
   * `temporalKind: "event"` or `parquetNature: "release_series"`, because only MTBS's reader unions
   * its past releases. A drought or perimeter gap is a real hole, and clearing it would assert
   * coverage nobody measured.
   */
  it("leaves other event and release-series layers' gaps exactly as the census reported them", async () => {
    const sparse = {
      earliestDay: "2015-04-01",
      latestDay: "2024-08-22",
      latestRecordedDay: "2024-08-22",
      publishedRanges: [
        { from: "2015-04-01", to: "2015-04-01" },
        { from: "2020-11-24", to: "2020-11-24" },
        { from: "2024-08-22", to: "2024-08-22" },
      ],
      gapRanges: [
        { from: "2015-04-02", to: "2020-11-23" },
        { from: "2020-11-25", to: "2024-08-21" },
      ],
    } as const satisfies Partial<CoverageRow>;
    setCoverage(
      withLane(withLane(completeCoverage(), "drought", sparse), "fire-perimeters", sparse)
    );

    const result = await getParquetSliderCapabilities();

    // `drought-areas` is the release-series half, `fire-perimeters` the `temporalKind: "event"` half.
    for (const layerName of ["drought-areas", "fire-perimeters"]) {
      expect(result.layers.find((layer) => layer.layerName === layerName)).toMatchObject({
        earliestObservedDate: "2015-04-01",
        coverageGaps: [
          { from: "2015-04-02", to: "2020-11-23" },
          { from: "2020-11-25", to: "2024-08-21" },
        ],
        earliestObservedDateRule: "warehouse_coverage",
        earliestContinuousObservationDate: "2024-08-22",
        observedDayCount: 3,
      });
    }
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
          ? { ...entry, earliestDay: null, latestDay: null, latestRecordedDay: null, publishedRanges: [] }
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
        latestRecordedDay: null,
        publishedRanges: [],
      }),
      reason: "rung_never_written",
    },
    {
      name: "half-bounded",
      mutate: (entry: CoverageRow): CoverageRow => ({ ...entry, latestDay: null }),
      reason: "invalid_rung_bounds",
    },
    {
      // A rung that can answer a day but names no day it wrote. The serving side nulls all three
      // bounds together or none, so this row is incoherent -- and the ceiling check has no day to
      // weigh, which must fail closed rather than skip the rung or fall back to the carried edge.
      name: "missing its recorded edge",
      mutate: (entry: CoverageRow): CoverageRow => ({ ...entry, latestRecordedDay: null }),
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
            latestRecordedDay: "2026-08-18",
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
            latestRecordedDay: "2026-08-22",
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

  /**
   * The ceiling is the lane's freshness horizon; `evaluatedThroughDay` is only when the census ran.
   * Running the closing gap to today would report every day the upstream has not published yet as
   * an ingest hole, so a lane correctly waiting on a lagged release reads as dead.
   */
  it("closes the tail at the lane's own source horizon, not at the census day", async () => {
    setCoverage(
      withLane(completeCoverage(), "vegetation", {
        latestDay: "2026-08-18",
        latestRecordedDay: "2026-08-18",
        publishedRanges: [{ from: FIRST_DAY, to: "2026-08-18" }],
        sourceCeilingDay: "2026-08-20",
      })
    );

    const result = await getParquetSliderCapabilities();

    // Two owed days, not the ten that run to the 2026-08-28 census day.
    //
    // `describedThroughDay` publishes the bound the tail was clamped to. Without it the eight days
    // above the ceiling are absent from `coverageGaps` for the SAME reason a dense day is, and a
    // client would read the server's deliberate silence as coverage -- inverting the clamp.
    expect(result.layers.find((layer) => layer.layerName === "vegetation")).toMatchObject({
      latestObservedDate: "2026-08-18",
      coverageGaps: [{ from: "2026-08-19", to: "2026-08-20" }],
      describedThroughDay: "2026-08-20",
    });
  });

  it("owes nothing at all when the lane already holds through its own ceiling", async () => {
    setCoverage(
      withLane(completeCoverage(), "vegetation", {
        latestDay: "2026-08-18",
        latestRecordedDay: "2026-08-18",
        publishedRanges: [{ from: FIRST_DAY, to: "2026-08-18" }],
        sourceCeilingDay: "2026-08-18",
      })
    );

    const result = await getParquetSliderCapabilities();

    expect(
      result.layers.find((layer) => layer.layerName === "vegetation")?.coverageGaps
    ).toEqual([]);
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

  it("does not start a PostgreSQL read while Parquet coverage is cold", async () => {
    let resolveCoverage!: (coverage: ReturnType<typeof completeCoverage>) => void;
    mocks.getParquetWarehouseCoverage.mockReturnValue(
      new Promise((resolve) => {
        resolveCoverage = (lanes) =>
          resolve({
            generatedAt: "2026-08-28T12:00:00Z",
            evaluatedThroughDay: "2026-08-28",
            lanes,
          });
      })
    );

    const pending = getParquetSliderCapabilities();
    expect(mocks.getGeoFeatureSliderCapabilities).not.toHaveBeenCalled();
    resolveCoverage(completeCoverage());
    await pending;
  });

  it("returns a retryable incomplete payload when the coverage plane is unavailable", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    mocks.getParquetWarehouseCoverage.mockRejectedValue(new UpstreamHttpError(503));

    const result = await getParquetSliderCapabilities();

    // burn-severity is gone too, deliberately: its pixels are Parquet, so retaining a PostgreSQL
    // axis for it exactly when the warehouse cannot answer is the inversion this module refuses.
    expect(result.layers.map((layer) => layer.layerName)).toEqual([]);
    expect(result.parquetCoverageUnavailable).toBe(true);
    expect(result.parquetCoverageGeneratedAt).toBeNull();
    expect(result.parquetCoverageEvaluatedThroughDay).toBeNull();
    expect(result.withheldParquetCapabilities).toHaveLength(PARQUET_CAPABILITY_CONTRACTS.length);
    expect(
      result.withheldParquetCapabilities.map((entry) => entry.layerName)
    ).toContain("burn-severity");
    expect(
      result.withheldParquetCapabilities.every(
        (entry) => entry.reason === "coverage_unavailable"
      )
    ).toBe(true);
    expect(errorSpy).toHaveBeenCalledOnce();
    errorSpy.mockRestore();
  });

  it("propagates an unexpected Parquet census programming fault without fallback", async () => {
    const fault = new Error("coverage unavailable");
    mocks.getParquetWarehouseCoverage.mockRejectedValue(fault);

    await expect(getParquetSliderCapabilities()).rejects.toBe(fault);
    expect(mocks.getGeoFeatureSliderCapabilities).not.toHaveBeenCalled();
  });

  it("withholds every Parquet-owned row when coverage predates the server current day", async () => {
    mocks.getParquetWarehouseCoverage.mockResolvedValue({
      coverageSchemaVersion: 2,
      generatedAt: "2026-08-27T23:59:59Z",
      evaluatedThroughDay: "2026-08-27",
      lanes: completeCoverage(),
    });

    const result = await getParquetSliderCapabilities();

    expect(result.layers.map((layer) => layer.layerName)).toEqual([]);
    expect(result.withheldParquetCapabilities).toHaveLength(PARQUET_CAPABILITY_CONTRACTS.length);
    expect(result.withheldParquetCapabilities.every((entry) => entry.reason === "coverage_not_current")).toBe(
      true
    );
  });

  /**
   * The availability index is the strongest evidence the plane publishes, and a withheld one is a
   * statement that the evidence is not there to be reasoned from. Falling back to the census walk
   * would answer, from a weaker witness, the exact question the warehouse just declined.
   */
  it.each([
    "availability_unpublished",
    "availability_stale",
    "availability_malformed",
    "availability_checksum_invalid",
  ] as const)("withholds a lane whose availability index reports %s", async (withheldReason) => {
    setCoverage(withLane(completeCoverage(), "vegetation", { withheldReason }));

    const result = await getParquetSliderCapabilities();

    expect(result.layers.some((layer) => layer.layerName === "vegetation")).toBe(false);
    expect(result.withheldParquetCapabilities).toContainEqual({
      layerName: "vegetation",
      parquetLanes: ["vegetation"],
      reason: withheldReason,
      // One entry per LANE, not per rung: a withheld index is a fact about the lane's published
      // evidence as a whole, so naming all four rungs would overstate what was measured.
      missingEvidence: [{ parquetLane: "vegetation", zoomTier: null }],
    });
    // Every other row is untouched: withholding is per lane, not a census-wide kill switch.
    expect(result.layers.some((layer) => layer.layerName === "water-gauges")).toBe(true);
  });

  it("reports the wire's first-declared reason when rungs of one lane disagree", async () => {
    setCoverage(
      completeCoverage().map((entry) =>
        entry.layer !== "vegetation"
          ? entry
          : {
              ...entry,
              withheldReason:
                entry.zoomTier === 13 ? "availability_checksum_invalid" : "availability_unpublished",
            }
      )
    );

    const result = await getParquetSliderCapabilities();

    expect(
      result.withheldParquetCapabilities.find((entry) => entry.layerName === "vegetation")?.reason
    ).toBe("availability_unpublished");
  });

  /**
   * burn-severity kept a PostgreSQL row through `POSTGRES_CAPABILITY_PASSTHROUGH_NAMES` until
   * 2026-09-07. The set is gone, so this now pins the stronger rule structurally: a withheld index
   * leaves the layer with NO row, and the PostgreSQL capability the mock still publishes for it is
   * not a second opinion about the evidence the warehouse just declined to state.
   */
  it("leaves burn-severity with no row at all when its Parquet lane withholds its index", async () => {
    setCoverage(
      withLane(completeCoverage(), "burn-severity", {
        withheldReason: "availability_checksum_invalid",
      })
    );

    const result = await getParquetSliderCapabilities();

    expect(result.layers.some((layer) => layer.layerName === "burn-severity")).toBe(false);
    expect(result.withheldParquetCapabilities).toContainEqual(
      expect.objectContaining({
        layerName: "burn-severity",
        reason: "availability_checksum_invalid",
      })
    );
  });

  /**
   * The other half of that rule, and the half that is easy to over-apply: withholding is per NAMED
   * lane. An unrelated lane's unpublished index says nothing about burn-severity's evidence, so
   * dropping its row on that would blank a layer nobody made a claim about -- a false report of its
   * own, in the same direction fail-closed is trying to avoid errors in.
   */
  it("keeps burn-severity synthesized when a DIFFERENT lane withholds its index", async () => {
    setCoverage(
      withLane(completeCoverage(), "vegetation", { withheldReason: "availability_unpublished" })
    );

    const result = await getParquetSliderCapabilities();

    expect(result.layers.find((layer) => layer.layerName === "burn-severity")).toMatchObject({
      earliestObservedDate: FIRST_DAY,
      earliestObservedDateRule: "full_history",
    });
    expect(
      result.withheldParquetCapabilities.some((entry) => entry.layerName === "burn-severity")
    ).toBe(false);
  });

  it("asks availability before currency, so one lane's unpublished index is not read as a stale census", async () => {
    mocks.getParquetWarehouseCoverage.mockResolvedValue({
      coverageSchemaVersion: 2,
      generatedAt: "2026-08-27T23:59:59Z",
      evaluatedThroughDay: "2026-08-27",
      lanes: withLane(completeCoverage(), "vegetation", {
        withheldReason: "availability_unpublished",
      }),
    });

    const result = await getParquetSliderCapabilities();

    expect(
      result.withheldParquetCapabilities.find((entry) => entry.layerName === "vegetation")?.reason
    ).toBe("availability_unpublished");
    expect(
      result.withheldParquetCapabilities.find((entry) => entry.layerName === "water-gauges")?.reason
    ).toBe("coverage_not_current");
  });

  /**
   * A day the source cannot have published is a lane that is wrong about something. Clamping the
   * axis to the ceiling would hide that behind a plausible answer; withholding names the rung.
   */
  it("withholds a rung holding a day past its own source ceiling rather than clamping it", async () => {
    setCoverage(
      completeCoverage().map((entry) =>
        entry.layer === "vegetation" && entry.zoomTier === 9
          ? { ...entry, sourceCeilingDay: "2026-08-19" }
          : entry
      )
    );

    const result = await getParquetSliderCapabilities();

    expect(result.layers.some((layer) => layer.layerName === "vegetation")).toBe(false);
    expect(result.withheldParquetCapabilities).toContainEqual({
      layerName: "vegetation",
      parquetLanes: ["vegetation"],
      reason: "ceiling_violation",
      missingEvidence: [{ parquetLane: "vegetation", zoomTier: 9 }],
    });
  });

  it("accepts a latest day that sits exactly on the ceiling", async () => {
    setCoverage(withLane(completeCoverage(), "vegetation", { sourceCeilingDay: LAST_DAY }));

    const result = await getParquetSliderCapabilities();

    expect(
      result.layers.find((layer) => layer.layerName === "vegetation")?.latestObservedDate
    ).toBe(LAST_DAY);
  });

  /**
   * THE 2026-09-07 REGRESSION, at this suite's clock. `drought-areas` served 1,470 days that
   * morning and was withheld by the afternoon, because `drought-direct-forward` published a
   * release recent enough for its fourteen-day carry to cross its four-day publication lag.
   *
   * Production measured `latest_day` 2026-09-07, `source_ceiling_day` 2026-09-03 and a newest
   * written object of 2026-09-01: the carried edge four days above the ceiling, the recorded day
   * two below it. Those offsets are reproduced here against `CENSUS_DAY`, and the carry is folded
   * into `publishedRanges` exactly as the serving side folds it.
   */
  it("serves a release lane carrying past its own ceiling, with the axis still ending at the carried day", async () => {
    setCoverage(
      withLane(completeCoverage(), "drought", {
        latestDay: "2026-08-28",
        latestRecordedDay: "2026-08-22",
        sourceCeilingDay: "2026-08-24",
        publishedRanges: [{ from: FIRST_DAY, to: "2026-08-28" }],
      })
    );

    const result = await getParquetSliderCapabilities();

    expect(
      result.withheldParquetCapabilities.some((entry) => entry.layerName === "drought-areas")
    ).toBe(false);
    expect(result.layers.find((layer) => layer.layerName === "drought-areas")).toMatchObject({
      earliestObservedDate: FIRST_DAY,
      // The carry is the FEATURE: a reader draws the 22nd's map on the 28th, so the axis has to
      // reach the 28th. Clamping it to the ceiling would shorten the lane by its own lag.
      latestObservedDate: "2026-08-28",
      sourceCeilingDay: "2026-08-24",
      coverageGaps: [],
    });
  });

  /**
   * The carry is not an exemption. A lane whose RECORDED day is past its ceiling is wrong about
   * something whether or not it also carries, so this is the same violation as the vegetation
   * case above, run on the one lane that could have talked its way out of it.
   */
  it("still withholds a carrying lane that recorded a day past its own ceiling", async () => {
    setCoverage(
      withLane(completeCoverage(), "drought", {
        latestDay: "2026-08-28",
        latestRecordedDay: "2026-08-27",
        sourceCeilingDay: "2026-08-24",
        publishedRanges: [{ from: FIRST_DAY, to: "2026-08-28" }],
      })
    );

    const result = await getParquetSliderCapabilities();

    expect(result.layers.some((layer) => layer.layerName === "drought-areas")).toBe(false);
    expect(
      result.withheldParquetCapabilities.find((entry) => entry.layerName === "drought-areas")
    ).toMatchObject({ reason: "ceiling_violation" });
  });

  /**
   * A lane that does not carry sees nothing change: its two edges are the same day, so the gate
   * asks the identical question it asked before. Stated as a test because "the fix only touches
   * carry lanes" is the claim a reviewer most needs held to.
   */
  it("leaves a non-carrying lane's proof exactly as it was", async () => {
    setCoverage(
      withLane(completeCoverage(), "vegetation", {
        latestDay: "2026-08-18",
        latestRecordedDay: "2026-08-18",
        publishedRanges: [{ from: FIRST_DAY, to: "2026-08-18" }],
        sourceCeilingDay: "2026-08-18",
      })
    );

    const result = await getParquetSliderCapabilities();

    expect(result.layers.find((layer) => layer.layerName === "vegetation")).toMatchObject({
      earliestObservedDate: FIRST_DAY,
      latestObservedDate: "2026-08-18",
      coverageGaps: [],
    });
    expect(
      result.withheldParquetCapabilities.some((entry) => entry.layerName === "vegetation")
    ).toBe(false);
  });

  it("surfaces the authority, the binding ceiling and the declared rungs on the published row", async () => {
    setCoverage(
      completeCoverage().map((entry) =>
        entry.layer !== "vegetation"
          ? entry
          : {
              ...entry,
              // The most binding ceiling wins, and one census rung makes the whole row a census row.
              sourceCeilingDay: entry.zoomTier === 5 ? "2026-08-21" : "2026-08-25",
              coverageAuthority: entry.zoomTier === 0 ? "census" : "availability",
            }
      )
    );

    const result = await getParquetSliderCapabilities();

    expect(result.layers.find((layer) => layer.layerName === "vegetation")).toMatchObject({
      coverageAuthority: "census",
      sourceCeilingDay: "2026-08-21",
      requiredRungs: [0, 5, 9, 13],
    });
  });

  it("reports an availability-backed row as such when every rung read the index", async () => {
    const result = await getParquetSliderCapabilities();

    expect(result.layers.find((layer) => layer.layerName === "water-gauges")).toMatchObject({
      coverageAuthority: "availability",
      sourceCeilingDay: null,
      // Nothing bounds the source, so the tail ran to the coverage end and the row is described
      // all the way there. The bound is stated rather than left absent: `undefined` means "a
      // server that predates the field", which a client must read as "described forever".
      describedThroughDay: "2026-08-28",
    });
  });

  /**
   * `requiredRungs` is a LABEL for what the gate enforced, never a gate the client re-applies --
   * and `REQUIRED_ZOOM_TIERS` is what `proveCapability` enforced before this row could exist.
   * Publishing `[]` when the wire declared nothing labelled a four-rung proof as an unconditional
   * one, which is the single reading of this field a client must never be able to take.
   */
  it("labels a row with the rungs the gate enforced when the wire declares none", async () => {
    setCoverage(withLane(completeCoverage(), "vegetation", { requiredRungs: [] }));

    const result = await getParquetSliderCapabilities();

    expect(
      result.layers.find((layer) => layer.layerName === "vegetation")?.requiredRungs
    ).toEqual([0, 5, 9, 13]);
  });

  it("keeps the wire's own declaration when it makes one", async () => {
    setCoverage(withLane(completeCoverage(), "vegetation", { requiredRungs: [9, 13] }));

    const result = await getParquetSliderCapabilities();

    expect(
      result.layers.find((layer) => layer.layerName === "vegetation")?.requiredRungs
    ).toEqual([9, 13]);
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

const MILLISECONDS_PER_DAY = 86_400_000;

function epochDay(day: string): number {
  return Date.parse(`${day}T00:00:00Z`) / MILLISECONDS_PER_DAY;
}

function calendarDay(epoch: number): string {
  return new Date(epoch * MILLISECONDS_PER_DAY).toISOString().slice(0, 10);
}

function daysIn(ranges: readonly { from: string; to: string }[]): number {
  return ranges.reduce((count, range) => count + epochDay(range.to) - epochDay(range.from) + 1, 0);
}

/** The `2026-08-28` every fixture here shares: `serverCurrentDate` and `evaluatedThroughDay` both. */
const CENSUS_DAY = "2026-08-28";

/**
 * Production's fire-detections lane, reproduced to the day: a ~26-year axis whose non-published
 * days are governed absences rather than ingest holes.
 *
 * The measured shape on 2026-09-07, matched here field for field -- 8,371 observed days, 447
 * governed-absence ranges over 1,069 days, ZERO coverage gaps, axis running to the census day so
 * no tail is owed. The absence runs are 2 and 3 days long and sit 18 or 19 published days apart,
 * which is production's own mean spacing of 21 days on a 9,440-day axis; that spacing is the
 * whole point, because 0.9% of this axis is 85 days and no two of these ranges can be drawn
 * apart by `floorAxisRunsToBands`.
 */
function fireDetectionsLane(): {
  publishedRanges: Array<{ from: string; to: string }>;
  governedAbsenceRanges: Array<{ from: string; to: string }>;
  earliestDay: string;
  latestDay: string;
  latestRecordedDay: string;
} {
  const axisDays = 9_440;
  const firstEpoch = epochDay(CENSUS_DAY) - (axisDays - 1);
  const publishedRanges: Array<{ from: string; to: string }> = [];
  const governedAbsenceRanges: Array<{ from: string; to: string }> = [];

  // The axis opens on a published day so `earliestObservedDate` is the axis start, not an
  // absence marker -- production's row opens the same way.
  publishedRanges.push({ from: calendarDay(firstEpoch), to: calendarDay(firstEpoch) });
  let cursor = firstEpoch + 1;
  for (let index = 0; index < 447; index += 1) {
    const absenceLength = index < 175 ? 3 : 2;
    const publishedLength = index < 324 ? 19 : 18;
    governedAbsenceRanges.push({
      from: calendarDay(cursor),
      to: calendarDay(cursor + absenceLength - 1),
    });
    cursor += absenceLength;
    publishedRanges.push({
      from: calendarDay(cursor),
      to: calendarDay(cursor + publishedLength - 1),
    });
    cursor += publishedLength;
  }

  return {
    publishedRanges,
    governedAbsenceRanges,
    earliestDay: calendarDay(firstEpoch),
    latestDay: CENSUS_DAY,
    latestRecordedDay: CENSUS_DAY,
  };
}

async function fireDetectionsCapability() {
  const lane = fireDetectionsLane();
  setCoverage(withLane(completeCoverage(), "fire-detections", lane));
  const result = await getParquetSliderCapabilities();
  const capability = result.layers.find((layer) => layer.layerName === "fire-detections");
  if (capability === undefined) throw new Error("fire-detections was withheld");
  return { lane, capability };
}

/**
 * What this payload costs a browser, and what it still lets one say.
 *
 * The endpoint is whole-warehouse, un-bboxed and memoised for 300 s, so every viewport in every
 * tab downloads one blob; on 2026-09-07 that blob was 42,291 bytes for 13 layers and
 * fire-detections' `governedAbsenceRanges` alone was 20,580 of them -- 49% of the payload spent
 * on one field of one layer.
 */
describe("governed-absence reporting on an absence-dominated axis", () => {
  it("holds the cap to the number of marks a coverage track can actually draw", () => {
    // The derivation asserted rather than commented, and asserted against the component's own
    // constant so a change to the flooring policy fails HERE. Deliberately not imported by the
    // service itself: a server payload must not compile against a CSS width, and a mirrored
    // copy of the number is a drift this test exists to catch instead.
    expect(MAX_REPORTED_GOVERNED_ABSENCE_RANGES).toBe(Math.ceil(100 / MINIMUM_DRAWN_BAND_PERCENT));
    expect(MAX_REPORTED_GOVERNED_ABSENCE_RANGES).toBe(112);
  });

  it("caps the fire axis to 4,481 bytes of absence ranges from 17,881 and publishes the boundary", async () => {
    const { lane, capability } = await fireDetectionsCapability();

    // The fixture is production's row, so these are the numbers the cap is being judged against.
    expect(lane.governedAbsenceRanges).toHaveLength(447);
    expect(daysIn(lane.governedAbsenceRanges)).toBe(1_069);
    expect(capability.observedDayCount).toBe(8_371);
    expect(capability.coverageGaps).toEqual([]);

    // BEFORE is not a guess: with `gapRanges` empty, `subtractRangeSets` is the identity and
    // `mergeDayRanges` merges nothing at 18-day spacing, so the uncapped code emitted exactly
    // the fixture's own list. AFTER is what it emits now.
    const beforeBytes = JSON.stringify(lane.governedAbsenceRanges).length;
    const afterBytes = JSON.stringify(capability.governedAbsenceRanges).length;
    expect(beforeBytes).toBe(17_881);
    expect(afterBytes).toBe(4_481);
    expect(beforeBytes - afterBytes).toBe(13_400);

    // The whole row, which is what actually ships: the field stops being the largest thing in it.
    const beforeRowBytes = JSON.stringify({
      ...capability,
      governedAbsenceRanges: lane.governedAbsenceRanges,
      describedFromDay: null,
    }).length;
    // 13,400 off the list, 8 back on for the boundary day that replaces a `null` -- the honesty
    // this change buys costs eight bytes and is the only thing the row gained.
    expect(beforeRowBytes - JSON.stringify(capability).length).toBe(13_392);

    // The newest 112 ranges kept, and the boundary set to the oldest SURVIVOR's own start day --
    // not the newest dropped range's end -- because a day just under it may sit inside a range
    // that was dropped.
    expect(capability.governedAbsenceRanges).toEqual(lane.governedAbsenceRanges.slice(-112));
    expect(capability.describedFromDay).toBe("2020-07-11");

    // The per-list fields keep stating per-list truths: `coverageGaps` really does describe this
    // whole axis, and saying otherwise to signal a DIFFERENT list's truncation would be a second
    // false report. The conjunction lives in `describedFromDay` alone.
    expect(capability.coverageGapsTruncated).toBe(false);
    expect(capability.coverageGapsDescribedFromDay).toBeNull();

    // Nothing about the axis narrowed. Every day from 2000 on is still selectable, still
    // fetchable, and still answered exactly by the read's own envelope.
    expect(capability.earliestObservedDate).toBe(lane.earliestDay);
    expect(capability.latestObservedDate).toBe(CENSUS_DAY);
    expect(capability.describedThroughDay).toBe(CENSUS_DAY);
  });

  it("keeps every distinction the coverage track draws derivable from what it now sends", async () => {
    const { lane, capability } = await fireDetectionsCapability();
    const domain: SliderDomain = {
      firstDay: capability.earliestObservedDate!,
      today: CENSUS_DAY,
      lastDay: CENSUS_DAY,
    };

    // ABOVE the boundary nothing changed at all: a surviving absence day is still named as one,
    // in the sentence that separates "the source served nothing" from "we hold no record".
    const survivingAbsenceDay = lane.governedAbsenceRanges.at(-1)!.from;
    expect(isWithinGovernedAbsence(capability, survivingAbsenceDay)).toBe(true);
    expect(dayCoverageState(domain, capability, survivingAbsenceDay)).toBe("governed_absence");
    expect(describeDayCoverage("governed_absence")).toBe(
      "The source was checked and intentionally published no data on this date"
    );

    // A published day above the boundary still reads as dense, so the cap did not blur the axis
    // it still describes.
    expect(dayCoverageState(domain, capability, "2026-08-20")).toBe("dense");

    // BELOW the boundary, THE ONE INVERSION THIS CHANGE MUST NOT MAKE. A dropped absence day is
    // reported as undescribed -- never as dense, which on a fire map would state that the lane
    // was checked and burned nothing on a day it was never asked about.
    const droppedAbsenceDay = lane.governedAbsenceRanges[0].from;
    expect(isWithinGovernedAbsence(capability, droppedAbsenceDay)).toBe(false);
    expect(isDayDescribed(capability, droppedAbsenceDay)).toBe(false);
    expect(dayCoverageState(domain, capability, droppedAbsenceDay)).toBe("undescribed");
    expect(describeDayCoverage("undescribed")).toBe(
      "Coverage on this date is unknown; the available record does not describe it"
    );

    // And a PUBLISHED day below the boundary reads undescribed too, which is the cost stated
    // rather than hidden: the boundary bounds the whole report, not just the dropped ranges.
    expect(dayCoverageState(domain, capability, "2001-01-15")).toBe("undescribed");
  });

  it("leaves a lane at the cap byte-identical, and moves no boundary for it", async () => {
    // Exactly `MAX_REPORTED_GOVERNED_ABSENCE_RANGES` one-day absences: the largest list that must
    // survive untouched, so the cap is pinned at `<=` and cannot drift to `<`.
    const governedAbsenceRanges = Array.from({ length: 112 }, (_unused, index) => {
      const day = calendarDay(epochDay("2025-01-01") + index * 3);
      return { from: day, to: day };
    });
    setCoverage(withLane(completeCoverage(), "vegetation", { governedAbsenceRanges }));

    const result = await getParquetSliderCapabilities();
    const vegetation = result.layers.find((layer) => layer.layerName === "vegetation");

    expect(vegetation?.governedAbsenceRanges).toEqual(governedAbsenceRanges);
    // Untruncated, so the row still describes its whole axis and the track paints no
    // undescribed region. The only boundary a layer like this can have is its own gap list's.
    expect(vegetation?.describedFromDay).toBeNull();
    expect(vegetation?.coverageGapsDescribedFromDay).toBeNull();
  });

  it("truncates and reports the boundary one range past the cap", async () => {
    const governedAbsenceRanges = Array.from({ length: 113 }, (_unused, index) => {
      const day = calendarDay(epochDay("2025-01-01") + index * 3);
      return { from: day, to: day };
    });
    setCoverage(withLane(completeCoverage(), "vegetation", { governedAbsenceRanges }));

    const result = await getParquetSliderCapabilities();
    const vegetation = result.layers.find((layer) => layer.layerName === "vegetation");

    expect(vegetation?.governedAbsenceRanges).toEqual(governedAbsenceRanges.slice(1));
    expect(vegetation?.describedFromDay).toBe(governedAbsenceRanges[1].from);
  });
});
