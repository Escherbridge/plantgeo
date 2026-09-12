import { describe, expect, it } from "vitest";
import { createExpression, featureFilter } from "@maplibre/maplibre-gl-style-spec";
import { measuredValueLabelLayer } from "@/lib/map/measured-value-label";

const layer = measuredValueLabelLayer({ id: "labels", source: "served", unit: "m³/m³", fractionDigits: 3, opacity: 0.5 });

function label(properties: Record<string, unknown>) {
  const compiled = createExpression(layer.layout?.["text-field"]);
  if (compiled.result === "error") throw new Error(JSON.stringify(compiled.value));
  return compiled.value.evaluate({ zoom: 8 }, { type: "Polygon", properties });
}

describe("measured scalar labels", () => {
  it.each([null, undefined, "0.25", NaN, Infinity, -Infinity])("does not label invalid value %s", (value) => {
    const compiled = featureFilter(layer.filter);
    expect(compiled.filter({ zoom: 8 }, { type: "Polygon", properties: { value } })).toBe(false);
    expect(label({ value })).toBe("");
  });

  it("preserves zero, sub-unit precision, and the served aggregate statistic", () => {
    expect(label({ value: 0 })).toBe("0.000 m³/m³");
    expect(label({ value: 0.023, aggregated: false })).toBe("0.023 m³/m³");
    expect(label({ value: 0.2536, aggregated: true })).toBe("avg 0.254 m³/m³");
    expect(label({ value: 0.00001 })).toBe("<0.001 m³/m³");
    expect(label({ value: -0.00001 })).toBe(">-0.001 m³/m³");
  });

  it("keeps labels readable with zoom and collision placement enabled", () => {
    expect(layer.source).toBe("served");
    expect(layer.layout).toMatchObject({ "text-allow-overlap": false, "text-ignore-placement": false });
    const compiled = createExpression(layer.layout?.["text-size"]);
    if (compiled.result === "error") throw new Error(JSON.stringify(compiled.value));
    expect([2, 8, 13].map(zoom => compiled.value.evaluate({ zoom }))).toEqual([11, 12, 14]);
  });
});
