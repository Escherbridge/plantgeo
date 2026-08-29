import {
  getGeoFeatureSliderCapabilities,
  MAX_REPORTED_DAY_RANGES,
  type ResolvedSliderCapabilities,
  type ResolvedSliderLayerCapability,
} from "@/lib/server/services/environmental-read-model";
import {
  getParquetWarehouseCoverage,
  type ParquetLaneCoverage,
  type ParquetLaneNature,
} from "@/lib/server/services/parquet-plane-client";
import {
  CLIMATE_FIELD_SIGNAL_IDS,
  climateFieldStreamName,
  type ClimateFieldSignalId,
} from "@/lib/environmental/climate-field";
import type { ZoomTier } from "@/lib/map/zoom-tiers";
import { SLIDER_STREAM_LAYER_NAMES, type DayRange, type TemporalKind } from "@/types/time-slider";

const REQUIRED_ZOOM_TIERS = [0, 5, 9, 13] as const satisfies readonly ZoomTier[];

/** Why a Parquet-owned slider row is deliberately absent from the public census. */
export type WithheldParquetCapabilityReason =
  | "coverage_not_current"
  | "reader_not_parquet"
  | "lane_not_registered"
  | "lane_never_written"
  | "rung_not_reported"
  | "rung_never_written"
  | "lane_nature_mismatch"
  | "invalid_rung_bounds"
  | "no_common_readable_history";

export interface MissingParquetCapabilityEvidence {
  parquetLane: string;
  zoomTier: ZoomTier | null;
}

export interface WithheldParquetCapability {
  layerName: string;
  parquetLanes: string[];
  reason: WithheldParquetCapabilityReason;
  missingEvidence: MissingParquetCapabilityEvidence[];
}

/** The ordinary slider payload plus auditable evidence for fail-closed Parquet omissions. */
export interface ParquetSliderCapabilities extends ResolvedSliderCapabilities {
  parquetCoverageGeneratedAt: string;
  parquetCoverageEvaluatedThroughDay: string;
  withheldParquetCapabilities: WithheldParquetCapability[];
}

interface ParquetCapabilityContract {
  layerName: string;
  temporalKind: TemporalKind;
  parquetNature: ParquetLaneNature;
  servingReader: "parquet" | "postgresql";
  parquetLanes: readonly string[];
  /** First day the lane contract promises as a usable series, even when older physical facts exist. */
  selectableHistoryFloor?: string;
}

const DIRECT_PARQUET_CAPABILITIES = [
  { layerName: SLIDER_STREAM_LAYER_NAMES.drought, temporalKind: "daily_series", parquetNature: "release_series", servingReader: "parquet", parquetLanes: ["drought"] },
  { layerName: "fire-detections", temporalKind: "event", parquetNature: "daily_series", servingReader: "parquet", parquetLanes: ["fire-detections"] },
  { layerName: "fire-perimeters", temporalKind: "event", parquetNature: "daily_series", servingReader: "postgresql", parquetLanes: ["fire-perimeters"] },
  {
    layerName: "water-gauges",
    temporalKind: "daily_series",
    parquetNature: "daily_series",
    servingReader: "parquet",
    parquetLanes: ["water-gauges"],
    selectableHistoryFloor: "2022-08-05",
  },
  { layerName: "weather-observations", temporalKind: "daily_series", parquetNature: "daily_series", servingReader: "parquet", parquetLanes: ["weather-observations"] },
  { layerName: "sensors", temporalKind: "snapshot", parquetNature: "daily_series", servingReader: "postgresql", parquetLanes: ["sensors"] },
  { layerName: "watersheds", temporalKind: "snapshot", parquetNature: "static_lookup", servingReader: "postgresql", parquetLanes: ["watersheds"] },
  { layerName: "vegetation", temporalKind: "daily_series", parquetNature: "daily_series", servingReader: "parquet", parquetLanes: ["vegetation"] },
  { layerName: "soil-survey", temporalKind: "snapshot", parquetNature: "static_lookup", servingReader: "postgresql", parquetLanes: ["soil-survey"] },
  { layerName: "evacuation-zones", temporalKind: "snapshot", parquetNature: "static_lookup", servingReader: "postgresql", parquetLanes: ["evacuation-zones"] },
  { layerName: "burn-severity", temporalKind: "event", parquetNature: "release_series", servingReader: "postgresql", parquetLanes: ["burn-severity"] },
] as const satisfies readonly ParquetCapabilityContract[];

