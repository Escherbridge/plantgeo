export interface CarbonPotential {
  currentOC: number;
  potentialGain: number;
  yearsToSaturation: number;
  confidenceClass: "high" | "medium" | "low";
}

export type InterventionType =
  | "reforestation"
  | "silvopasture"
  | "cover_cropping"
  | "biochar"
  | "keyline";
