import {
  getGeoFeatureSliderCapabilities,
  MAX_REPORTED_DAY_RANGES,
  type ResolvedSliderCapabilities,
  type ResolvedSliderLayerCapability,
} from "@/lib/server/services/environmental-read-model";
import {
  getParquetWarehouseCoverage,
  PARQUET_AVAILABILITY_WITHHELD_REASONS,
  ParquetPlaneContractError,
  type ParquetAvailabilityWithheldReason,
  type ParquetCoverageAuthority,
  type ParquetLaneCoverage,
  type ParquetLaneNature,
} from "@/lib/server/services/parquet-plane-client";
import {
  UpstreamConfigurationError,
  UpstreamHttpError,
  UpstreamPayloadError,
  UpstreamTimeoutError,
} from "@/lib/server/http/bounded-upstream";
import {
  CLIMATE_FIELD_SIGNAL_IDS,
  climateFieldStreamName,
  type ClimateFieldSignalId,
} from "@/lib/environmental/climate-field";
import type { ZoomTier } from "@/lib/map/zoom-tiers";
import { SLIDER_STREAM_LAYER_NAMES, type DayRange, type TemporalKind } from "@/types/time-slider";

const REQUIRED_ZOOM_TIERS = [0, 5, 9, 13] as const satisfies readonly ZoomTier[];

/**
 * Why a Parquet-owned slider row is deliberately absent from the public census.
 *
 * The four `availability_*` members are the wire's own `withheld_reason` spellings, carried
 * through unchanged rather than translated: a mapping table between two enums that mean the same
 * thing is a place for the two to drift, and an operator reading this list should be able to
 * grep the serving side for the identical string.
 */
export type WithheldParquetCapabilityReason =
  | "coverage_unavailable"
  | "coverage_not_current"
  | "reader_not_parquet"
  | "lane_not_registered"
  | "lane_never_written"
  | "rung_not_reported"
  | "rung_never_written"
  | "lane_nature_mismatch"
  | "invalid_rung_bounds"
  | "no_common_readable_history"
  | "availability_unpublished"
  | "availability_stale"
  | "availability_malformed"
  | "availability_checksum_invalid"
  /** A rung holds a day past its own source's ceiling; see `ParquetLaneCoverage.sourceCeilingDay`. */
  | "ceiling_violation";

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
  parquetCoverageGeneratedAt: string | null;
  parquetCoverageEvaluatedThroughDay: string | null;
  parquetCoverageUnavailable: boolean;
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
  { layerName: "fire-perimeters", temporalKind: "event", parquetNature: "static_lookup", servingReader: "parquet", parquetLanes: ["fire-perimeters"] },
  {
    layerName: "water-gauges",
    temporalKind: "daily_series",
    parquetNature: "daily_series",
    servingReader: "parquet",
    parquetLanes: ["water-gauges"],
    selectableHistoryFloor: "2022-08-05",
  },
  { layerName: "weather-observations", temporalKind: "daily_series", parquetNature: "daily_series", servingReader: "parquet", parquetLanes: ["weather-observations"] },
  // sensors / watersheds / evacuation-zones flipped to "parquet" on 2026-09-07. Their RENDER paths
  // left Postgres on 2026-09-04 (wave C) and this line was never updated behind them, so all three
  // were withheld as `reader_not_parquet` while drawing Parquet pixels. Two independent proofs:
  // each resolves to a `getParquet*` reader (`parquet-trpc-readers.ts:2200/2063/1923`) onto a GeoJSON
  // source `LayerManager.applyParquetFeatureData` fills, and Martin publishes only
  // `intervention_tiles`/`building_tiles` (`infra/martin/martin.yaml:65-71`), so no tile function
  // serves them. The production reason itself was the third proof: `reader_not_parquet` is the LAST
  // check in `proveCapability`, so each had already passed availability, four-rung reporting, nature,
  // bounds and ceiling.
  //
  // WHAT THIS DOES NOT DO: all three are `temporalKind: "snapshot"`, so `sliderDomain` returns null
  // (`stores/time-slider-store.ts:105`) and none gains a scrubber. What changes is that they stop
  // being withheld, and `resolveLayerDate` starts using the lane's own `latestObservedDate` instead
  // of falling back to today -- which is a fix, not a cosmetic one.
  { layerName: "sensors", temporalKind: "snapshot", parquetNature: "daily_series", servingReader: "parquet", parquetLanes: ["sensors"] },
  { layerName: "watersheds", temporalKind: "snapshot", parquetNature: "static_lookup", servingReader: "parquet", parquetLanes: ["watersheds"] },
  { layerName: "vegetation", temporalKind: "daily_series", parquetNature: "daily_series", servingReader: "parquet", parquetLanes: ["vegetation"] },
  // soil-survey STAYS postgresql, and not because the line is stale: the lane has never written an
  // object (it is withheld as `lane_never_written`, never reaching the reader gate), and
  // `services/usda-soil.ts` both READS `geo.features`/`geo.soil_survey_coverage` and WRITES them on
  // its SDA warm path. Flipping it would advertise a Parquet axis over Postgres polygons -- the exact
  // inversion this gate exists to prevent.
  { layerName: "soil-survey", temporalKind: "snapshot", parquetNature: "static_lookup", servingReader: "postgresql", parquetLanes: ["soil-survey"] },
  { layerName: "evacuation-zones", temporalKind: "snapshot", parquetNature: "static_lookup", servingReader: "parquet", parquetLanes: ["evacuation-zones"] },
  // burn-severity flipped to "parquet" on 2026-09-07: the last row in this table whose axis and
  // pixels disagreed, and the only one that ran the inversion in the direction `servingReader`
  // cannot catch. The gate withholds a Parquet AXIS over a PostgreSQL population; this row was a
  // PostgreSQL axis over a Parquet population, which no check here refuses -- it was simply exempt.
  // Its render path has been Parquet since the reader cutover (`trpc/routers/environmental.ts:419`
  // -> `parquet-trpc-readers.ts:2122` `getParquetBurnSeverity` -> the `burn-severity-features`
  // GeoJSON source at `lib/map/layers.ts:314-317`, filled by `LayerManager.applyParquetFeatureData`)
  // and Martin publishes no burn-severity tile function (`infra/martin/martin.yaml:65-71`), so
  // nothing else was ever drawing it.
  //
  // WHAT THIS DOES DO, unlike the three snapshot rows above: `temporalKind: "event"` means
  // `sliderDomain` returns a real domain (`stores/time-slider-store.ts`) and
  // `lib/map/tile-layer-date-filter.ts:21` applies a real style filter, so the synthesized axis is
  // user-visible on every scrub. `restoreCumulativeBurnHistory` is what keeps it honest against a
  // reader that unions every release at or before the viewed day.
  { layerName: "burn-severity", temporalKind: "event", parquetNature: "release_series", servingReader: "parquet", parquetLanes: ["burn-severity"] },
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

