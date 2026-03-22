"use client";

import { ArcLayer as DeckArcLayer } from "@deck.gl/layers";
import type { Color } from "@deck.gl/core";
import { DECK_DEFAULT_PROPS } from "@/lib/map/deck-config";

export interface ArcDatum {
  source: [number, number];
  target: [number, number];
  value?: number;
  sourceColor?: Color;
  targetColor?: Color;
}

interface ArcLayerProps {
  id?: string;
  data: ArcDatum[];
  widthScale?: number;
  widthMinPixels?: number;
  greatCircle?: boolean;
  onHover?: (info: { object?: ArcDatum; x: number; y: number }) => void;
  onClick?: (info: { object?: ArcDatum }) => void;
}

export function createArcLayer({
  id = "arc",
  data,
  widthScale = 2,
  widthMinPixels = 1,
  greatCircle = false,
  onHover,
  onClick,
}: ArcLayerProps) {
  return new DeckArcLayer<ArcDatum>({
    ...DECK_DEFAULT_PROPS,
    id,
    data,
    getSourcePosition: (d) => d.source,
    getTargetPosition: (d) => d.target,
    getSourceColor: (d): Color => d.sourceColor ?? [255, 140, 0, 200],
    getTargetColor: (d): Color => d.targetColor ?? [255, 0, 0, 200],
    getWidth: (d) => d.value ?? 1,
    widthScale,
    widthMinPixels,
    greatCircle,
    onHover,
    onClick,
  });
}
