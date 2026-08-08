import { describe, expect, it } from "vitest";

import { buildLinearScales, selectXLabelIndices } from "@/components/dashboard/chart-scales";

const FRAME = { width: 320, height: 180, padLeft: 44, padRight: 12, padTop: 12, padBottom: 28 };

describe("buildLinearScales", () => {
  it("spans the domain across the padded frame", () => {
    const scales = buildLinearScales([0.2, 0.8], 2, FRAME);
    expect(scales.scaleY(0.8)).toBe(FRAME.padTop);
    expect(scales.scaleY(0.2)).toBe(FRAME.height - FRAME.padBottom);
    expect(scales.scaleX(0)).toBe(FRAME.padLeft);
    expect(scales.scaleX(1)).toBe(FRAME.width - FRAME.padRight);
  });

  it("pads a flat domain instead of faking a unit range", () => {
    // A zero-spread series used to label the mid gridline above the data maximum
    // and emit duplicate tick values; the padded domain keeps 0.42 in the middle.
    const scales = buildLinearScales([0.42, 0.42, 0.42], 3, FRAME);
    const values = scales.ticks.map((tick) => tick.value);
    expect(new Set(values).size).toBe(3);
    expect(values[1]).toBeCloseTo(0.42, 10);
    expect(values[0]).toBeLessThan(0.42);
    expect(values[2]).toBeGreaterThan(0.42);
  });

  it("centres a single point rather than dividing by zero", () => {
    const scales = buildLinearScales([0.5], 1, FRAME);
    const chartWidth = FRAME.width - FRAME.padLeft - FRAME.padRight;
    expect(scales.scaleX(0)).toBe(FRAME.padLeft + chartWidth / 2);
    expect(Number.isFinite(scales.scaleY(0.5))).toBe(true);
  });

  it("chooses decimals by domain spread", () => {
    expect(buildLinearScales([0, 500], 2, FRAME).decimals).toBe(0);
    expect(buildLinearScales([0, 50], 2, FRAME).decimals).toBe(1);
    expect(buildLinearScales([0, 5], 2, FRAME).decimals).toBe(2);
    expect(buildLinearScales([0.1, 0.6], 2, FRAME).decimals).toBe(3);
  });
});

describe("selectXLabelIndices", () => {
  it("always includes both endpoints", () => {
    const indices = selectXLabelIndices(30);
    expect(indices[0]).toBe(0);
    expect(indices[indices.length - 1]).toBe(29);
    expect(indices).toHaveLength(6);
  });

  it("degrades to a single label for a single point", () => {
    expect(selectXLabelIndices(1)).toEqual([0]);
  });
});