const CLIMATE_PARQUET_LANES = {
  "air-temperature": [
    "climate-field-air-temperature-mean",
    "climate-field-air-temperature-max",
    "climate-field-air-temperature-min",
  ],
  "dew-point": ["climate-field-dew-point"],
  precipitation: ["climate-field-precipitation"],
  "relative-humidity": ["climate-field-relative-humidity"],
  "shortwave-radiation": ["climate-field-shortwave-radiation"],
  "wind-speed": ["climate-field-wind-speed"],
  "soil-wetness-surface": ["soil-wetness-surface"],
  "soil-wetness-root-zone": ["soil-wetness-root-zone"],
  "soil-wetness-profile": ["soil-wetness-profile"],
} as const satisfies Readonly<Record<ClimateFieldSignalId, readonly string[]>>;

const SIGNAL_PARQUET_CAPABILITIES = [
  {
    layerName: SLIDER_STREAM_LAYER_NAMES.soilMoisture,
    temporalKind: "daily_series",
    parquetNature: "daily_series",
    servingReader: "parquet",
    parquetLanes: [
      "soil-field-moisture-0-7cm",
      "soil-field-moisture-7-28cm",
      "soil-field-moisture-28-100cm",
    ],
  },
  {
    layerName: SLIDER_STREAM_LAYER_NAMES.soilTemperature,
    temporalKind: "daily_series",
    parquetNature: "daily_series",
    servingReader: "parquet",
    parquetLanes: [
      "soil-temperature-0-to-7cm",
      "soil-temperature-7-to-28cm",
      "soil-temperature-28-to-100cm",
      "soil-temperature-100-to-255cm",
    ],
  },
  {
    layerName: SLIDER_STREAM_LAYER_NAMES.soilVapourPressureDeficit,
    temporalKind: "daily_series",
    parquetNature: "daily_series",
    servingReader: "parquet",
    parquetLanes: ["soil-field-vpd"],
  },
  ...CLIMATE_FIELD_SIGNAL_IDS.map(
    (signal): ParquetCapabilityContract => ({
      layerName: climateFieldStreamName(signal),
      temporalKind: "daily_series",
      parquetNature: "daily_series",
      servingReader: "parquet",
      parquetLanes: CLIMATE_PARQUET_LANES[signal],
    })
  ),
] as const satisfies readonly ParquetCapabilityContract[];

/** Complete client-catalogue ownership table; a migrated row absent here would fall back to PostgreSQL. */
export const PARQUET_CAPABILITY_CONTRACTS = [
  ...DIRECT_PARQUET_CAPABILITIES,
  ...SIGNAL_PARQUET_CAPABILITIES,
] as const satisfies readonly ParquetCapabilityContract[];

const PARQUET_CAPABILITY_NAMES = new Set(
  PARQUET_CAPABILITY_CONTRACTS.map((contract) => contract.layerName)
);

const POSTGRES_CAPABILITY_PASSTHROUGH_NAMES = new Set(["burn-severity"]);

interface CapabilityProof {
  capability: ResolvedSliderLayerCapability | null;
  withheld: WithheldParquetCapability | null;
}

function evidenceKey(layer: string, zoomTier: ZoomTier): string {
  return `${layer}\u0000${zoomTier}`;
}

function buildEvidenceIndex(coverage: readonly ParquetLaneCoverage[]): Map<string, ParquetLaneCoverage[]> {
  const index = new Map<string, ParquetLaneCoverage[]>();
  for (const entry of coverage) {
    const key = evidenceKey(entry.layer, entry.zoomTier);
    const held = index.get(key);
    if (held === undefined) index.set(key, [entry]);
    else held.push(entry);
  }
  return index;
}

function missing(
  contract: ParquetCapabilityContract,
  reason: WithheldParquetCapabilityReason,
  missingEvidence: MissingParquetCapabilityEvidence[]
): CapabilityProof {
  return {
    capability: null,
    withheld: {
      layerName: contract.layerName,
      parquetLanes: [...contract.parquetLanes],
      reason,
      missingEvidence,
    },
  };
}

function intersectingBounds(entries: readonly ParquetLaneCoverage[]): {
  earliestDay: string;
  latestDay: string;
} | null {
  const earliestDay = entries.reduce(
    (latest, entry) => (entry.earliestDay! > latest ? entry.earliestDay! : latest),
    entries[0].earliestDay!
  );
  const latestDay = entries.reduce(
    (earliest, entry) => (entry.latestDay! < earliest ? entry.latestDay! : earliest),
    entries[0].latestDay!
  );
  return earliestDay <= latestDay ? { earliestDay, latestDay } : null;
}

