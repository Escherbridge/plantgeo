"use client";

import { ScatterplotLayer } from "@deck.gl/layers";
import type { Color } from "@deck.gl/core";
import { DECK_DEFAULT_PROPS, CATEGORY_COLORS } from "@/lib/map/deck-config";

interface ScatterPoint {
  coordinates: [number, number];
  size?: number;
  category?: string;
  properties?: Record<string, unknown>;
}

interface ScatterLayerProps {
  id?: string;
  data: ScatterPoint[];
  radiusScale?: number;
  radiusMinPixels?: number;
  radiusMaxPixels?: number;
  onHover?: (info: { object?: ScatterPoint; x: number; y: number }) => void;
  onClick?: (info: { object?: ScatterPoint }) => void;
}

export function createScatterLayer({
  id = "scatter",
  data,
  radiusScale = 10,
  radiusMinPixels = 4,
  radiusMaxPixels = 30,
  onHover,
  onClick,
}: ScatterLayerProps) {
  return new ScatterplotLayer<ScatterPoint>({
    ...DECK_DEFAULT_PROPS,
    id,
    data,
    getPosition: (d) => d.coordinates,
    getRadius: (d) => d.size ?? 1,
    getFillColor: (d): Color =>
      CATEGORY_COLORS[d.category ?? "default"] ?? CATEGORY_COLORS.default,
    getLineColor: [255, 255, 255, 80],
    lineWidthMinPixels: 1,
    stroked: true,
    radiusScale,
    radiusMinPixels,
    radiusMaxPixels,
    onHover,
    onClick,
  });
}
