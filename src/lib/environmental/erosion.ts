export type ErosionClass =
  | "very_low"
  | "low"
  | "moderate"
  | "high"
  | "very_high";

// YlOrRd-5 ordinal sequence -- shared source of truth for erosion risk color everywhere
// it's shown (map layer + SoilPanel's SuitabilityBar), so the two cannot disagree.
export const EROSION_COLORS: Readonly<Record<ErosionClass, string>> = {
  very_low: "#ffffb2",
  low: "#fecc5c",
  moderate: "#fd8d3c",
  high: "#f03b20",
  very_high: "#bd0026",
};