function epochDay(day: string): number {
  const milliseconds = Date.parse(`${day}T00:00:00Z`);
  if (!Number.isFinite(milliseconds)) throw new Error(`invalid calendar day ${day}`);
  return milliseconds / 86_400_000;
}

function calendarDay(epoch: number): string {
  return new Date(epoch * 86_400_000).toISOString().slice(0, 10);
}

function mergeDayRanges(
  ranges: readonly DayRange[],
  firstDay: string,
  lastDay: string
): DayRange[] {
  const clipped = ranges
    .map((range) => ({
      from: range.from < firstDay ? firstDay : range.from,
      to: range.to > lastDay ? lastDay : range.to,
    }))
    .filter((range) => range.from <= range.to)
    .sort((left, right) => left.from.localeCompare(right.from) || left.to.localeCompare(right.to));
  const merged: DayRange[] = [];
  for (const range of clipped) {
    const previous = merged.at(-1);
    if (previous === undefined || epochDay(range.from) > epochDay(previous.to) + 1) {
      merged.push({ ...range });
    } else if (range.to > previous.to) {
      previous.to = range.to;
    }
  }
  return merged;
}

function mergeRanges(
  entries: readonly ParquetLaneCoverage[],
  field: "publishedRanges" | "gapRanges" | "governedAbsenceRanges",
  firstDay: string,
  lastDay: string
): DayRange[] {
  return mergeDayRanges(
    entries.flatMap((entry) => entry[field]),
    firstDay,
    lastDay
  );
}

/** Exact-day rungs are unavailable after their own immutable last written day. */
function coverageTailRanges(
  entries: readonly ParquetLaneCoverage[],
  evaluatedThroughDay: string
): DayRange[] {
  return entries.flatMap((entry) => {
    if (
      entry.nature !== "daily_series" ||
      entry.latestDay === null ||
      entry.latestDay >= evaluatedThroughDay
    ) {
      return [];
    }
    return [
      {
        from: calendarDay(epochDay(entry.latestDay) + 1),
        to: evaluatedThroughDay,
      },
    ];
  });
}

function dayCount(firstDay: string, lastDay: string, excluded: readonly DayRange[]): number {
  const total = epochDay(lastDay) - epochDay(firstDay) + 1;
  const excludedDays = excluded.reduce(
    (count, range) => count + epochDay(range.to) - epochDay(range.from) + 1,
    0
  );
  return Math.max(0, total - excludedDays);
}

function intersectRangeSets(left: readonly DayRange[], right: readonly DayRange[]): DayRange[] {
  const intersections: DayRange[] = [];
  let leftIndex = 0;
  let rightIndex = 0;
  while (leftIndex < left.length && rightIndex < right.length) {
    const from = left[leftIndex].from > right[rightIndex].from ? left[leftIndex].from : right[rightIndex].from;
    const to = left[leftIndex].to < right[rightIndex].to ? left[leftIndex].to : right[rightIndex].to;
    if (from <= to) intersections.push({ from, to });
    if (left[leftIndex].to < right[rightIndex].to) leftIndex += 1;
    else rightIndex += 1;
  }
  return intersections;
}

/** Remove lower-precedence evidence wherever a stronger range overlaps it. */
function subtractRangeSets(ranges: readonly DayRange[], excluded: readonly DayRange[]): DayRange[] {
  const remaining: DayRange[] = [];
  for (const range of ranges) {
    let cursor = epochDay(range.from);
    const last = epochDay(range.to);
    for (const exclusion of excluded) {
      const exclusionFirst = epochDay(exclusion.from);
      const exclusionLast = epochDay(exclusion.to);
      if (exclusionLast < cursor) continue;
      if (exclusionFirst > last) break;
      if (exclusionFirst > cursor) {
        remaining.push({
          from: calendarDay(cursor),
          to: calendarDay(exclusionFirst - 1),
        });
      }
      cursor = Math.max(cursor, exclusionLast + 1);
      if (cursor > last) break;
    }
    if (cursor <= last) {
      remaining.push({
        from: calendarDay(cursor),
        to: range.to,
      });
    }
  }
  return remaining;
}

function commonPublishedRanges(
  entries: readonly ParquetLaneCoverage[],
  earliestDay: string,
  latestDay: string
): DayRange[] {
  return entries.reduce<DayRange[]>((common, entry) => {
    const published = mergeRanges([entry], "publishedRanges", earliestDay, latestDay);
    return intersectRangeSets(common, published);
  }, [{ from: earliestDay, to: latestDay }]);
}

