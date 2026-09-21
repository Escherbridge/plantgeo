export const ANALYSIS_TIME_SCALES = ['day', 'month', 'year'] as const;
export type AnalysisTimeScale = (typeof ANALYSIS_TIME_SCALES)[number];

export interface RegionalAnalysisSelection {
  timeScale: AnalysisTimeScale;
  rangeSteps: number;
  zoom: number;
  layerDays: Record<string, string>;
  cropCoverReleaseDay?: string;
}

export const DEFAULT_ANALYSIS_WINDOW = { timeScale: 'month', rangeSteps: 1 } as const;

/** Calendar arithmetic preserves month ends and never changes a named day to local time. */
export function analysisDateRange(day: string, timeScale: AnalysisTimeScale, steps: number) {
  const shift = (direction: number) => {
    const date = new Date(`${day}T00:00:00Z`);
    if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== day) return day;
    if (timeScale === 'day') date.setUTCDate(date.getUTCDate() + direction * steps);
    else {
      const selectedDay = date.getUTCDate();
      date.setUTCDate(1);
      date.setUTCMonth(date.getUTCMonth() + direction * steps * (timeScale === 'year' ? 12 : 1));
      const lastDay = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0)).getUTCDate();
      date.setUTCDate(Math.min(selectedDay, lastDay));
    }
    return date.toISOString().slice(0, 10);
  };
  return { rangeStart: shift(-1), rangeEnd: shift(1) };
}
