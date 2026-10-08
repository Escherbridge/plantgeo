import { z } from "zod";
import {
  CLIMATE_FIELD_SIGNALS,
  climateFieldSignalForToggle,
  climateFieldSignalName,
  type AirTemperatureVariant,
} from "@/lib/environmental/climate-field";
import { climateFieldSignalForGeometryLayerId } from "@/lib/map/climate-field-layer-ids";
import {
  DEFAULT_SOIL_FIELD_DEPTHS,
  SOIL_FIELD_MEASURES,
  soilFieldDepthDefinition,
  soilFieldLayerIdsFor,
  type SoilFieldDepth,
  type SoilFieldMeasure,
} from "@/lib/environmental/soil-field";
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
  /**
   * The read cap (contract 2026-10-07): `truncated` means agri read only the window's latest published
   * days, `read_range_start..read_range_end` (`days_in_read_range` calendar days), and every count and
   * statistic covers that range alone. Defaulted so an answer cached before the cap still parses.
   */
  days_read: z.number().int().nonnegative().nullable().default(null),
  days_in_read_range: z.number().int().nonnegative().nullable().default(null),
  read_range_start: z.string().nullable().default(null),
  read_range_end: z.string().nullable().default(null),
  truncated: z.boolean().default(false),
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
  /** Why a `refused` lane has no statistics; decides how long the answer may be cached. */
  refusal_code: z.string().nullable().default(null),
});

/**
 * The agri `distribution_at_point` result (contract 2026-10-05). A whole-call refusal has the same
 * shape with `state: "refused"`, a `refusal_code` and no lanes. Unknown extra keys are dropped.
 */
export const distributionAtPointResultSchema = z.object({
  surface: z.string(),
  range_start: z.string(),
  range_end: z.string(),
  state: z.string().nullable().default(null),
  refusal_code: z.string().nullable().default(null),
  lanes: z.array(distributionLaneSchema).max(32),
});

export type DistributionAtPointResult = z.infer<typeof distributionAtPointResultSchema>;
export type DistributionLane = DistributionAtPointResult["lanes"][number];

/**
 * The two ERA5-Land soil measures a hovered/tapped point may read a window over. `soil-vpd` is
 * deliberately excluded: its one pseudo-depth ("surface") is already the whole signal, so it
 * needs no depth-to-signal threading and stays off this surface's small, hand-written map.
 */
export const WINDOWED_SOIL_FIELD_MEASURES: readonly SoilFieldMeasure[] = ["moisture", "temperature"];

/** The measure whose `soilFieldLayerIdsFor` fill/outline id this is, as `SoilFieldLayer.tsx` forms them. */
export function soilFieldMeasureForStyleLayerId(styleLayerId: string): SoilFieldMeasure | null {
  for (const measure of WINDOWED_SOIL_FIELD_MEASURES) {
    const ids = soilFieldLayerIdsFor(measure);
    if (styleLayerId === ids.fill || styleLayerId === ids.outline) {
      return measure;
    }
  }
  return null;
}

/**
 * The dated toggle whose window a hovered/tapped style layer reads, or null when that layer has no
 * scalar value series to summarise. Deliberately small: scalar grids only, never sparse areas.
 */
export function windowedToggleForStyleLayer(styleLayerId: string): LayerToggleId | null {
  const climateSignal = climateFieldSignalForGeometryLayerId(styleLayerId);
  if (climateSignal !== null) return CLIMATE_FIELD_SIGNALS[climateSignal].toggleId;
  const soilMeasure = soilFieldMeasureForStyleLayerId(styleLayerId);
  if (soilMeasure !== null) return SOIL_FIELD_MEASURES[soilMeasure].toggleId;
  if (styleLayerId === "vegetation-ndvi-cells-fill") return "vegetation";
  if (styleLayerId === "weather-temperature" || styleLayerId === "weather-temperature-cells") return "weather";
  return null;
}

/** The weather-observations measure the weather temperature layers paint. */
const WEATHER_AIR_TEMPERATURE_SIGNAL = "air_temperature";

/**
 * The one signal to ask agri for when the hovered layer paints one signal of a surface that carries
 * several (air temperature's mean/max/min lanes, weather's four measures, a soil field's 3-4
 * depths); null when the surface's only signal is the one painted. Sent as `signal_name`, so agri
 * reads one lane, not all of them.
 *
 * `soilFieldDepths` is the painted depth per measure (`soil-store.fieldDepth`), not an
 * `AirTemperatureVariant` sibling parameter, because a depth is per-MEASURE (moisture and
 * temperature pick independently) where a variant is per-SURFACE: defaulted so every existing
 * caller -- weather, climate, vegetation -- keeps compiling unchanged.
 */
export function distributionSignalName(
  styleLayerId: string,
  airTemperatureVariant: AirTemperatureVariant,
  soilFieldDepths: Readonly<Record<SoilFieldMeasure, SoilFieldDepth>> = DEFAULT_SOIL_FIELD_DEPTHS
): string | null {
  const climateSignal = climateFieldSignalForGeometryLayerId(styleLayerId);
  if (climateSignal !== null) {
    return CLIMATE_FIELD_SIGNALS[climateSignal].signalName === null
      ? climateFieldSignalName(climateSignal, airTemperatureVariant)
      : null;
  }
  const soilMeasure = soilFieldMeasureForStyleLayerId(styleLayerId);
  if (soilMeasure !== null) {
    return soilFieldDepthDefinition(soilMeasure, soilFieldDepths[soilMeasure]).signalName;
  }
  if (styleLayerId === "weather-temperature" || styleLayerId === "weather-temperature-cells") {
    return WEATHER_AIR_TEMPERATURE_SIGNAL;
  }
  return null;
}

/** Every `signalName` the server accepts for a toggle: exactly what `distributionSignalName` can send. */
export function acceptedDistributionSignalNames(layerId: LayerToggleId): readonly string[] {
  const climateSignal = climateFieldSignalForToggle(layerId);
  if (climateSignal !== null) {
    const definition = CLIMATE_FIELD_SIGNALS[climateSignal];
    return definition.signalName === null ? definition.variants.map((variant) => variant.signalName) : [];
  }
  const soilMeasure = WINDOWED_SOIL_FIELD_MEASURES.find(
    (measure) => SOIL_FIELD_MEASURES[measure].toggleId === layerId
  );
  if (soilMeasure !== undefined) {
    return SOIL_FIELD_MEASURES[soilMeasure].depths.map((depth) => depth.signalName);
  }
  return layerId === "weather" ? [WEATHER_AIR_TEMPERATURE_SIGNAL] : [];
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
 * 23.6 km"; a truncated read says so: "365 d (latest 204 read): median … · 200 of 204 days".
 * Null -- render nothing -- for static, refused and outside-every-area answers.
 */
export function describeLaneDistribution(lane: DistributionLane): string | null {
  if (lane.static || lane.state === "static_not_applicable" || lane.state === "refused") return null;
  if (lane.spatial_relation === "nearest_area_outside") return null;
  // A truncated answer's counts cover only the days read, so they are read against that range.
  const daysCounted =
    lane.truncated && lane.days_in_read_range !== null ? lane.days_in_read_range : lane.days_in_window;
  const window =
    daysCounted === lane.days_in_window
      ? `${lane.days_in_window} d`
      : `${lane.days_in_window} d (latest ${daysCounted} d read)`;
  const coverage = `${lane.days_with_data} of ${daysCounted} days`;
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