function synthesizeCapability(
  contract: ParquetCapabilityContract,
  entries: readonly ParquetLaneCoverage[],
  earliestRecordedDay: string,
  earliestDay: string,
  latestDay: string,
  serverCurrentDate: string,
  publishedRanges: readonly DayRange[],
  excludedPublishedDayCount: number
): ResolvedSliderLayerCapability {
  const coverageEnd = serverCurrentDate < latestDay ? latestDay : serverCurrentDate;
  const allCoverageGaps = mergeDayRanges(
    [
      ...entries.flatMap((entry) => entry.gapRanges),
      ...coverageTailRanges(entries, coverageEnd),
    ],
    earliestDay,
    coverageEnd
  );
  const governedAbsenceRanges = subtractRangeSets(
    mergeRanges(entries, "governedAbsenceRanges", earliestDay, coverageEnd),
    allCoverageGaps
  );
  const coverageGapsTruncated = allCoverageGaps.length > MAX_REPORTED_DAY_RANGES;
  const coverageGaps = coverageGapsTruncated
    ? allCoverageGaps.slice(-MAX_REPORTED_DAY_RANGES)
    : allCoverageGaps;
  const coverageGapsDescribedFromDay = coverageGapsTruncated ? coverageGaps[0].from : null;
  return {
    layerName: contract.layerName,
    temporalKind: contract.temporalKind,
    forecastHorizonDays: 0,
    forecastVariants: [],
    earliestObservedDate: earliestDay,
    latestObservedDate: latestDay,
    coverageGaps,
    governedAbsenceRanges,
    thinRanges: [],
    describedFromDay: coverageGapsDescribedFromDay,
    coverageGapsTruncated,
    coverageGapsDescribedFromDay,
    thinRangesTruncated: false,
    thinRangesDescribedFromDay: null,
    earliestObservedDateRule:
      earliestRecordedDay < earliestDay ? "gap_clustered" : "warehouse_coverage",
    earliestRecordedObservationDate: earliestRecordedDay,
    earliestContinuousObservationDate: publishedRanges.at(-1)?.from ?? null,
    latestRecordedObservationDate: latestDay,
    observedDayCount: publishedRanges.reduce(
      (count, range) => count + dayCount(range.from, range.to, []),
      0
    ),
    excludedObservedDayCount: excludedPublishedDayCount,
    gapExcludedObservedDayCount: excludedPublishedDayCount,
    densityExcludedObservedDayCount: 0,
    minimumDailyObservationCount: null,
  };
}

