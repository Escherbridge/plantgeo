export type ErosionClass =
  | "very_low"
  | "low"
  | "moderate"
  | "high"
  | "very_high";

export const EROSION_COLORS: Readonly<Record<ErosionClass, string>> = {
  very_low: "#4caf50",
  low: "#8bc34a",
  moderate: "#ff9800",
  high: "#f44336",
  very_high: "#9c27b0",
};
