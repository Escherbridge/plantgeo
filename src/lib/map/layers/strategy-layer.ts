import type { LayerSpecification, SourceSpecification } from "maplibre-gl";

export const STRATEGY_COLOR_RAMPS = {
  regenerative_ag: "#10b981", // Emerald
  agroforestry: "#15803d", // Forest Green
  biochar: "#d97706", // Amber / Bronze
  wildfire_buffer: "#ef4444", // Coral / Red
  water_conservation: "#0284c7", // Azure
  default: "#8b5cf6", // Violet fallback
} as const;

export const strategySource: SourceSpecification = {
  type: "vector",
  tiles: ["/api/tiles/strategy-recommendations/{z}/{x}/{y}.pbf"],
  minzoom: 0,
  maxzoom: 22,
};

export const strategyFillLayer: LayerSpecification = {
  id: "strategy-recommendations-fill",
  type: "fill",
  source: "strategy-recommendations",
  "source-layer": "strategy-recommendations",
  paint: {
    "fill-color": [
      "match",
      ["get", "strategy_category"],
      "regenerative_ag",
      STRATEGY_COLOR_RAMPS.regenerative_ag,
      "agroforestry",
      STRATEGY_COLOR_RAMPS.agroforestry,
      "biochar",
      STRATEGY_COLOR_RAMPS.biochar,
      "wildfire_buffer",
      STRATEGY_COLOR_RAMPS.wildfire_buffer,
      "water_conservation",
      STRATEGY_COLOR_RAMPS.water_conservation,
      STRATEGY_COLOR_RAMPS.default,
    ],
    "fill-opacity": [
      "interpolate",
      ["linear"],
      ["get", "suitability_score"],
      0.0,
      0.1,
      0.5,
      0.35,
      1.0,
      0.65,
    ],
  },
};

export const strategyOutlineLayer: LayerSpecification = {
  id: "strategy-recommendations-outline",
  type: "line",
  source: "strategy-recommendations",
  "source-layer": "strategy-recommendations",
  paint: {
    "line-color": [
      "match",
      ["get", "strategy_category"],
      "regenerative_ag",
      STRATEGY_COLOR_RAMPS.regenerative_ag,
      "agroforestry",
      STRATEGY_COLOR_RAMPS.agroforestry,
      "biochar",
      STRATEGY_COLOR_RAMPS.biochar,
      "wildfire_buffer",
      STRATEGY_COLOR_RAMPS.wildfire_buffer,
      "water_conservation",
      STRATEGY_COLOR_RAMPS.water_conservation,
      STRATEGY_COLOR_RAMPS.default,
    ],
    "line-width": 1.5,
    "line-opacity": 0.8,
  },
};