function proveCapability(
  contract: ParquetCapabilityContract,
  coverage: readonly ParquetLaneCoverage[],
  evidence: ReadonlyMap<string, ParquetLaneCoverage[]>,
  serverCurrentDate: string
): CapabilityProof {
  const reportedLanes = new Set(coverage.map((entry) => entry.layer));
  const unregistered = contract.parquetLanes.filter((lane) => !reportedLanes.has(lane));
  if (unregistered.length > 0) {
    return missing(
      contract,
      "lane_not_registered",
      unregistered.map((parquetLane) => ({ parquetLane, zoomTier: null }))
    );
  }

  const neverWritten = contract.parquetLanes.filter((lane) =>
    REQUIRED_ZOOM_TIERS.every((tier) => {
      const entry = evidence.get(evidenceKey(lane, tier));
      return entry?.length === 1 && entry[0].earliestDay === null && entry[0].latestDay === null;
    })
  );
  if (neverWritten.length > 0) {
    return missing(
      contract,
      "lane_never_written",
      neverWritten.map((parquetLane) => ({ parquetLane, zoomTier: null }))
    );
  }

  const exactEntries: ParquetLaneCoverage[] = [];
  const unreportedRungs: MissingParquetCapabilityEvidence[] = [];
  for (const parquetLane of contract.parquetLanes) {
    for (const zoomTier of REQUIRED_ZOOM_TIERS) {
      const entries = evidence.get(evidenceKey(parquetLane, zoomTier));
      if (entries?.length !== 1) unreportedRungs.push({ parquetLane, zoomTier });
      else exactEntries.push(entries[0]);
    }
  }
  if (unreportedRungs.length > 0) return missing(contract, "rung_not_reported", unreportedRungs);

  const emptyRungs = exactEntries
    .filter((entry) => entry.earliestDay === null && entry.latestDay === null)
    .map((entry) => ({ parquetLane: entry.layer, zoomTier: entry.zoomTier }));
  if (emptyRungs.length > 0) return missing(contract, "rung_never_written", emptyRungs);

  const natureMismatches = exactEntries
    .filter((entry) => entry.nature !== contract.parquetNature)
    .map((entry) => ({ parquetLane: entry.layer, zoomTier: entry.zoomTier }));
  if (natureMismatches.length > 0) {
    return missing(contract, "lane_nature_mismatch", natureMismatches);
  }

  const invalidRungs = exactEntries
    .filter(
      (entry) =>
        entry.kind !== "observed" ||
        entry.earliestDay === null ||
        entry.latestDay === null ||
        entry.earliestDay > entry.latestDay
    )
    .map((entry) => ({ parquetLane: entry.layer, zoomTier: entry.zoomTier }));
  if (invalidRungs.length > 0) return missing(contract, "invalid_rung_bounds", invalidRungs);

  const bounds = intersectingBounds(exactEntries);
  if (bounds === null) return missing(contract, "no_common_readable_history", []);
  const recordedPublishedRanges = commonPublishedRanges(
    exactEntries,
    bounds.earliestDay,
    bounds.latestDay
  );
  const boundedEarliestDay =
    contract.selectableHistoryFloor !== undefined &&
    contract.selectableHistoryFloor > bounds.earliestDay
      ? contract.selectableHistoryFloor
      : bounds.earliestDay;
  if (boundedEarliestDay > bounds.latestDay) {
    return missing(contract, "no_common_readable_history", []);
  }
  const publishedRanges = commonPublishedRanges(
    exactEntries,
    boundedEarliestDay,
    bounds.latestDay
  );
  if (publishedRanges.length === 0) {
    return missing(contract, "no_common_readable_history", []);
  }
  if (contract.servingReader !== "parquet") {
    return missing(contract, "reader_not_parquet", []);
  }
  const earliestDay = publishedRanges[0].from;
  const latestDay = publishedRanges.at(-1)!.to;
  const recordedPublishedDayCount = recordedPublishedRanges.reduce(
    (count, range) => count + dayCount(range.from, range.to, []),
    0
  );
  const selectablePublishedDayCount = publishedRanges.reduce(
    (count, range) => count + dayCount(range.from, range.to, []),
    0
  );
  return {
    capability: synthesizeCapability(
      contract,
      exactEntries,
      bounds.earliestDay,
      earliestDay,
      latestDay,
      serverCurrentDate,
      publishedRanges,
      recordedPublishedDayCount - selectablePublishedDayCount
    ),
    withheld: null,
  };
}

/**
 * Repoints the public census to exact Parquet product/rung evidence without a PostgreSQL fallback.
 * See `src/lib/server/AGENTS.md` section "Parquet tRPC cutover".
 */
export async function getParquetSliderCapabilities(): Promise<ParquetSliderCapabilities> {
  // Coverage comes first: its fault must never fall through to the retired PostgreSQL rows.
  const coverage = await getParquetWarehouseCoverage();
  const postgresCapabilities = await getGeoFeatureSliderCapabilities();
  const evidence = buildEvidenceIndex(coverage.lanes);
  const proofs = PARQUET_CAPABILITY_CONTRACTS.filter(
    (contract) => !POSTGRES_CAPABILITY_PASSTHROUGH_NAMES.has(contract.layerName)
  ).map((contract) =>
    coverage.evaluatedThroughDay === postgresCapabilities.serverCurrentDate
      ? proveCapability(contract, coverage.lanes, evidence, postgresCapabilities.serverCurrentDate)
      : missing(contract, "coverage_not_current", [])
  );

  return {
    ...postgresCapabilities,
    layers: [
      ...postgresCapabilities.layers.filter(
        (layer) =>
          !PARQUET_CAPABILITY_NAMES.has(layer.layerName) ||
          POSTGRES_CAPABILITY_PASSTHROUGH_NAMES.has(layer.layerName)
      ),
      ...proofs.flatMap((proof) => (proof.capability === null ? [] : [proof.capability])),
    ],
    // Every PostgreSQL stream row is Parquet-owned above; its retired scan cannot remount one.
    streamsUnavailable: false,
    parquetCoverageGeneratedAt: coverage.generatedAt,
    parquetCoverageEvaluatedThroughDay: coverage.evaluatedThroughDay,
    withheldParquetCapabilities: proofs.flatMap((proof) =>
      proof.withheld === null ? [] : [proof.withheld]
    ),
  };
}

/** Physical lanes this adapter owns, exported for reference-scan tests and cutover reporting. */
export const PARQUET_CAPABILITY_LANES = [
  ...new Set(PARQUET_CAPABILITY_CONTRACTS.flatMap((contract) => contract.parquetLanes)),
] as const;
