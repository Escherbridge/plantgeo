/** Maximum reported gap or thin ranges per layer; see AGENTS.md §slider-policy. */
export const MAX_REPORTED_DAY_RANGES = 800;

/** Visible future band, independent of any forecast capability. */
export const FUTURE_AXIS_DAYS = 30;

/** Checks coverage clock metadata without interpreting publisher day strings. */
export function isReusableSliderCoverage(
  coverage: { evaluatedThroughDay: string; generatedAt: string },
  nowMs: number
): boolean {
  const generatedAt = Date.parse(coverage.generatedAt);
  return coverage.evaluatedThroughDay === new Date(nowMs).toISOString().slice(0, 10) &&
    Number.isFinite(generatedAt) && generatedAt <= nowMs && nowMs - generatedAt < 10 * 60_000;
}
