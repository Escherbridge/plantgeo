import { isLayerToggleId, LAYER_REGISTRY, type LayerToggleId } from '@/lib/map/layer-registry';

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

/** Per-layer window chip presets, in inclusive days; see stores/AGENTS.md §layer-window. */
export const LAYER_WINDOW_PRESETS = [7, 30, 90, 365] as const;
export type LayerWindowPreset = (typeof LAYER_WINDOW_PRESETS)[number];
export const DEFAULT_LAYER_WINDOW_PRESET: LayerWindowPreset = 30;
/** The longest inclusive window a request may name: a leap year. */
export const MAX_LAYER_WINDOW_DAYS = 366;

const MILLISECONDS_PER_DAY = 86_400_000;

export function isLayerWindowPreset(value: unknown): value is LayerWindowPreset {
  return (LAYER_WINDOW_PRESETS as readonly unknown[]).includes(value);
}

/** A registry toggle backed by a warehouse stream: the only ids `layerWindows` may name. */
export function isWindowedLayerId(value: unknown): value is LayerToggleId {
  return typeof value === 'string' && isLayerToggleId(value) && LAYER_REGISTRY[value].warehouseLayerName !== null;
}

function calendarDayTime(day: string): number {
  return Date.parse(`${day}T00:00:00Z`);
}

/** Inclusive day count of a window; 0 when either end is not a calendar day. */
export function layerWindowDayCount(window: AnalysisLayerWindow): number {
  if (!isAnalysisCalendarDay(window.rangeStart) || !isAnalysisCalendarDay(window.rangeEnd)) return 0;
  return Math.round((calendarDayTime(window.rangeEnd) - calendarDayTime(window.rangeStart)) / MILLISECONDS_PER_DAY) + 1;
}

/** Two real calendar days, start ≤ end, at most `MAX_LAYER_WINDOW_DAYS` inclusive. */
export function isValidLayerWindow(window: unknown): window is AnalysisLayerWindow {
  if (typeof window !== 'object' || window === null) return false;
  const { rangeStart, rangeEnd } = window as Record<string, unknown>;
  if (!isAnalysisCalendarDay(rangeStart) || !isAnalysisCalendarDay(rangeEnd) || rangeStart > rangeEnd) return false;
  return layerWindowDayCount({ rangeStart, rangeEnd }) <= MAX_LAYER_WINDOW_DAYS;
}

/**
 * The inclusive `days`-day window ending at `day`, its end capped at `today` (UTC) so it never
 * reaches past it. Null when `day` or `today` is not a calendar day.
 */
export function trailingLayerWindow(day: string, days: number, today: string): AnalysisLayerWindow | null {
  if (!isAnalysisCalendarDay(day) || !isAnalysisCalendarDay(today)) return null;
  const rangeEnd = day > today ? today : day;
  const span = Math.max(1, Math.min(MAX_LAYER_WINDOW_DAYS, Math.trunc(days)));
  const rangeStart = new Date(calendarDayTime(rangeEnd) - (span - 1) * MILLISECONDS_PER_DAY).toISOString().slice(0, 10);
  return { rangeStart, rangeEnd };
}

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
