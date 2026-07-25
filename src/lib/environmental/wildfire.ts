export type BurnSeverityClass =
  | "unburned"
  | "low"
  | "moderate"
  | "high"
  | "increased_greenness";

export interface MTBSFireProperties {
  fireName: string;
  fireYear: number;
  acres: number;
  severityClass: BurnSeverityClass;
  ignitionDate: string;
  fireId: string;
}
