import { getSoilProperties, type SoilProperties } from "@/lib/server/services/soilgrids";
import { calculateErosionRisk, classifyErosionRisk } from "@/lib/server/services/usle";

/** Land cover multipliers for carbon sequestration potential */
const LAND_COVER_MULTIPLIERS: Record<string, number> = {
  reforestation: 1.5,
  silvopasture: 1.2,
  cover_cropping: 0.8,
  biochar: 2.0,
  pasture: 0.6,
  cropland: 0.4,
  grassland: 0.7,
  forest: 0.3, // already sequestering; less additional potential
  bare: 1.0,
};

export interface CarbonPotential {
  currentOC: number;              // current organic carbon g/kg
  potentialGain: number;          // potential tC/ha/yr
  yearsToSaturation: number;      // estimated years to reach saturation
  confidenceClass: "high" | "medium" | "low";
}

export type InterventionType =
  | "reforestation"
  | "silvopasture"
  | "cover_cropping"
  | "biochar"
  | "keyline";

export interface InterventionSuitability {
  lat: number;
  lon: number;
  soilProps: SoilProperties;
  erosionRisk: number;
  erosionClass: string;
  interventions: Record<
    InterventionType,
    {
      suitabilityScore: number;       // 0–100
      carbonPotential: CarbonPotential;
      rationale: string;
    }
  >;
}

/**
 * Calculate carbon sequestration potential for a given land cover intervention.
 * Formula: potential_tC_ha_yr = (6.5 - organicCarbon) * 0.3 * landCoverMultiplier * rainfallFactor
 *
 * @param soilProps  SoilProperties from SoilGrids (organicCarbon in g/kg)
 * @param landCover  Intervention / land cover type key
 * @param rainfall   Annual rainfall in mm
 */
export function calculateCarbonPotential(
  soilProps: SoilProperties,
  landCover: string,
  rainfall: number
): CarbonPotential {
  // Convert organicCarbon from g/kg to % for formula (g/kg ÷ 10 = %)
  const ocPercent = soilProps.organicCarbon / 10;

  // Deficit from saturation (assumed saturation ~6.5% OC for most soils)
  const deficit = Math.max(0, 6.5 - ocPercent);

  // Rainfall factor: optimal around 800mm, reduced below 400mm and above 2000mm
  const rainfallFactor = rainfall < 400
    ? 0.4
    : rainfall < 800
    ? 0.4 + ((rainfall - 400) / 400) * 0.6
    : rainfall <= 2000
    ? 1.0
    : Math.max(0.6, 1.0 - ((rainfall - 2000) / 2000) * 0.4);

  const multiplier = LAND_COVER_MULTIPLIERS[landCover] ?? 1.0;

  const potentialGain = deficit * 0.3 * multiplier * rainfallFactor;

  // Years to saturation: at this rate, how long to close the deficit
  // Simplified: saturation = deficit / (potentialGain / ocSaturationFactor)
  const yearsToSaturation =
    potentialGain > 0 ? Math.round(deficit / (potentialGain / 10)) : 999;

  // Confidence class based on data completeness
  const confidenceClass: "high" | "medium" | "low" =
    soilProps.organicCarbon > 0 && soilProps.bulkDensity > 0
      ? rainfall > 0
        ? "high"
        : "medium"
      : "low";

  return {
    currentOC: soilProps.organicCarbon,
    potentialGain: Math.round(potentialGain * 100) / 100,
    yearsToSaturation: Math.min(yearsToSaturation, 100),
    confidenceClass,
  };
}

/**
 * Compute suitability score (0–100) for each intervention type at a point.
 * Combines soil health, erosion risk, and carbon potential.
 *
 * Exported for use by Track 26 (Strategy Cards).
 */
