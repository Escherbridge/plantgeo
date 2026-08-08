export type CarbonClass = "very_low" | "low" | "medium" | "high" | "very_high";

// Green ordinal ramp (darker = higher sequestration potential) -- shared source of truth
// for carbon-potential color everywhere it's shown, so surfaces cannot disagree.
export const CARBON_COLORS: Readonly<Record<CarbonClass, string>> = {
  very_low: "#f1f8e9",
  low: "#c5e1a5",
  medium: "#8bc34a",
  high: "#558b2f",
  very_high: "#1b5e20",
};

/** Bins a potential gain (tC/ha/yr) into the ordinal class the ramp above colors. */
export function classifyCarbonPotential(potentialGain: number): CarbonClass {
  if (potentialGain < 0.2) return "very_low";
  if (potentialGain < 0.5) return "low";
  if (potentialGain < 1.0) return "medium";
  if (potentialGain < 2.0) return "high";
  return "very_high";
}