function isCoverageBoundaryFault(error: unknown): boolean {
  return (
    error instanceof UpstreamConfigurationError ||
    error instanceof UpstreamHttpError ||
    error instanceof UpstreamPayloadError ||
    error instanceof UpstreamTimeoutError ||
    error instanceof ParquetPlaneContractError ||
    (error instanceof TypeError && error.message.includes("fetch failed"))
  );
}

/**
 * MTBS is a sparse cumulative event reader, so every day after its first event is selectable.
 *
 * `getParquetBurnSeverity` (`parquet-trpc-readers.ts:2122`) walks BACK through releases and unions
 * every one dated at or before the requested day, so a day between two releases draws the older
 * release in full. The evidence the census publishes says the opposite -- burn-severity has roughly
 * five real release days across 2015-2026 -- so an axis synthesized straight from it would mark
 * nearly the whole span as `coverageGaps` and the scrubber would forbid days the renderer handles
 * perfectly. That is the one direction of error this module must never produce, so the rewrite
 * follows the reader across the cutover rather than staying behind on the PostgreSQL path.
 *
 * Applied at exactly ONE site -- `proveCapability`'s single capability return, which is also the
 * only producer of a non-null `CapabilityProof.capability` -- because the
 * `observedDayCount + excludedObservedDayCount` fold below is not idempotent. The layer-name guard
 * is what makes that one shared site a no-op for every other row.
 */
