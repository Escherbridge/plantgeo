import { z } from "zod";
import { CLIMATE_FIELD_SIGNALS } from "@/lib/environmental/climate-field";
import { climateFieldSignalForGeometryLayerId } from "@/lib/map/climate-field-layer-ids";
import type { LayerToggleId } from "@/lib/map/layer-registry";

/** The agri agent tool behind the one-line window distribution; see map/AGENTS.md §window-distribution. */
export const DISTRIBUTION_AT_POINT_TOOL = "distribution_at_point";

const DISTRIBUTION_LANE_STATES = [
  "published",
  "no_data_in_window",
  "static_not_applicable",
  "refused",
] as const;

const finiteNumber = z.number().finite();

const distributionLaneSchema = z.object({
  parquet_lane: z.string(),
  signal_name: z.string(),
  unit: z.string().nullable(),
  days_in_window: z.number().int().nonnegative(),
  days_with_data: z.number().int().nonnegative(),
  stats: z
    .object({
      min: finiteNumber,
      p10: finiteNumber,
      median: finiteNumber,
      p90: finiteNumber,
      max: finiteNumber,
      mean: finiteNumber,
    })
    .nullable(),
  spatial_relation: z.string().nullable(),
  distance_km: finiteNumber.nullable(),
  distance_km_basis: z.string().nullable(),
  static: z.boolean(),
  state: z.enum(DISTRIBUTION_LANE_STATES),
});

/** The agri `distribution_at_point` result (contract 2026-10-04). Unknown extra keys are dropped. */
export const distributionAtPointResultSchema = z.object({
  surface: z.string(),
  range_start: z.string(),
  range_end: z.string(),
  lanes: z.array(distributionLaneSchema).max(32),
});

export type DistributionAtPointResult = z.infer<typeof distributionAtPointResultSchema>;
export type DistributionLane = DistributionAtPointResult["lanes"][number];

/**
 * The dated toggle whose window a hovered/tapped style layer reads, or null when that layer has no
 * scalar value series to summarise. Deliberately small: scalar grids only, never sparse areas.
 */
export function windowedToggleForStyleLayer(styleLayerId: string): LayerToggleId | null {
  const climateSignal = climateFieldSignalForGeometryLayerId(styleLayerId);
  if (climateSignal !== null) return CLIMATE_FIELD_SIGNALS[climateSignal].toggleId;
  if (styleLayerId === "vegetation-ndvi-cells-fill") return "vegetation";
  if (styleLayerId === "weather-temperature" || styleLayerId === "weather-temperature-cells") return "weather";
  return null;
}

/** The lane matching the layer's selected variant, else the first lane. */
export function selectDistributionLane(
  result: DistributionAtPointResult,
  preferredSignalName: string | null
): DistributionLane | null {
  return (
    result.lanes.find((lane) => preferredSignalName !== null && lane.signal_name === preferredSignalName) ??
    result.lanes[0] ??
    null
  );
}

/** Two decimals under 10, one under 100, whole above: enough to read a spread, no more. */
function formatStatistic(value: number): string {
  const magnitude = Math.abs(value);
  return value.toFixed(magnitude >= 100 ? 0 : magnitude >= 10 ? 1 : 2);
}

/** What the nearest-support distance measured, in the analysis gap line's own words. */
function nearestSupportNoun(basis: string | null): string {
  if (basis === "source_coordinate") return "station";
  if (basis === "delineation_edge") return "delineation";
  return "cell";
}

/**
 * The one line, e.g. "30 d: median 0.36 (p10 0.30 – p90 0.41) · 28 of 30 days · nearest cell
 * 23.6 km". Null -- render nothing -- for static, refused and outside-every-area answers.
 */
export function describeLaneDistribution(lane: DistributionLane): string | null {
  if (lane.static || lane.state === "static_not_applicable" || lane.state === "refused") return null;
  if (lane.spatial_relation === "nearest_area_outside") return null;
  const window = `${lane.days_in_window} d`;
  const coverage = `${lane.days_with_data} of ${lane.days_in_window} days`;
  const nearest =
    lane.spatial_relation === "nearest_cell" && lane.distance_km !== null
      ? ` · nearest ${nearestSupportNoun(lane.distance_km_basis)} ${lane.distance_km.toFixed(1)} km`
      : "";
  if (lane.state === "no_data_in_window" || lane.stats === null) {
    return `${window}: no data · ${coverage}${nearest}`;
  }
  const unit = lane.unit ? ` ${lane.unit}` : "";
  const { median, p10, p90 } = lane.stats;
  return `${window}: median ${formatStatistic(median)}${unit} (p10 ${formatStatistic(p10)} – p90 ${formatStatistic(p90)}) · ${coverage}${nearest}`;
}
