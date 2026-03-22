"use client";

import { PathLayer as DeckPathLayer } from "@deck.gl/layers";
import type { Color } from "@deck.gl/core";
import { DECK_DEFAULT_PROPS } from "@/lib/map/deck-config";

export interface PathDatum {
  path: [number, number][];
  color?: Color;
  width?: number;
  name?: string;
}

interface PathLayerProps {
  id?: string;
  data: PathDatum[];
  widthScale?: number;
  widthMinPixels?: number;
  capRounded?: boolean;
  jointRounded?: boolean;
  onHover?: (info: { object?: PathDatum; x: number; y: number }) => void;
  onClick?: (info: { object?: PathDatum }) => void;
}

export function createPathLayer({
  id = "path",
  data,
  widthScale = 2,
  widthMinPixels = 2,
  capRounded = true,
  jointRounded = true,
  onHover,
  onClick,
}: PathLayerProps) {
  return new DeckPathLayer<PathDatum>({
    ...DECK_DEFAULT_PROPS,
    id,
    data,
    getPath: (d) => d.path,
    getColor: (d): Color => d.color ?? [0, 200, 255, 200],
    getWidth: (d) => d.width ?? 3,
    widthScale,
    widthMinPixels,
    capRounded,
    jointRounded,
    onHover,
    onClick,
  });
}
