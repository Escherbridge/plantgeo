import type { Color } from "@deck.gl/core";

export const DECK_DEFAULT_PROPS = {
  pickable: true,
  autoHighlight: true,
  highlightColor: [255, 255, 255, 60] as number[],
};

export const FIRE_COLOR_RANGE: Color[] = [
  [255, 255, 178],
  [254, 204, 92],
  [253, 141, 60],
  [240, 59, 32],
  [189, 0, 38],
];

export const HEAT_COLOR_RANGE: Color[] = [
  [0, 0, 255, 0],
  [0, 128, 255, 128],
  [0, 255, 128, 192],
  [255, 255, 0, 224],
  [255, 0, 0, 255],
];

export const CATEGORY_COLORS: Record<string, Color> = {
  fire: [255, 69, 0],
  sensor: [0, 200, 255],
  poi: [255, 215, 0],
  default: [100, 200, 100],
};

export const DECK_CANVAS_PARAMS = {
  antialias: true,
};