function restoreCumulativeBurnHistory(
  layer: ResolvedSliderLayerCapability
): ResolvedSliderLayerCapability {
  if (layer.layerName !== "burn-severity") return layer;
  const earliest = layer.earliestRecordedObservationDate ?? layer.earliestObservedDate;
  if (earliest === null) return layer;
  return {
    ...layer,
    earliestObservedDate: earliest,
    coverageGaps: [],
    governedAbsenceRanges: [],
    thinRanges: [],
    describedFromDay: null,
    // Both boundaries cleared together: this reader restates the whole axis as described, and a
    // retained upper bound would leave the cumulative history described at one end only.
    describedThroughDay: null,
    coverageGapsTruncated: false,
    coverageGapsDescribedFromDay: null,
    thinRangesTruncated: false,
    thinRangesDescribedFromDay: null,
    earliestObservedDateRule: "full_history",
    earliestContinuousObservationDate: earliest,
    observedDayCount: layer.observedDayCount + layer.excludedObservedDayCount,
    excludedObservedDayCount: 0,
    gapExcludedObservedDayCount: 0,
    densityExcludedObservedDayCount: 0,
    minimumDailyObservationCount: null,
  };
}

/**
 * The PostgreSQL rows that survive the cutover: exactly the layers this module owns no contract for.
 *
 * THE WITHHOLDING RULE, which is now structural rather than a per-name exception list. A
 * `POSTGRES_CAPABILITY_PASSTHROUGH_NAMES` set carried the one exception -- `burn-severity`, and
 * nothing else -- until its reader flipped to Parquet on 2026-09-07 and emptied it. The set was
 * deleted along with its four use sites rather than left empty, because an empty set states a rule
 * through branches that can no longer be taken, and a reader cannot tell a live exception with no
 * current members apart from a retired one:
 *
 * 1. **A Parquet-owned layer is proved from Parquet evidence or it has no row at all.** Not a
 *    census fallback, not the older PostgreSQL row -- either would answer a question the warehouse
 *    just declined to answer, in a form the client cannot tell apart from a proved one. This holds
 *    identically for a withheld availability index, an unproven contract, a stale census and a
 *    wholly unavailable one, and needs no bookkeeping to keep holding.
 * 2. **A layer with no Parquet contract is untouched.** `interventions` and any future
 *    PostgreSQL-only row are not claims about warehouse evidence, so nothing here withholds them --
 *    blanking a layer the census never claimed to describe would be its own false report.
 *
 * What (1) cost, stated rather than discovered: when the coverage plane is unavailable altogether,
 * burn-severity now loses its slider row with every other Parquet-owned layer instead of falling
 * back to a PostgreSQL axis. That is the point -- its pixels come from Parquet, so a retained
 * PostgreSQL axis would be exactly the inversion `servingReader` exists to prevent, drawn at the
 * moment the warehouse is least able to contradict it.
 */
function retainedPostgresCapabilities(
  capabilities: ResolvedSliderCapabilities
): ResolvedSliderLayerCapability[] {
  return capabilities.layers.filter((layer) => !PARQUET_CAPABILITY_NAMES.has(layer.layerName));
}

function unavailableCoverageProofs(): WithheldParquetCapability[] {
  return PARQUET_CAPABILITY_CONTRACTS.map((contract) => ({
    layerName: contract.layerName,
    parquetLanes: [...contract.parquetLanes],
    reason: "coverage_unavailable",
    missingEvidence: [],
  }));
}

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

/** Where a reason sits in the wire's declared order, which doubles as its precedence. */
function reasonPrecedence(reason: ParquetAvailabilityWithheldReason): number {
  return PARQUET_AVAILABILITY_WITHHELD_REASONS.indexOf(reason);
}

/**
 * The lanes the serving side refuses to describe, one reason each.
 *
 * Keyed by physical LANE and not by rung: a withheld availability index is a statement about the
 * lane's published evidence as a whole, so one rung reporting it withholds all four. Where rungs
 * disagree the earliest-declared reason wins, so the answer does not depend on lane ordering.
 */
function availabilityWithheldLanes(
  coverage: readonly ParquetLaneCoverage[]
): Map<string, ParquetAvailabilityWithheldReason> {
  const withheld = new Map<string, ParquetAvailabilityWithheldReason>();
  for (const entry of coverage) {
    if (entry.withheldReason === null) continue;
    const held = withheld.get(entry.layer);
    if (held === undefined || reasonPrecedence(entry.withheldReason) < reasonPrecedence(held)) {
      withheld.set(entry.layer, entry.withheldReason);
    }
  }
  return withheld;
}

