export const ANALYSIS_TIME_SCALES = ['day', 'month', 'year'] as const;
export type AnalysisTimeScale = (typeof ANALYSIS_TIME_SCALES)[number];

/** One layer's own inclusive history window; overrides the global trailing window for that layer. */
export interface AnalysisLayerWindow {
  rangeStart: string;
  rangeEnd: string;
}

export interface RegionalAnalysisSelection {
  timeScale: AnalysisTimeScale;
  rangeSteps: number;
  zoom: number;
  layerDays: Record<string, string>;
  /** Keyed like `layerDays` (toggle id or surface name); see services/AGENTS.md §regional-analysis-window. */
  layerWindows?: Record<string, AnalysisLayerWindow>;
  cropCoverReleaseDay?: string;
}

/** The trailing calendar month ending at each layer's selected day. */
export const DEFAULT_ANALYSIS_WINDOW = { timeScale: 'month', rangeSteps: 1 } as const;

export function isAnalysisCalendarDay(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const time = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value;
}

/**
 * Trailing window ending at `day`: `steps` units back, preserving month ends and leap days, and
 * never past `today` when one is given. A day that is not a real calendar day is returned as-is.
 */
export function analysisDateRange(day: string, timeScale: AnalysisTimeScale, steps: number, today?: string) {
  if (!isAnalysisCalendarDay(day)) return { rangeStart: day, rangeEnd: day };
  const date = new Date(`${day}T00:00:00Z`);
  if (timeScale === 'day') date.setUTCDate(date.getUTCDate() - steps);
  else {
    const selectedDay = date.getUTCDate();
    date.setUTCDate(1);
    date.setUTCMonth(date.getUTCMonth() - steps * (timeScale === 'year' ? 12 : 1));
    const lastDay = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0)).getUTCDate();
    date.setUTCDate(Math.min(selectedDay, lastDay));
  }
  const rangeStart = date.toISOString().slice(0, 10);
  const rangeEnd = isAnalysisCalendarDay(today) && today < day ? today : day;
  return { rangeStart: rangeStart < rangeEnd ? rangeStart : rangeEnd, rangeEnd };
}
