import type { SoilProperties } from "@/lib/server/services/soilgrids";

/** C-factors by land cover type (dimensionless, 0–1) */
const C_FACTORS: Record<string, number> = {
  forest: 0.001,
  woodland: 0.003,
  pasture: 0.01,
  grassland: 0.02,
  cover_cropping: 0.05,
  silvopasture: 0.008,
  cropland: 0.2,
  bare: 1.0,
  shrubland: 0.05,
  wetland: 0.001,
  urban: 0.01,
};

/**
 * Approximate USLE K-factor (soil erodibility) from soil properties.
 * K ranges from 0.02 (resistant) to 0.69 (highly erodible).
 * Approximation uses organic carbon and bulk density as texture proxies.
 */
function estimateKFactor(soilProps: SoilProperties): number {
  // Higher organic carbon = lower erodibility
  const ocPenalty = Math.max(0, 1 - soilProps.organicCarbon / 20);
  // Higher bulk density = coarser texture = slightly higher erodibility
  const bdFactor = Math.min(1, soilProps.bulkDensity / 1.5);
  // Base K influenced by both
  const k = 0.1 + ocPenalty * 0.4 + bdFactor * 0.15;
  return Math.min(0.69, Math.max(0.02, k));
}

/**
 * USLE LS-factor from slope angle in degrees.
 * Uses the standard McCool et al. (1987) approximation.
 * @param slopeDeg slope in degrees
 */
function computeLSFactor(slopeDeg: number): number {
  if (slopeDeg <= 0) return 0;
  const slopeRad = (slopeDeg * Math.PI) / 180;
  const ls =
    Math.pow(slopeDeg / 22.13, 0.6) *
    Math.pow(Math.sin(slopeRad) / 0.0896, 1.3);
  return Math.min(ls, 50); // cap at 50 to avoid extreme values on cliffs
}

/**
 * Calculate USLE-based erosion risk score (0–100).
 * USLE: A = R × K × LS × C × P
 * R (rainfall erosivity) is normalised to 1 for scoring.
 * P (practice factor) is assumed 1 (no conservation practice).
 * Score is scaled to 0–100 for display.
 *
 * @param soilProps   SoilProperties from SoilGrids
 * @param slopeDeg    Slope in degrees
 * @param landCover   Land cover type key (forest, pasture, cropland, bare, etc.)
 */
export function calculateErosionRisk(
  soilProps: SoilProperties,
  slopeDeg: number,
  landCover: string
): number {
  const k = estimateKFactor(soilProps);
  const ls = computeLSFactor(slopeDeg);
  const c = C_FACTORS[landCover] ?? 0.1;
  const p = 1.0; // no conservation practice assumed

  // Normalised USLE value — divide by a reference value to scale to 0-100
  const usle = k * ls * c * p;
  // Reference: K=0.4, LS=10, C=0.2, P=1 → 0.8 → maps to ~80 score
  const reference = 0.01;
  const score = (usle / reference) * 10;

  return Math.round(Math.min(100, Math.max(0, score)));
}

export type ErosionClass =
  | "very_low"
  | "low"
  | "moderate"
  | "high"
  | "very_high";

/** Classify erosion risk score into 5-class rating */
export function classifyErosionRisk(score: number): ErosionClass {
  if (score < 10) return "very_low";
  if (score < 30) return "low";
  if (score < 55) return "moderate";
  if (score < 75) return "high";
  return "very_high";
}

/** Color for each erosion risk class */
export const EROSION_COLORS: Record<ErosionClass, string> = {
  very_low: "#4caf50",
  low: "#8bc34a",
  moderate: "#ff9800",
  high: "#f44336",
  very_high: "#9c27b0",
};
