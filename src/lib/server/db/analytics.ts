/**
 * Reserved contract types for the regional-analytics aggregates.
 *
 * The query implementations that used to live here were removed: every one of
 * them was unreachable, because analyticsRouter answers getRegionalRiskSummary,
 * getTrendData, getPrioritySubregions and getDemandDensity with
 * PRECONDITION_FAILED until a versioned warehouse aggregate is published. The
 * removed code also treated "no rows" as zero, which made an empty database
 * report a confident fireRiskAvg of 0, a droughtClass of 0 and an "improving"
 * risk trend -- a fabricated result, not an absent one.
 *
 * These types stay because the router and lib/export/analytics-export.ts still
 * describe the shape the future aggregate must satisfy. Reintroduce queries
 * here only alongside a read path that distinguishes "unknown" from "zero".
 */

export interface RegionalRiskSummary {
  fireRiskAvg: number;
  droughtClass: number;
  /** Null when no water_gauges rows fall within the bbox — never fabricated. */
  streamflowPercentile: number | null;
  activeFireCount: number;
  riskTrend: "improving" | "stable" | "worsening";
}

export interface TrendPoint {
  date: string;
  value: number;
}

export interface PrioritySubregion {
  name: string;
  lat: number;
  lon: number;
  score: number;
  primaryIssue: string;
}
