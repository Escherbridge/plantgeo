"use client";

import { ColumnLayer as DeckColumnLayer } from "@deck.gl/layers";
import type { Color } from "@deck.gl/core";
import { DECK_DEFAULT_PROPS, FIRE_COLOR_RANGE } from "@/lib/map/deck-config";

export interface ColumnDatum {
  coordinates: [number, number];
  value: number;
  label?: string;
}

interface ColumnLayerProps {
  id?: string;
  data: ColumnDatum[];
  diskResolution?: number;
  radius?: number;
  elevationScale?: number;
  maxValue?: number;
  onHover?: (info: { object?: ColumnDatum; x: number; y: number }) => void;
  onClick?: (info: { object?: ColumnDatum }) => void;
}

function valueToColor(t: number): Color {
  const idx = Math.min(FIRE_COLOR_RANGE.length - 1, Math.floor(t * FIRE_COLOR_RANGE.length));
  return FIRE_COLOR_RANGE[idx];
}

export function createColumnLayer({
  id = "column",
  data,
  diskResolution = 6,
  radius = 100,
  elevationScale = 1,
  maxValue,
  onHover,
  onClick,
}: ColumnLayerProps) {
  const max = maxValue ?? Math.max(...data.map((d) => d.value), 1);

  return new DeckColumnLayer<ColumnDatum>({
    ...DECK_DEFAULT_PROPS,
    id,
    data,
    getPosition: (d) => d.coordinates,
    getElevation: (d) => d.value * elevationScale,
    getFillColor: (d): Color => valueToColor(d.value / max),
    diskResolution,
    radius,
    extruded: true,
    pickable: true,
    opacity: 0.85,
    onHover,
    onClick,
  });
}
