"use client";

import { ScatterplotLayer } from "@deck.gl/layers";
import type { Color } from "@deck.gl/core";
import { DECK_DEFAULT_PROPS } from "@/lib/map/deck-config";

export interface HeatPoint {
  coordinates: [number, number];
  weight?: number;
}

interface HeatmapLayerProps {
  id?: string;
  data: HeatPoint[];
  radiusPixels?: number;
  onHover?: (info: { object?: HeatPoint; x: number; y: number }) => void;
  onClick?: (info: { object?: HeatPoint }) => void;
}

function weightToColor(weight: number): Color {
  const t = Math.min(1, Math.max(0, weight));
  if (t < 0.25) return [0, 0, 255, Math.round(t * 4 * 180)];
  if (t < 0.5) return [0, 128 + Math.round((t - 0.25) * 4 * 127), 255, 200];
  if (t < 0.75) return [255, 255 - Math.round((t - 0.5) * 4 * 255), 0, 220];
  return [255, 0, 0, 240];
}

export function createHeatmapLayer({
  id = "heatmap",
  data,
  radiusPixels = 30,
  onHover,
  onClick,
}: HeatmapLayerProps) {
  return new ScatterplotLayer<HeatPoint>({
    ...DECK_DEFAULT_PROPS,
    id,
    data,
    getPosition: (d) => d.coordinates,
    getRadius: radiusPixels,
    radiusUnits: "pixels",
    getFillColor: (d) => weightToColor(d.weight ?? 0.5),
    stroked: false,
    opacity: 0.6,
    onHover,
    onClick,
  });
}
