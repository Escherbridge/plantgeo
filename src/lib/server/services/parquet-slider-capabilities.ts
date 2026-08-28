import {
  getSliderCapabilities,
  type ResolvedSliderCapabilities,
} from "@/lib/server/services/environmental-read-model";
import { getParquetWarehouseCoverage } from "@/lib/server/services/parquet-plane-client";
import { CLIMATE_FIELD_SIGNAL_IDS, climateFieldStreamName } from "@/lib/environmental/climate-field";
import { SLIDER_STREAM_LAYER_NAMES } from "@/types/time-slider";

/** Why a Parquet-owned slider row is deliberately absent from the public census. */
export type WithheldParquetCapabilityReason =
  | "lane_not_registered"
  | "lane_never_written"
  | "per_rung_coverage_not_reported"
  | "signal_product_coverage_not_reported";

export interface WithheldParquetCapability {
  layerName: string;
  parquetLane: string;
  reason: WithheldParquetCapabilityReason;
}

/** The ordinary slider payload plus auditable evidence for fail-closed Parquet omissions. */
export interface ParquetSliderCapabilities extends ResolvedSliderCapabilities {
  parquetCoverageGeneratedAt: string;
  withheldParquetCapabilities: WithheldParquetCapability[];
}

const DIRECT_PARQUET_LANES = [
  "drought",
  "fire-detections",
  "vegetation",
  "water-gauges",
  "weather-observations",
] as const;

const SIGNAL_CAPABILITY_NAMES = [
  SLIDER_STREAM_LAYER_NAMES.soilMoisture,
  SLIDER_STREAM_LAYER_NAMES.soilTemperature,
  SLIDER_STREAM_LAYER_NAMES.soilVapourPressureDeficit,
  ...CLIMATE_FIELD_SIGNAL_IDS.map(climateFieldStreamName),
] as const;

const PARQUET_LANE_BY_CAPABILITY = new Map<string, string>([
  [SLIDER_STREAM_LAYER_NAMES.drought, "drought"],
  ["fire-detections", "fire-detections"],
  ["vegetation", "vegetation"],
  ["water-gauges", "water-gauges"],
  ["weather-observations", "weather-observations"],
  ...SIGNAL_CAPABILITY_NAMES.map((layerName) => [layerName, "signal"] as const),
]);

function withheldReason(
  layerName: string,
  parquetLane: string,
  coverageByLane: ReadonlyMap<string, { earliestDay: string | null; latestDay: string | null }>
): WithheldParquetCapabilityReason {
  const laneCoverage = coverageByLane.get(parquetLane);
  if (laneCoverage === undefined) return "lane_not_registered";
  if (laneCoverage.earliestDay === null && laneCoverage.latestDay === null) {
    return "lane_never_written";
  }
  return SIGNAL_CAPABILITY_NAMES.includes(layerName as (typeof SIGNAL_CAPABILITY_NAMES)[number])
    ? "signal_product_coverage_not_reported"
    : "per_rung_coverage_not_reported";
}

/**
 * Repoints the public census to Parquet without using PostgreSQL as a fallback for migrated rows.
 * See `src/lib/server/AGENTS.md` section "Parquet tRPC cutover".
 */
export async function getParquetSliderCapabilities(): Promise<ParquetSliderCapabilities> {
  const coverage = await getParquetWarehouseCoverage();
  const postgresCapabilities = await getSliderCapabilities();
  const coverageByLane = new Map(coverage.lanes.map((lane) => [lane.layer, lane] as const));

  const withheldParquetCapabilities = [...PARQUET_LANE_BY_CAPABILITY].map(
    ([layerName, parquetLane]) => ({
      layerName,
      parquetLane,
      reason: withheldReason(layerName, parquetLane, coverageByLane),
    })
  );

  return {
    ...postgresCapabilities,
    // The frozen coverage route is tier-agnostic, and `signal` has no product dimension.
    // Until those axes exist, publishing any migrated row would overstate what can be read.
    layers: postgresCapabilities.layers.filter(
      (layer) => !PARQUET_LANE_BY_CAPABILITY.has(layer.layerName)
    ),
    // Every PostgreSQL stream row is Parquet-owned and withheld above, so its scan state must
    // not make the client remount an unproved Parquet slider as an "unknown" capability.
    streamsUnavailable: false,
    parquetCoverageGeneratedAt: coverage.generatedAt,
    withheldParquetCapabilities,
  };
}

/** Parquet lanes this adapter owns, exported for reference-scan tests and cutover reporting. */
export const PARQUET_CAPABILITY_LANES = [...DIRECT_PARQUET_LANES, "signal"] as const;
