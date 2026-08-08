/**
 * The scale/axis math every hand-rolled SVG chart in this directory shares, so a
 * chart-specific fix (a flat series, a single point) lands once instead of drifting
 * per component.
 */

export interface ChartFrame {
  width: number;
  height: number;
  padLeft: number;
  padRight: number;
  padTop: number;
  padBottom: number;
}

export interface LinearScales {
  scaleX: (index: number) => number;
  scaleY: (value: number) => number;
  chartWidth: number;
  chartHeight: number;
  /** min / mid / max of the padded domain, with their pixel rows. */
  ticks: { value: number; y: number }[];
  /** Decimal places that keep tick labels legible for this domain's spread. */
  decimals: number;
}

/**
 * Builds x/y scales over `values` (every value the domain must hold -- including
 * band extremes, or the band clips at the frame). A flat domain is padded around
 * its value rather than stretched to a fake unit range, so the labels stay true;
 * a single x position centres rather than dividing by zero.
 */
export function buildLinearScales(
  values: number[],
  pointCount: number,
  frame: ChartFrame
): LinearScales {
  const chartWidth = frame.width - frame.padLeft - frame.padRight;
  const chartHeight = frame.height - frame.padTop - frame.padBottom;

  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    const pad = Math.max(Math.abs(min) * 0.1, 0.1);
    min -= pad;
    max += pad;
  }
  const range = max - min;

  const scaleX = (index: number) =>
    frame.padLeft +
    (pointCount === 1 ? chartWidth / 2 : (index / (pointCount - 1)) * chartWidth);
  const scaleY = (value: number) =>
    frame.padTop + chartHeight - ((value - min) / range) * chartHeight;

  const decimals = range >= 100 ? 0 : range >= 10 ? 1 : range >= 1 ? 2 : 3;
  const ticks = [min, min + range * 0.5, max].map((value) => ({
    value,
    y: scaleY(value),
  }));

  return { scaleX, scaleY, chartWidth, chartHeight, ticks, decimals };
}

/**
 * True when every point carries both band quantiles. One predicate on purpose:
 * the caption that claims a band and the chart that draws one must agree.
 */
export function hasFullBand(points: { low: number | null; high: number | null }[]): boolean {
  return points.length > 0 && points.every((point) => point.low !== null && point.high !== null);
}

/** Up to `maxLabels` evenly spaced x indices, endpoints always included. */
export function selectXLabelIndices(pointCount: number, maxLabels = 6): number[] {
  const labelCount = Math.min(pointCount, maxLabels);
  if (labelCount <= 1) return [0];
  return Array.from({ length: labelCount }, (_, i) =>
    Math.round((i / (labelCount - 1)) * (pointCount - 1))
  );
}
