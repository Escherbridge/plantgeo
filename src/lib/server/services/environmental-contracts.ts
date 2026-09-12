import type {
  AirTemperatureVariant,
  ClimateFieldBand,
  ClimateFieldSignalId,
  ClimateRenderForm,
} from "@/lib/environmental/climate-field";
import type { SoilFieldBand, SoilFieldDepth, SoilFieldMeasure } from "@/lib/environmental/soil-field";
import type { SliderCapabilities, SliderLayerCapability } from "@/types/time-slider";
import type { ZoomGranularity } from "./zoom-granularity";

/** A weather sample returned by the governed Parquet reader. */
export interface PublishedWeatherObservation {
  lat: number;
  lon: number;
  observedAt: string;
  temperature: number | null;
  humidity: number | null;
  windSpeed: number | null;
  windDirection: number | null;
  precipitation: number | null;
}

export type PublishedDroughtReason =
  | "not_published"
  | "invalid_observation_time"
  | "stale"
  | "not_forecastable"
  | "release_week_not_published";

/** Drought polygons and their selected-day publication state. */
export interface PublishedDroughtCollection extends GeoJSON.FeatureCollection {
  availability: "published" | "unavailable";
  observedAt: string | null;
  reason?: PublishedDroughtReason;
  carryForwardDays?: number;
}

export type SoilFieldFeatureProperties = {
  value: number;
  bandIndex: number;
  bandLabel: string;
  aggregated: boolean;
  cellKey: string | null;
  coverageFraction: number | null;
};

export interface PublishedSoilFieldCollection
  extends GeoJSON.FeatureCollection<GeoJSON.Polygon | GeoJSON.MultiPolygon> {
  availability: "published" | "unavailable";
  reason: "not_published" | "stale" | "not_forecastable" | null;
  granularity: ZoomGranularity;
  measure: SoilFieldMeasure;
  depth: SoilFieldDepth;
  unit: string;
  attribution: string;
  observedDay: string | null;
  requestedDay: string;
  newestAvailableDay: string | null;
  cellCount: number;
  truncated: boolean;
  maxCellCount: number;
  maxObservationAgeDays: number;
  latticeDegrees: number | null;
  smoothingSigmaDegrees: number | null;
  bands: readonly SoilFieldBand[];
  sourceClientExposureApproved: boolean;
}

export interface SoilFieldReadOptions {
  measure?: SoilFieldMeasure;
  date?: string;
  depth?: SoilFieldDepth;
  zoom?: number;
}

export type ClimateFieldFeatureProperties = {
  value: number;
  unit: string;
  bandIndex: number;
  bandLabel: string;
  observedDay: string;
  aggregated: boolean;
  cellKey: string | null;
  coverageFraction: number | null;
};

export interface PublishedClimateFieldCollection
  extends GeoJSON.FeatureCollection<GeoJSON.Polygon | GeoJSON.MultiPolygon | GeoJSON.Point> {
  availability: "published" | "unavailable";
  reason: "not_published" | "stale" | "not_forecastable" | null;
  granularity: ZoomGranularity;
  signal: ClimateFieldSignalId;
  variant: AirTemperatureVariant;
  unit: string;
  attribution: string;
  observedDay: string | null;
  requestedDay: string;
  newestAvailableDay: string | null;
  cellCount: number;
  latticeCellCount: number;
  renderForm: ClimateRenderForm;
  truncated: boolean;
  maxCellCount: number;
  maxObservationAgeDays: number;
  bands: readonly ClimateFieldBand[];
  sourceClientExposureApproved: boolean;
}

export interface ClimateFieldReadOptions {
  signal?: ClimateFieldSignalId;
  variant?: AirTemperatureVariant;
  date?: string;
  renderForm?: ClimateRenderForm;
}

export type EarliestObservedDateRule =
  | "gap_clustered"
  | "density_floored"
  | "full_history"
  | "warehouse_coverage"
  | "no_observations";

export interface ResolvedSliderLayerCapability extends SliderLayerCapability {
  earliestObservedDateRule: EarliestObservedDateRule;
  earliestRecordedObservationDate: string | null;
  earliestContinuousObservationDate: string | null;
  latestRecordedObservationDate: string | null;
  coverageGapsTruncated: boolean;
  thinRangesTruncated: boolean;
  coverageGapsDescribedFromDay: string | null;
  thinRangesDescribedFromDay: string | null;
  observedDayCount: number;
  excludedObservedDayCount: number;
  gapExcludedObservedDayCount: number;
  densityExcludedObservedDayCount: number;
  minimumDailyObservationCount: number | null;
}

export interface ResolvedSliderCapabilities extends SliderCapabilities {
  layers: ResolvedSliderLayerCapability[];
}
