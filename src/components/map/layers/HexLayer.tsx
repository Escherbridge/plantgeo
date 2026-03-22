"use client";

import { GridCellLayer } from "@deck.gl/layers";
import type { Color } from "@deck.gl/core";
import { DECK_DEFAULT_PROPS, FIRE_COLOR_RANGE } from "@/lib/map/deck-config";

export interface HexBin {
  coordinates: [number, number];
  count: number;
  value?: number;
}

interface HexLayerProps {
  id?: string;
  data: HexBin[];
  cellSize?: number;
  elevationScale?: number;
  maxValue?: number;
  onHover?: (info: { object?: HexBin; x: number; y: number }) => void;
  onClick?: (info: { object?: HexBin }) => void;
}

function valueToColor(t: number, colorRange: Color[]): Color {
  const idx = Math.min(colorRange.length - 1, Math.floor(t * colorRange.length));
  return colorRange[idx];
}

export function createHexLayer({
  id = "hex",
  data,
  cellSize = 500,
  elevationScale = 100,
  maxValue,
  onHover,
  onClick,
}: HexLayerProps) {
  const max = maxValue ?? Math.max(...data.map((d) => d.value ?? d.count), 1);

  return new GridCellLayer<HexBin>({
    ...DECK_DEFAULT_PROPS,
    id,
    data,
    getPosition: (d) => d.coordinates,
    cellSize,
    getElevation: (d) => ((d.value ?? d.count) / max) * elevationScale,
    getFillColor: (d): Color => valueToColor((d.value ?? d.count) / max, FIRE_COLOR_RANGE),
    extruded: true,
    elevationScale: 1,
    opacity: 0.8,
    onHover,
    onClick,
  });
}
