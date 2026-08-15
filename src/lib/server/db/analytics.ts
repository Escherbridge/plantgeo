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
 * These types stay because analyticsRouter still describes the shape the future
 * aggregate must satisfy. Reintroduce queries here only alongside a read path
 * that distinguishes "unknown" from "zero".
 *
 * The Environmental Analytics panel that consumed these -- and the CSV/PDF
 * exporter in lib/export/analytics-export.ts -- were removed from the front end
 * on 2026-08-15: with the aggregate unpublished, the panel could only ever
 * render the PRECONDITION_FAILED message. The router is deliberately left in
 * place so restoring the panel is a front-end change alone.
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