export async function getInterventionSuitability(
  lat: number,
  lon: number
): Promise<InterventionSuitability> {
  const soilProps = await getSoilProperties(lat, lon);

  // Default rainfall of 600mm if not available from external source
  const rainfall = 600;

  const erosionScore = calculateErosionRisk(soilProps, 5, "cropland");
  const erosionClass = classifyErosionRisk(erosionScore);

  function buildIntervention(
    type: InterventionType,
    landCoverKey: string
  ): { suitabilityScore: number; carbonPotential: CarbonPotential; rationale: string } {
    const carbon = calculateCarbonPotential(soilProps, landCoverKey, rainfall);

    // Suitability heuristics per intervention type
    let score = 50;
    let rationale = "";

    switch (type) {
      case "reforestation": {
        // Needs adequate moisture, low current OC (degraded land), gentle slopes
        const ocPct = soilProps.organicCarbon / 10;
        score = 40 + (ocPct < 2 ? 30 : 10) + (soilProps.ph > 5.5 && soilProps.ph < 7.5 ? 20 : 0);
        rationale =
          ocPct < 2
            ? "Degraded soil with low OC — high restoration benefit"
            : soilProps.ph > 7.5
            ? "Alkaline soil — select lime-tolerant species"
            : "Moderate suitability for reforestation";
        break;
      }
      case "silvopasture": {
        // Needs well-drained, moderate-high OC
        const ocPct = soilProps.organicCarbon / 10;
        score = 30 + (ocPct > 1.5 ? 25 : 10) + (soilProps.ph > 5.5 && soilProps.ph < 7.0 ? 25 : 10) + (soilProps.cec > 10 ? 10 : 0);
        rationale =
          ocPct > 1.5 && soilProps.ph > 5.5
            ? "Good soil health — silvopasture highly suitable"
            : "Soil improvement recommended before silvopasture";
        break;
      }
      case "cover_cropping": {
        // Works on most soils; better when low OC
        const ocPct = soilProps.organicCarbon / 10;
        score = 55 + (ocPct < 1.5 ? 20 : 5) + (soilProps.nitrogen < 1 ? 15 : 0);
        rationale =
          soilProps.nitrogen < 1
            ? "Low nitrogen — legume cover crops will improve fertility"
            : "Cover cropping suitable for maintaining SOC";
        break;
      }
      case "biochar": {
        // Most beneficial on low-OC, low-CEC soils
        const ocPct = soilProps.organicCarbon / 10;
        score = 30 + (ocPct < 1 ? 40 : ocPct < 2 ? 20 : 5) + (soilProps.cec < 10 ? 20 : 5);
        rationale =
          ocPct < 1
            ? "Very low OC — biochar will significantly improve soil carbon and CEC"
            : soilProps.cec < 10
            ? "Low CEC — biochar improves nutrient retention"
            : "Moderate benefit; soil not severely degraded";
        break;
      }
      case "keyline": {
        // Needs adequate depth + infiltration; penalise heavy clay or very low BD
        score = 40 + (soilProps.bulkDensity > 0.8 && soilProps.bulkDensity < 1.4 ? 30 : 10) + (soilProps.ph > 5.5 ? 15 : 0);
        rationale =
          soilProps.bulkDensity > 0.8 && soilProps.bulkDensity < 1.4
            ? "Suitable bulk density for keyline design and water infiltration"
            : soilProps.bulkDensity >= 1.4
            ? "High bulk density — compaction may limit keyline effectiveness"
            : "Low bulk density — assess soil depth before keyline design";
        break;
      }
    }

    return {
      suitabilityScore: Math.round(Math.min(100, Math.max(0, score))),
      carbonPotential: carbon,
      rationale,
    };
  }

  return {
    lat,
    lon,
    soilProps,
    erosionRisk: erosionScore,
    erosionClass,
    interventions: {
      reforestation: buildIntervention("reforestation", "reforestation"),
      silvopasture: buildIntervention("silvopasture", "silvopasture"),
      cover_cropping: buildIntervention("cover_cropping", "cover_cropping"),
      biochar: buildIntervention("biochar", "bare"),
      keyline: buildIntervention("keyline", "pasture"),
    },
  };
}
