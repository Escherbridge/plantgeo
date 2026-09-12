import type { ExpressionSpecification, SymbolLayerSpecification } from "maplibre-gl";

interface MeasuredValueLabelOptions {
  id: string;
  source: string;
  unit: string;
  fractionDigits: number;
  opacity: number;
}

/** Labels served scalar cells without deriving geometry or values; see AGENTS.md. */
export function measuredValueLabelLayer({
  id,
  source,
  unit,
  fractionDigits,
  opacity,
}: MeasuredValueLabelOptions): SymbolLayerSpecification {
  const value: ExpressionSpecification = ["get", "value"];
  const finiteValue: ExpressionSpecification = [
    "case",
    ["==", ["typeof", value], "number"],
    ["all", [">=", value, -Number.MAX_VALUE], ["<=", value, Number.MAX_VALUE]],
    false,
  ];
  const precision = 10 ** -fractionDigits;
  const formatted: ExpressionSpecification = [
    "case",
    ["all", [">", value, 0], ["<", value, precision]],
    `<${precision}`,
    ["all", ["<", value, 0], [">", value, -precision]],
    `>-${precision}`,
    ["number-format", value, { locale: "en-US", "min-fraction-digits": fractionDigits, "max-fraction-digits": fractionDigits }],
  ];
  return {
    id,
    type: "symbol",
    source,
    filter: finiteValue,
    layout: {
      "symbol-placement": "point",
      "text-field": [
        "case", finiteValue,
        ["concat", ["case", ["==", ["get", "aggregated"], true], "avg ", ""], formatted, ` ${unit}`],
        "",
      ],
      "text-font": ["Noto Sans Regular"],
      "text-size": ["interpolate", ["linear"], ["zoom"], 2, 11, 8, 12, 13, 14],
      "text-max-width": 12,
      "text-padding": 6,
      "text-allow-overlap": false,
      "text-ignore-placement": false,
      "text-rotation-alignment": "viewport",
      "text-pitch-alignment": "viewport",
    },
    paint: {
      "text-color": "#18181b",
      "text-halo-color": "#ffffff",
      "text-halo-width": 1.5,
      "text-opacity": opacity,
    },
  };
}
