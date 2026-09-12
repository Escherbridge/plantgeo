import {
  resolveZoomGranularity,
  type ZoomGranularity,
  type ZoomGranularityTiers,
} from "./zoom-granularity";

export type SoilSurveyGranularity = ZoomGranularity;

export interface SoilSurveyCoverage {
  cells: number;
  covered: number;
  ingested: number;
}

export const MAX_SOIL_BBOX_SQUARE_DEGREES = 0.02;
export const SOIL_SURVEY_DETAIL_MIN_ZOOM = 13;
export const SOIL_SURVEY_REGIONAL_MIN_ZOOM = 9;

export const SOIL_SURVEY_TIERS: ZoomGranularityTiers = {
  detailMinZoom: SOIL_SURVEY_DETAIL_MIN_ZOOM,
  regionalMinZoom: SOIL_SURVEY_REGIONAL_MIN_ZOOM,
};

export function resolveSoilSurveyGranularity(zoom?: number): SoilSurveyGranularity {
  return resolveZoomGranularity(zoom, SOIL_SURVEY_TIERS);
}

/** Detail requests retain the bounded viewport contract until the Parquet lane is published. */
export function soilSurveyAreaCeiling(zoom?: number): number | null {
  return resolveSoilSurveyGranularity(zoom) === "detail"
    ? MAX_SOIL_BBOX_SQUARE_DEGREES
    : null;
}