/**
 * A capability whose lane withheld itself, or null when every one of its lanes is describable.
 *
 * Checked BEFORE any census fact and before the PostgreSQL passthrough, which is the whole point
 * of the fail-closed rule: an unpublished, stale, malformed or checksum-invalid availability index
 * says nothing about which days exist, so falling back to a census walk -- or to the PostgreSQL
 * row the layer used to be served from -- would answer a question the warehouse just declined to
 * answer, in a form the client cannot tell apart from a proved one.
 */
function availabilityWithholding(
  contract: ParquetCapabilityContract,
  withheldLanes: ReadonlyMap<string, ParquetAvailabilityWithheldReason>
): CapabilityProof | null {
  const withheld = contract.parquetLanes.flatMap((parquetLane) => {
    const reason = withheldLanes.get(parquetLane);
    return reason === undefined ? [] : [{ parquetLane, reason }];
  });
  const strongest = withheld.reduce<{ parquetLane: string; reason: ParquetAvailabilityWithheldReason } | null>(
    (held, entry) =>
      held === null || reasonPrecedence(entry.reason) < reasonPrecedence(held.reason) ? entry : held,
    null
  );
  if (strongest === null) return null;
  return missing(
    contract,
    strongest.reason,
    withheld.map((entry) => ({ parquetLane: entry.parquetLane, zoomTier: null }))
  );
}

/** A row is only as authoritative as its WEAKEST rung: one census walk makes the whole row a walk. */
function rowCoverageAuthority(
  entries: readonly ParquetLaneCoverage[]
): ParquetCoverageAuthority {
  return entries.every((entry) => entry.coverageAuthority === "availability")
    ? "availability"
    : "census";
}

/** The most binding ceiling across the rungs; null only when every one of them is unbounded. */
function rowSourceCeilingDay(entries: readonly ParquetLaneCoverage[]): string | null {
  return entries.reduce<string | null>(
    (lowest, entry) =>
      entry.sourceCeilingDay !== null && (lowest === null || entry.sourceCeilingDay < lowest)
        ? entry.sourceCeilingDay
        : lowest,
    null
  );
}

/**
 * Every rung the serving side declared required, deduplicated and ordered low to high; falls back
 * to the rungs this module actually gated on when the wire declared none.
 *
 * An empty list is not "no rungs were required" -- `REQUIRED_ZOOM_TIERS` is what `proveCapability`
 * enforced before this row could exist, and a wire that stated nothing does not relax it (see
 * `src/lib/server/services/AGENTS.md` §availability-authority: `requiredRungs` is a label, not a
 * gate). Publishing `[]` labelled a four-rung proof as an unconditional one, which is the one
 * reading of this field a client must never be able to take.
 */
function rowRequiredRungs(entries: readonly ParquetLaneCoverage[]): ZoomTier[] {
  const declared = [...new Set(entries.flatMap((entry) => entry.requiredRungs))].sort(
    (left, right) => left - right
  );
  return declared.length > 0 ? declared : [...REQUIRED_ZOOM_TIERS];
}

/**
 * A rung `proveCapability`'s `invalid_rung_bounds` check has already cleared, so both of its days
 * are known present.
 *
 * The narrowing exists so the readers below state that guarantee in the type instead of restating
 * it as a non-null assertion at each use. Six assertions meant six independent bets that a check
 * forty lines above still ran first; `hasReadableBounds` is the one place the bet is made.
 */
type BoundedLaneCoverage = ParquetLaneCoverage & { earliestDay: string; latestDay: string };

function hasReadableBounds(entry: ParquetLaneCoverage): entry is BoundedLaneCoverage {
  return entry.earliestDay !== null && entry.latestDay !== null;
}

function intersectingBounds(entries: readonly BoundedLaneCoverage[]): {
  earliestDay: string;
  latestDay: string;
} | null {
  const earliestDay = entries.reduce(
    (latest, entry) => (entry.earliestDay > latest ? entry.earliestDay : latest),
    entries[0].earliestDay
  );
  const latestDay = entries.reduce(
    (earliest, entry) => (entry.latestDay < earliest ? entry.latestDay : earliest),
    entries[0].latestDay
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

/**
 * Exact-day rungs are unavailable after their own immutable last written day, up to the day the
 * LANE's source can reach -- not up to today.
 *
 * `sourceCeilingDay` is the lane's own freshness horizon and `evaluatedThroughDay` is merely when
 * the census ran. Running the tail to the later of the two would report every day the upstream has
 * not published yet as an ingest hole, turning a lane that is correctly waiting on a weekly or
 * lagged source into one that reads as dead. The golden fixture is exactly this shape: a lane
 * holding through 2026-08-05 with a ceiling of 2026-08-07, censused on 2026-08-25 -- two owed
 * days, not twenty.
 */
function coverageTailRanges(
  entries: readonly ParquetLaneCoverage[],
  evaluatedThroughDay: string
): DayRange[] {
  return entries.flatMap((entry) => {
    const horizon =
      entry.sourceCeilingDay !== null && entry.sourceCeilingDay < evaluatedThroughDay
        ? entry.sourceCeilingDay
        : evaluatedThroughDay;
    if (entry.nature !== "daily_series" || entry.latestDay === null || entry.latestDay >= horizon) {
      return [];
    }
    return [
      {
        from: calendarDay(epochDay(entry.latestDay) + 1),
        to: horizon,
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

/**
 * How many governed-absence ranges one row publishes, newest first.
 *
 * THIS LIST IS PARQUET-ONLY, which is why it is capped here and `coverageGaps` is not. The
 * PostgreSQL read model has no governed-absence concept at all -- `buildCapability`
 * (`environmental-read-model.ts`) never sets the field, and `SliderLayerCapability` marks it
 * optional for exactly that reason -- so this module is its sole producer and the only place a
 * cap can live. `coverageGaps` has two producers and must keep the one shared
 * `MAX_REPORTED_DAY_RANGES`; a second, tighter cap here would make a layer's Parquet row and its
 * PostgreSQL row disagree about what "truncated" means.
 *
 * DERIVED FROM WHAT THE TRACK CAN DRAW, not from a byte target. `floorAxisRunsToBands`
 * (`components/map/layer-panel/layer-coverage-track.ts`) widens every run to
 * `MINIMUM_DRAWN_BAND_PERCENT` = 0.9% of the axis and coalesces any two same-kind bands that
 * then touch, so a track can never show more than `ceil(100 / 0.9)` = 112 marks of one kind
 * however many ranges it is sent. Past that the extra ranges buy no ahead-of-scrubbing
 * information at all: on fire-detections' 9,441-day axis 0.9% is 85 days, its 447 absence
 * ranges sit a mean 21 days apart, and every one of them coalesces into essentially a single
 * painted band -- 19,668 bytes, 47% of the whole 42,291-byte whole-warehouse payload, spent to
 * draw one band and to let `describeCoverageTopology` say "447 ranges" instead of "112".
 *
 * WHAT IS NOT LOST, and the reason a cap is honest rather than merely cheap. The distinction
 * this list exists to carry -- the source was checked and served nothing, versus we hold no
 * record -- is NOT carried only here. Every day read returns a `ParquetPlaneEnvelope` whose four
 * states name it exactly (`parquet-envelope.ts` `PARQUET_PLANE_STATES`), and the surfaces quote
 * it verbatim with the upstream's own reason attached: `LayerManager.tsx` "The fire lane
 * recorded a governed absence for this day: <reason>" against "This day has not been written for
 * the fire lane", and `FireDetails.tsx` the same sentence in the dock. That per-day answer is
 * strictly better evidence than a range list, because it carries the reason. This list's own job
 * is the view BEFORE any scrubbing -- where to look -- and that view is band-resolution.
 *
 * WHAT IS LOST, stated rather than discovered: below the boundary this cap publishes, a day is
 * `undescribed` on the track rather than named as a governed absence or drawn as dense. The
 * reader is told the report does not reach that far, scrubs onto the day anyway (nothing here
 * narrows the axis or forbids a day), and the read then answers exactly. That is the direction
 * `capDayRanges` already chose for the two lists it caps -- understating what we know is safe,
 * overstating it is the bug -- and it is why the boundary is folded into `describedFromDay`
 * below instead of the list being silently shortened.
 */
export const MAX_REPORTED_GOVERNED_ABSENCE_RANGES = 112;

/** A capped range list plus the oldest day the survivors still completely describe. */
interface CappedDayRanges {
  kept: DayRange[];
  /** null when nothing was dropped, so the list still describes the whole axis. */
  describedFromDay: string | null;
}

/**
 * Keeps the newest `limit` ranges and says which day the kept list still describes.
 *
 * The same policy as `capDayRanges` in `environmental-read-model.ts`, restated here because that
 * one is module-private: newest kept, because the right-hand edge of the axis is where people
 * scrub; boundary set to the oldest SURVIVING range's own start day rather than the newest
 * dropped range's end, because the survivors are complete only from there on and a day just
 * below it may sit inside a range that was dropped.
 *
 * No `truncated` flag is produced, and that is deliberate rather than an omission. There is no
 * `governedAbsenceRangesTruncated` slot on `ResolvedSliderLayerCapability` to fill, and the
 * lesson recorded on `MAX_REPORTED_DAY_RANGES` is that the flags "were computed, shipped and
 * read by nothing" while the DAY is the value a consumer has to consult. Publishing the day and
 * not a flag is that lesson applied, not skipped.
 */
function capNewestDayRanges(ranges: readonly DayRange[], limit: number): CappedDayRanges {
  if (ranges.length <= limit) return { kept: [...ranges], describedFromDay: null };
  const kept = ranges.slice(ranges.length - limit);
  return { kept, describedFromDay: kept[0].from };
}

/**
 * The later of two per-list boundaries; null only when BOTH lists describe their whole axis.
 *
 * Deliberately the same name as the private helper in `environmental-read-model.ts` that folds
 * `coverageGaps`' and `thinRanges`' boundaries together, so one grep finds both sites of one
 * rule: a day is described only when EVERY list describes it, so the stricter boundary wins.
 */
function laterDescribedFromDay(left: string | null, right: string | null): string | null {
  if (left === null) return right;
  if (right === null) return left;
  return left > right ? left : right;
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
  // The same bound `coverageTailRanges` clamps each tail to, published rather than kept private:
  // above it the tail asked no question, so `coverageGaps`' silence there is not a claim that the
  // days are dense. Without this the client reads the ceiling-to-today span as covered, which
  // inverts the very reason the tail stops at the ceiling.
  const describedThroughDay = rowSourceCeilingDay(entries) ?? coverageEnd;
  const allCoverageGaps = mergeDayRanges(
    [
      ...entries.flatMap((entry) => entry.gapRanges),
      ...coverageTailRanges(entries, coverageEnd),
    ],
    earliestDay,
    coverageEnd
  );
  // Capped AFTER the subtraction, never before: `subtractRangeSets` can split one governed range
  // into two around an overlapping gap, so a cap applied first would report a boundary for a
  // count the row does not publish.
  const governedAbsence = capNewestDayRanges(
    subtractRangeSets(
      mergeRanges(entries, "governedAbsenceRanges", earliestDay, coverageEnd),
      allCoverageGaps
    ),
    MAX_REPORTED_GOVERNED_ABSENCE_RANGES
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
    governedAbsenceRanges: governedAbsence.kept,
    thinRanges: [],
    // The three lists' boundaries folded into the one field every consumer consults
    // (`isDayDescribed`), exactly as the read model folds `coverageGaps`' and `thinRanges`'.
    // A truncated governed-absence list makes the OTHER lists' silence unusable below its
    // boundary too -- a day there is neither provably dense nor provably absent -- so the fold
    // is what keeps `dayCoverageState` from falling through to `dense` on a day this row just
    // stopped describing.
    describedFromDay: laterDescribedFromDay(
      coverageGapsDescribedFromDay,
      governedAbsence.describedFromDay
    ),
    describedThroughDay,
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
    // Published so the UI can say WHICH evidence it is drawing and what bounded it, rather than
    // presenting an object-store walk and a checksummed index as the same claim.
    coverageAuthority: rowCoverageAuthority(entries),
    sourceCeilingDay: rowSourceCeilingDay(entries),
    requiredRungs: rowRequiredRungs(entries),
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

  // Every rung has both days from here down, which the check above just proved and this filter
  // records in the type. Same membership, narrower type: nothing can be dropped here without
  // `invalid_rung_bounds` having returned already.
  const boundedEntries = exactEntries.filter(hasReadableBounds);

  // Withheld rather than clamped. A rung holding a day its own source cannot have published is
  // wrong about something -- a mislabelled partition, a clock skew, a forecast row in an observed
  // stream -- and a lane that disagrees with its source at the live edge has not earned belief on
  // the days below it either. Clamping would hide the disagreement behind a plausible axis.
  const beyondCeiling = boundedEntries
    .filter((entry) => entry.sourceCeilingDay !== null && entry.latestDay > entry.sourceCeilingDay)
    .map((entry) => ({ parquetLane: entry.layer, zoomTier: entry.zoomTier }));
  if (beyondCeiling.length > 0) return missing(contract, "ceiling_violation", beyondCeiling);

  const bounds = intersectingBounds(boundedEntries);
  if (bounds === null) return missing(contract, "no_common_readable_history", []);
  const recordedPublishedRanges = commonPublishedRanges(
    boundedEntries,
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
    boundedEntries,
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
    // The module's ONLY non-null capability, and therefore the only place a per-contract axis
    // rewrite can be applied once and only once. `synthesizeCapability` stays a pure translation of
    // rung evidence; a reader whose own semantics contradict that evidence is corrected here.
    capability: restoreCumulativeBurnHistory(
      synthesizeCapability(
        contract,
        boundedEntries,
        bounds.earliestDay,
        earliestDay,
        latestDay,
        serverCurrentDate,
        publishedRanges,
        recordedPublishedDayCount - selectablePublishedDayCount
      )
    ),
    withheld: null,
  };
}

/**
 * Repoints the public census to exact Parquet product/rung evidence without a PostgreSQL fallback.
 * See `src/lib/server/AGENTS.md` section "Parquet tRPC cutover".
 */
export async function getParquetSliderCapabilities(): Promise<ParquetSliderCapabilities> {
  const [coverageResult, postgresResult] = await Promise.allSettled([
    getParquetWarehouseCoverage(),
    getGeoFeatureSliderCapabilities(),
  ]);
  if (postgresResult.status === "rejected") throw postgresResult.reason;
  const postgresCapabilities = postgresResult.value;
  if (coverageResult.status === "rejected") {
    if (!isCoverageBoundaryFault(coverageResult.reason)) throw coverageResult.reason;
    console.error("Parquet slider coverage unavailable; withholding every Parquet-owned row", {
      error:
        coverageResult.reason instanceof Error
          ? coverageResult.reason.message
          : String(coverageResult.reason),
    });
    return {
      ...postgresCapabilities,
      layers: retainedPostgresCapabilities(postgresCapabilities),
      streamsUnavailable: false,
      parquetCoverageGeneratedAt: null,
      parquetCoverageEvaluatedThroughDay: null,
      parquetCoverageUnavailable: true,
      withheldParquetCapabilities: unavailableCoverageProofs(),
    };
  }
  const coverage = coverageResult.value;
  const evidence = buildEvidenceIndex(coverage.lanes);
  const withheldLanes = availabilityWithheldLanes(coverage.lanes);
  // Availability is asked FIRST, of every contract: a lane that withheld its index is withheld
  // everywhere, and `coverage_not_current` would report the whole-census reason for what is really
  // one lane's unpublished evidence.
  const proofs = PARQUET_CAPABILITY_CONTRACTS.map((contract) => {
    const withheld = availabilityWithholding(contract, withheldLanes);
    if (withheld !== null) return withheld;
    return coverage.evaluatedThroughDay === postgresCapabilities.serverCurrentDate
      ? proveCapability(contract, coverage.lanes, evidence, postgresCapabilities.serverCurrentDate)
      : missing(contract, "coverage_not_current", []);
  });

  return {
    ...postgresCapabilities,
    layers: [
      ...retainedPostgresCapabilities(postgresCapabilities),
      ...proofs.flatMap((proof) => (proof.capability === null ? [] : [proof.capability])),
    ],
    // Every PostgreSQL stream row is Parquet-owned above; its retired scan cannot remount one.
    streamsUnavailable: false,
    parquetCoverageGeneratedAt: coverage.generatedAt,
    parquetCoverageEvaluatedThroughDay: coverage.evaluatedThroughDay,
    parquetCoverageUnavailable: false,
    withheldParquetCapabilities: proofs.flatMap((proof) =>
      proof.withheld === null ? [] : [proof.withheld]
    ),
  };
}

/** Physical lanes this adapter owns, exported for reference-scan tests and cutover reporting. */
export const PARQUET_CAPABILITY_LANES = [
  ...new Set(PARQUET_CAPABILITY_CONTRACTS.flatMap((contract) => contract.parquetLanes)),
] as const;
